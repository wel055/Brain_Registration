#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="${PYTHON:-/Users/wenxili/miniforge3/bin/python}"
DEST="${DEST:-/Volumes/Seagate/scratch/Proj_reg_brain/ssd_20260628_153426}"
FIXED="${FIXED:-$ROOT/CB2_KP2_A1a_A.Ex_488.231.mip4.zlib.tif}"
TEMPLATE="${TEMPLATE:-$ROOT/CCFv3_25um.coronal.tif}"
ATLAS="${ATLAS:-$ROOT/CCFv3_Atlas.ccf_2017.coronal.tif}"
STEPS="${STEPS:-100}"
SHAPE="${SHAPE:-96 48 80}"

mkdir -p "$DEST/learned_registration/models" "$DEST/learned_registration/source_snapshot" "$DEST/registration_runs"
cp "$HERE/learned_registration.py" "$HERE/run_learned_registration.sh" \
  "$HERE/run_benchmark.sh" "$HERE/environment.yml" "$HERE/README.md" \
  "$DEST/learned_registration/source_snapshot/"
cp "$ROOT/registration_benchmark/evaluate_registration.py" \
  "$DEST/learned_registration/source_snapshot/evaluate_registration.py"

for model in voxelmorph transmorph; do
  checkpoint="$DEST/learned_registration/models/$model.pt"
  KMP_DUPLICATE_LIB_OK=TRUE "$PYTHON" "$HERE/learned_registration.py" train \
    --model "$model" --fixed "$FIXED" --template "$TEMPLATE" \
    --output "$checkpoint" --steps "$STEPS" --shape $SHAPE \
    2>&1 | tee "$DEST/learned_registration/${model}_training.log"
  MODEL="$model" PYTHON="$PYTHON" "$HERE/run_learned_registration.sh" \
    "$FIXED" "$checkpoint" "$DEST/registration_runs/$model" \
    2>&1 | tee "$DEST/learned_registration/${model}_inference.log"
done

KMP_DUPLICATE_LIB_OK=TRUE "$PYTHON" "$ROOT/registration_benchmark/evaluate_registration.py" \
  --fixed "$FIXED" --output-root "$DEST/registration_runs" --stride 4 \
  --out-csv "$DEST/evaluation_metrics_learned.csv" \
  --out-json "$DEST/evaluation_metrics_learned.json" \
  --out-subregion-csv "$DEST/subregion_metrics_learned.csv" \
  --out-subregion-json "$DEST/subregion_metrics_learned.json"
