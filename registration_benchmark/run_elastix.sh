#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/config.sh"
check_common_inputs
require_file "$ELASTIX_SCRIPT" "elastix script"

METHOD_OUT="$OUTPUT_ROOT/elastix"
mkdir -p "$METHOD_OUT"

REGISTERED_TEMPLATE="$PROJECT_ROOT/transformixOutput/${SAMPLE_STEM}._registered_CCFv3_template.tif"
ANNOTATION="$PROJECT_ROOT/transformixOutput/${SAMPLE_STEM}._annotation.tif"

if [ "${FORCE_RUN:-0}" = "1" ] || [ ! -f "$REGISTERED_TEMPLATE" ] || [ ! -f "$ANNOTATION" ]; then
	"$ELASTIX_SCRIPT" "$FIXED_IMAGE"
fi

cp -p "$REGISTERED_TEMPLATE" "$METHOD_OUT/${SAMPLE_STEM}_registered_CCFv3_template.tif"
cp -p "$ANNOTATION" "$METHOD_OUT/${SAMPLE_STEM}_annotation.tif"
cp -p "$PROJECT_ROOT/elastixOutput/elastix.log" "$METHOD_OUT/elastix.log" 2>/dev/null || true
cp -p "$PROJECT_ROOT/transformixOutput/transformix.log" "$METHOD_OUT/transformix.log" 2>/dev/null || true

echo "Elastix outputs copied to:"
echo "  $METHOD_OUT"
