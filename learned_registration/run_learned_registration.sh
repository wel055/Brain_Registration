#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="${PYTHON:-/Users/wenxili/miniforge3/bin/python}"
MODEL="${MODEL:-voxelmorph}"
FIXED="${1:?Usage: $0 FIXED_IMAGE [MODEL_CHECKPOINT] [OUTPUT_DIR]}"
CHECKPOINT="${2:-$HERE/models/${MODEL}.pt}"
OUTPUT_DIR="${3:-$ROOT/registration_runs/$MODEL}"
TEMPLATE="${TEMPLATE:-$ROOT/CCFv3_25um.coronal.tif}"
ATLAS="${ATLAS:-$ROOT/CCFv3_Atlas.ccf_2017.coronal.tif}"

KMP_DUPLICATE_LIB_OK=TRUE "$PYTHON" "$HERE/learned_registration.py" infer \
  --fixed "$FIXED" --template "$TEMPLATE" --atlas "$ATLAS" \
  --checkpoint "$CHECKPOINT" --output-dir "$OUTPUT_DIR" "${@:4}"
