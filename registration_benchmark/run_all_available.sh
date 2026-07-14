#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/config.sh"

RUN_ID="${RUN_ID:-ssd_$(date +%Y%m%d_%H%M%S)}"
SCRATCH_RUN="${SCRATCH_RUN:-$SCRATCH_BASE/$RUN_ID}"
FINAL_RUN="${FINAL_RUN:-$FINAL_BASE/$RUN_ID}"
MIN_SCRATCH_GB="${MIN_SCRATCH_GB:-20}"
EVALUATION_STRIDE="${EVALUATION_STRIDE:-4}"
INCLUDE_ELASTIX="${INCLUDE_ELASTIX:-1}"
INCLUDE_ANTS="${INCLUDE_ANTS:-1}"
INCLUDE_EMLDDMM="${INCLUDE_EMLDDMM:-1}"
INCLUDE_FIREANTS="${INCLUDE_FIREANTS:-0}"
INCLUDE_MBRAINALIGNER="${INCLUDE_MBRAINALIGNER:-0}"

# Server (Linux) migration: no external SSD mount to check for. The writability
# and free-space checks below operate on SCRATCH_BASE (local disk) directly.
mkdir -p "$SCRATCH_BASE"
if [ ! -w "$SCRATCH_BASE" ]; then
	echo "Scratch directory is not writable: $SCRATCH_BASE" >&2
	exit 1
fi

available_kb="$(df -Pk "$SCRATCH_BASE" | awk 'NR == 2 {print $4}')"
required_kb="$((MIN_SCRATCH_GB * 1024 * 1024))"
if [ "$available_kb" -lt "$required_kb" ]; then
	echo "Insufficient scratch space: require ${MIN_SCRATCH_GB} GiB at $SCRATCH_BASE" >&2
	exit 1
fi

if [ -e "$SCRATCH_RUN" ]; then
	echo "Scratch run already exists; choose a new RUN_ID: $SCRATCH_RUN" >&2
	exit 1
fi
if [ -e "$FINAL_RUN" ]; then
	echo "Final run already exists; choose a new RUN_ID: $FINAL_RUN" >&2
	exit 1
fi

OUTPUT_ROOT="$SCRATCH_RUN/registration_runs"
ANTS_TMP_ROOT="$SCRATCH_RUN/ants_tmp"
KEEP_ANTS_TMP=1
FIREANTS_TMP_ROOT="$SCRATCH_RUN/fireants_tmp"
KEEP_FIREANTS_TMP=1
export RUN_ID SCRATCH_RUN FINAL_RUN OUTPUT_ROOT ANTS_TMP_ROOT KEEP_ANTS_TMP
export FIREANTS_TMP_ROOT KEEP_FIREANTS_TMP

mkdir -p "$OUTPUT_ROOT"
printf 'running\n' > "$SCRATCH_RUN/PIPELINE_STATUS.txt"

pipeline_failed() {
	local exit_code=$?
	if [ "$exit_code" -ne 0 ]; then
		printf 'failed (exit %s)\n' "$exit_code" > "$SCRATCH_RUN/PIPELINE_STATUS.txt"
		echo "Pipeline failed. Scratch data retained at: $SCRATCH_RUN" >&2
	fi
}
trap pipeline_failed EXIT

echo "Run ID:       $RUN_ID"
echo "Scratch run:  $SCRATCH_RUN"
echo "Final run:    $FINAL_RUN"
echo "Free scratch: $((available_kb / 1024 / 1024)) GiB"

# Per-method timing. `run_timed` measures each method wrapper's full wall-clock
# (registration + its format conversions), the true per-method cost in the
# pipeline. Written incrementally so a later failure keeps completed timings.
TIMINGS_CSV="$SCRATCH_RUN/timings.csv"
echo "method,seconds" > "$TIMINGS_CSV"
run_timed() {
	local name="$1"; shift
	local start end dur
	start="$(date +%s.%N)"
	"$@"
	end="$(date +%s.%N)"
	dur="$(awk "BEGIN{printf \"%.1f\", $end - $start}")"
	printf '%s,%s\n' "$name" "$dur" >> "$TIMINGS_CSV"
	echo "[timing] $name: ${dur}s (wall-clock, incl. format conversions)"
}

# A benchmark run must actually compute elastix, not reuse a cached result from a
# previous run's shared transformixOutput/ dir. Forcing this also makes the elastix
# timing real and prevents silently reusing a stale result after a re-stitch.
# Override with FORCE_RUN=0 to allow the cache.
export FORCE_RUN="${FORCE_RUN:-1}"

if [ "$INCLUDE_ELASTIX" = "1" ]; then
	run_timed elastix "$HERE/run_elastix.sh"
