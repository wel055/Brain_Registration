#!/usr/bin/env bash
#
# run_pipeline.sh — full brain-registration pipeline for one LSFM sample.
#
# Stages (all on one sample):
#   0. stitch the raw tiled acquisition into a 25 um, CCF-oriented 3D volume
#   1. register CCFv3 -> sample with elastix / ANTs / FireANTs / emlddmm, then evaluate
#   2. build the per-subregion, per-method comparison tables
#
# All outputs for a sample live under  <output-dir>/<sample-name>/ :
#   <out>/<sample>/stitched/<sample>_<channel>_fullres_<tag>.tif  (fixed image)
#   <out>/<sample>/<run-id>/           cleaned results (registration + eval + tables)
#   <out>/<sample>/.scratch/<run-id>/  full intermediates (ANTs tmp, emlddmm work)
#
# Usage:
#   ./run_pipeline.sh /path/to/data/<sample_folder> [options]
#
# The sample folder must contain the raw tiled channel subfolder (e.g.
# Ex_488_Ch1/<X>/<X>_<Y>/<z>.png), like every acquisition in data/.
#
# Options (all have sensible defaults):
#   --output-dir DIR      results root; a <sample> subfolder is made here
#                                                         (default: ./results)
#   --channel NAME        full-res channel subfolder      (default: Ex_488_Ch1)
#   --target-um N         isotropic output voxel size um  (default: 25)
#   --threads N           CPU threads (ANTs + elastix)    (default: 32)
#   --eval-stride N       evaluation downsample stride    (default: 4)
#   --methods LIST        comma list of methods to run    (default: elastix,ants,fireants,emlddmm)
#                         (fireants = GPU-accelerated ANTs; runs in its own env)
#   --emlddmm-iters JSON  emlddmm iterations per scale    (default: [30, 15])
#   --fireants-iters CSV  fireants iters per scale        (default: 200,100,50)
#   --orient MODE         ccf | none (axis reorient)      (default: ccf)
#   --flip AXES           axes to flip, CCF order 0,1,2   (default: none)
#   --report-level N      ontology aggregation depth      (default: 5)
#   --run-id ID           run identifier                  (default: run_<timestamp>)
#   --conda-env NAME      conda env to activate           (default: brainreg)
#   --elastix-dir DIR     elastix install prefix          (default: ~/software/elastix-5.3.1)
#   --skip-stitch         reuse an existing stitched volume, skip stage 0
#   --no-report           skip stage 2 (subregion tables)
#   -h | --help           show this help and exit
#
# Example:
#   ./run_pipeline.sh data/20260220_11_25_58_AZ4_DB6_P60_GS_F1_A_Raw_Transferred \
#       --output-dir /data2/Wenxi/results --threads 48 --methods ants,elastix
#
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- defaults ----
OUTPUT_DIR="$PROJECT_ROOT/results"
CHANNEL="Ex_488_Ch1"
TARGET_UM="25"
THREADS="32"
EVAL_STRIDE="4"
METHODS="elastix,ants,fireants,emlddmm"
EMLDDMM_ITERS="[30, 15]"
FIREANTS_ITERS="200,100,50"
ORIENT="ccf"
FLIP=""
REPORT_LEVEL="5"
RUN_ID=""
CONDA_ENV="brainreg"
ELASTIX_DIR="$HOME/software/elastix-5.3.1"
SKIP_STITCH=0
RUN_REPORT=1

# print the leading comment block as help
usage() { awk 'NR>1{ if(/^#/){sub(/^# ?/,"");print} else exit }' "${BASH_SOURCE[0]}"; exit "${1:-0}"; }

# ---- parse args ----
[ $# -ge 1 ] || { echo "Error: sample folder path is required." >&2; usage 1; }
SAMPLE_PATH="$1"; shift
case "$SAMPLE_PATH" in -h|--help) usage 0;; esac

while [ $# -gt 0 ]; do
	case "$1" in
		--output-dir)    OUTPUT_DIR="$2"; shift 2;;
		--channel)       CHANNEL="$2"; shift 2;;
		--target-um)     TARGET_UM="$2"; shift 2;;
		--threads)       THREADS="$2"; shift 2;;
		--eval-stride)   EVAL_STRIDE="$2"; shift 2;;
		--methods)       METHODS="$2"; shift 2;;
		--emlddmm-iters) EMLDDMM_ITERS="$2"; shift 2;;
		--fireants-iters) FIREANTS_ITERS="$2"; shift 2;;
		--orient)        ORIENT="$2"; shift 2;;
		--flip)          FLIP="$2"; shift 2;;
		--report-level)  REPORT_LEVEL="$2"; shift 2;;
		--run-id)        RUN_ID="$2"; shift 2;;
		--conda-env)     CONDA_ENV="$2"; shift 2;;
		--elastix-dir)   ELASTIX_DIR="$2"; shift 2;;
		--skip-stitch)   SKIP_STITCH=1; shift;;
		--no-report)     RUN_REPORT=0; shift;;
		-h|--help)       usage 0;;
		*) echo "Unknown option: $1" >&2; usage 1;;
	esac
done

# ---- resolve sample path -> data-root + sample name ----
SAMPLE_PATH="${SAMPLE_PATH%/}"
[ -d "$SAMPLE_PATH" ] || { echo "Error: sample folder not found: $SAMPLE_PATH" >&2; exit 1; }
DATA_ROOT="$(cd "$(dirname "$SAMPLE_PATH")" && pwd)"
SAMPLE="$(basename "$SAMPLE_PATH")"
[ -d "$SAMPLE_PATH/$CHANNEL" ] || {
	echo "Error: channel '$CHANNEL' not found under $SAMPLE_PATH" >&2; exit 1; }

[ -n "$RUN_ID" ] || RUN_ID="run_$(date +%Y%m%d_%H%M%S)"

# ---- environment ----
if [ -f "$HOME/miniforge3/etc/profile.d/conda.sh" ]; then
	# conda's activate/deactivate hooks reference unset vars; relax `set -u` here
	set +u
	# shellcheck disable=SC1091
	source "$HOME/miniforge3/etc/profile.d/conda.sh"
	conda activate "$CONDA_ENV"
	set -u
fi
export PATH="$ELASTIX_DIR/bin:$PATH"
export LD_LIBRARY_PATH="$ELASTIX_DIR/lib:${LD_LIBRARY_PATH:-}"

# ---- output layout: everything for this sample under <output-dir>/<sample>/ ----
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"          # absolutise
SAMPLE_OUT="$OUTPUT_DIR/$SAMPLE"
STITCH_DIR="$SAMPLE_OUT/stitched"
mkdir -p "$STITCH_DIR"

TAG="$(printf '%.0f' "$TARGET_UM")um"
STITCHED="$STITCH_DIR/${SAMPLE}_${CHANNEL}_fullres_${TAG}.tif"

# run_all_available.sh writes <SCRATCH_BASE>/<RUN_ID> (working, full intermediates)
# then copies a cleaned tree to <FINAL_BASE>/<RUN_ID>. Root both at the sample dir.
export SCRATCH_BASE="$SAMPLE_OUT/.scratch"
export FINAL_BASE="$SAMPLE_OUT"
SCRATCH_RUN="$SCRATCH_BASE/$RUN_ID"
FINAL_RUN="$FINAL_BASE/$RUN_ID"

# methods csv -> INCLUDE_* flags
has_method() { case ",$METHODS," in *",$1,"*) return 0;; *) return 1;; esac; }
INCLUDE_ELASTIX=$(has_method elastix && echo 1 || echo 0)
INCLUDE_ANTS=$(has_method ants && echo 1 || echo 0)
INCLUDE_FIREANTS=$(has_method fireants && echo 1 || echo 0)
INCLUDE_EMLDDMM=$(has_method emlddmm && echo 1 || echo 0)