fi

if [ "$INCLUDE_ANTS" = "1" ]; then
	if ! command -v antsRegistrationSyNQuick.sh >/dev/null 2>&1 || \
		! command -v antsApplyTransforms >/dev/null 2>&1; then
		echo "ANTs commands are not available in PATH." >&2
		exit 1
	fi
	run_timed ants "$HERE/run_ants.sh"
fi

if [ "$INCLUDE_EMLDDMM" = "1" ]; then
	if [ ! -f "$PROJECT_ROOT/external/emlddmm/transformation_graph_v01.py" ]; then
		echo "emlddmm source is not installed." >&2
		exit 1
	fi
	run_timed emlddmm "$HERE/run_emlddmm.sh"
fi

if [ "$INCLUDE_FIREANTS" = "1" ]; then
	FIREANTS_PY="${FIREANTS_PY:-$HOME/miniforge3/envs/fireants/bin/python}"
	if [ ! -x "$FIREANTS_PY" ]; then
		echo "FireANTs env python not found: $FIREANTS_PY (set FIREANTS_PY)." >&2
		exit 1
	fi
	run_timed fireants "$HERE/run_fireants.sh"
fi

if [ "$INCLUDE_MBRAINALIGNER" = "1" ]; then
	"$HERE/run_mbrainaligner.sh"
fi

# consolidate per-method timings into JSON (seconds + minutes)
python - "$TIMINGS_CSV" "$SCRATCH_RUN/timings.json" <<'PY'
import csv, json, sys
rows = {r["method"]: float(r["seconds"]) for r in csv.DictReader(open(sys.argv[1]))}
json.dump({"seconds": rows,
           "minutes": {k: round(v / 60.0, 2) for k, v in rows.items()}},
          open(sys.argv[2], "w"), indent=2)
print("Per-method timings:", ", ".join(f"{k}={v:.1f}s" for k, v in rows.items()))
PY

python "$HERE/evaluate_registration.py" \
	--fixed "$FIXED_IMAGE" \
	--output-root "$OUTPUT_ROOT" \
	--stride "$EVALUATION_STRIDE" \
	--out-csv "$SCRATCH_RUN/evaluation_metrics.csv" \
	--out-json "$SCRATCH_RUN/evaluation_metrics.json" \
	--out-subregion-csv "$SCRATCH_RUN/subregion_metrics.csv" \
	--out-subregion-json "$SCRATCH_RUN/subregion_metrics.json"

mkdir -p "$SCRATCH_RUN/scripts/registration_benchmark"
rsync -a --exclude '__pycache__/' --exclude '._*' "$HERE/" "$SCRATCH_RUN/scripts/registration_benchmark/"
cp -p "$PROJECT_ROOT/new_runElastixTransformix_LSFM.sh" "$SCRATCH_RUN/scripts/"
cp -p "$PROJECT_ROOT/Par0000affine_rmc.txt" "$SCRATCH_RUN/scripts/"
cp -p "$PROJECT_ROOT/Par0000bspline_rmc.txt" "$SCRATCH_RUN/scripts/"

cat > "$SCRATCH_RUN/RUN_NOTES.txt" <<NOTES
Brain registration benchmark
Run ID: $RUN_ID
Completed: $(date)
Scratch root: $SCRATCH_RUN
Final root: $FINAL_RUN
Fixed sample: $FIXED_IMAGE
Template: $TEMPLATE_IMAGE
Annotation atlas: $ATLAS_IMAGE
Evaluation stride: $EVALUATION_STRIDE

All registration and evaluation I/O ran on the external SSD. The final NAS
copy excludes ANTs temporary files, emlddmm input work files, and matplotlib
cache files. The full intermediates remain in the scratch root above.
NOTES

printf 'validated; transferring to NAS\n' > "$SCRATCH_RUN/PIPELINE_STATUS.txt"
mkdir -p "$FINAL_RUN"
rsync -av --partial \
	--exclude 'PIPELINE_STATUS.txt' \
	--exclude '._*' \
	--exclude 'ants_tmp/' \
	--exclude 'fireants_tmp/' \
	--exclude 'registration_runs/emlddmm/work/' \
	--exclude 'registration_runs/emlddmm/mplconfig/' \
	"$SCRATCH_RUN/" "$FINAL_RUN/"

printf 'complete\n' > "$SCRATCH_RUN/PIPELINE_STATUS.txt"
cp -p "$SCRATCH_RUN/PIPELINE_STATUS.txt" "$FINAL_RUN/PIPELINE_STATUS.txt"
trap - EXIT

echo "Pipeline completed successfully."
echo "Scratch data: $SCRATCH_RUN"
echo "Final results: $FINAL_RUN"