echo "=================================================================="
echo " Brain registration pipeline"
echo "   sample      : $SAMPLE   (data root: $DATA_ROOT)"
echo "   channel     : $CHANNEL @ ${TARGET_UM} um  (orient=$ORIENT flip='${FLIP:-none}')"
echo "   methods     : $METHODS   threads=$THREADS  eval-stride=$EVAL_STRIDE"
echo "   emlddmm iter: $EMLDDMM_ITERS"
echo "   run id      : $RUN_ID"
echo "   sample out  : $SAMPLE_OUT"
echo "   fixed image : $STITCHED"
echo "=================================================================="

# ---- Stage 0: stitch ----
# NOTE: this stage uses the FULL-RES stitcher (stitch_fullres.py) on purpose.
# WARNING: do NOT point --channel at a MIP channel (e.g. Ex_488_Ch1_MIP). The MIP
# data has only ~15 Z-blocks, so it stitches to a thin SLAB ("the weird-shaped
# brain") that cannot register to the 3D CCF. The old MIP stitcher
# (registration_benchmark/stitch_mip_tiles.py) is broken for registration and is
# deliberately NOT wired into this pipeline.
case "$CHANNEL" in
	*MIP*|*mip*)
		echo "WARNING: channel '$CHANNEL' looks like a MIP channel — this produces a" >&2
		echo "         thin, non-registerable slab. Use a full-res channel (Ex_488_Ch1)." >&2
		;;
esac
if [ "$SKIP_STITCH" = "1" ] && [ -f "$STITCHED" ]; then
	echo "[stage 0] skip stitch, reusing $STITCHED"
else
	echo "[stage 0] stitching full-res -> ${TAG} volume ..."
	FLIP_ARGS=(); [ -n "$FLIP" ] && FLIP_ARGS=(--flip "$FLIP")
	python "$PROJECT_ROOT/registration_benchmark/stitch_fullres.py" \
		--data-root "$DATA_ROOT" --samples "$SAMPLE" \
		--channel "$CHANNEL" --target-um "$TARGET_UM" \
		--orient "$ORIENT" "${FLIP_ARGS[@]}" \
		--out-dir "$STITCH_DIR"
fi
[ -f "$STITCHED" ] || { echo "Error: stitched image missing: $STITCHED" >&2; exit 1; }

# ---- Stage 1: registration benchmark + evaluation ----
echo "[stage 1] registration benchmark ($METHODS) + evaluation ..."
export FIXED_IMAGE="$STITCHED" THREADS ELASTIX_THREADS="$THREADS" TRANSFORMIX_THREADS="$THREADS"
export EVALUATION_STRIDE="$EVAL_STRIDE" RUN_ID
export INCLUDE_ELASTIX INCLUDE_ANTS INCLUDE_EMLDDMM INCLUDE_FIREANTS
export EMLDDMM_N_ITER="$EMLDDMM_ITERS"
export FIREANTS_GREEDY_ITERS="$FIREANTS_ITERS" FIREANTS_AFFINE_ITERS="$FIREANTS_ITERS"
"$PROJECT_ROOT/registration_benchmark/run_all_available.sh"

# ---- Stage 2: per-subregion report (on the cleaned final run) ----
if [ "$RUN_REPORT" = "1" ]; then
	echo "[stage 2] per-subregion comparison tables ..."
	python "$PROJECT_ROOT/registration_benchmark/subregion_report.py" \
		--run-dir "$FINAL_RUN" --fixed "$STITCHED" \
		--stride "$EVAL_STRIDE" --level "$REPORT_LEVEL"
fi

echo "=================================================================="
echo " Done. Outputs under: $SAMPLE_OUT"
echo "   fixed image : $STITCHED"
echo "   results     : $FINAL_RUN"
echo "   metrics     : $FINAL_RUN/evaluation_metrics.csv"
echo "   timings     : $FINAL_RUN/timings.csv"
echo "   subregions  : $FINAL_RUN/subregion_report.html"
echo "   intermediates (ANTs/FireANTs tmp, emlddmm work): $SCRATCH_RUN"
echo "=================================================================="
