#!/usr/bin/env bash
#
# run_fireants.sh — GPU-accelerated registration with FireANTs.
#
# Mirrors run_ants.sh: converts TIFF inputs to NIfTI with volume_io, runs the
# FireANTs affine + greedy driver (in the isolated `fireants` conda env, on GPU),
# warps the CCF template (bilinear) and annotation (nearest), validates, and
# converts the results back to TIFF with the standard output names.
#
set -euo pipefail

source "$(dirname "$0")/config.sh"
check_common_inputs

VOLUME_IO="$PROJECT_ROOT/registration_benchmark/volume_io.py"
FIREANTS_DRIVER="$PROJECT_ROOT/registration_benchmark/fireants_register.py"
require_file "$VOLUME_IO" "validated volume conversion helper"
require_file "$FIREANTS_DRIVER" "FireANTs registration driver"

# FireANTs runs in its own env (its torch build differs from brainreg's).
FIREANTS_PY="${FIREANTS_PY:-$HOME/miniforge3/envs/fireants/bin/python}"
require_executable "$FIREANTS_PY" "fireants env python (set FIREANTS_PY)"

METHOD_OUT="$OUTPUT_ROOT/fireants"
mkdir -p "$METHOD_OUT"

if [ -n "${FIREANTS_TMP_ROOT:-}" ]; then
	TMP_ROOT="$FIREANTS_TMP_ROOT"
else
	mkdir -p "$SCRATCH_BASE"
	TMP_ROOT="$(mktemp -d "$SCRATCH_BASE/fireants_registration.XXXXXX")"
fi
WORK_DIR="$TMP_ROOT/work"
mkdir -p "$WORK_DIR"
if [ "${KEEP_FIREANTS_TMP:-0}" != "1" ]; then
	trap 'rm -rf "$TMP_ROOT"' EXIT
fi

FIXED_NII="$WORK_DIR/fixed_sample.nii.gz"
TEMPLATE_NII="$WORK_DIR/ccfv3_template.nii.gz"
ATLAS_NII="$WORK_DIR/ccfv3_annotation.nii.gz"
REG_TEMPLATE_NII="$WORK_DIR/registered_template.nii.gz"
ANNOTATION_NII="$WORK_DIR/annotation.nii.gz"

echo "Converting TIFF inputs to NIfTI for FireANTs..."
python "$VOLUME_IO" tiff-to-nifti "$FIXED_IMAGE" "$FIXED_NII" --kind intensity
python "$VOLUME_IO" tiff-to-nifti "$TEMPLATE_IMAGE" "$TEMPLATE_NII" --kind intensity
python "$VOLUME_IO" tiff-to-nifti "$ATLAS_IMAGE" "$ATLAS_NII" --kind labels

echo "Running FireANTs GPU registration (template -> sample)..."
{
	"$FIREANTS_PY" "$FIREANTS_DRIVER" \
		--fixed "$FIXED_NII" \
		--template "$TEMPLATE_NII" \
		--annotation "$ATLAS_NII" \
		--out-template "$REG_TEMPLATE_NII" \
		--out-annotation "$ANNOTATION_NII" \
		--scales "${FIREANTS_SCALES:-4,2,1}" \
		--affine-iters "${FIREANTS_AFFINE_ITERS:-200,100,50}" \
		--greedy-iters "${FIREANTS_GREEDY_ITERS:-200,100,50}"
} 2>&1 | tee "$METHOD_OUT/fireants_registration.log"

require_file "$REG_TEMPLATE_NII" "FireANTs registered template"
require_file "$ANNOTATION_NII" "FireANTs warped annotation"

echo "Validating and converting FireANTs outputs to TIFF..."
python "$VOLUME_IO" inspect "$REG_TEMPLATE_NII" --require-nonzero
python "$VOLUME_IO" inspect "$ANNOTATION_NII" --require-nonzero
python "$VOLUME_IO" nifti-to-tiff "$REG_TEMPLATE_NII" "$METHOD_OUT/${SAMPLE_STEM}_registered_CCFv3_template.tif" --kind intensity
python "$VOLUME_IO" nifti-to-tiff "$ANNOTATION_NII" "$METHOD_OUT/${SAMPLE_STEM}_annotation.tif" --kind labels

echo "FireANTs outputs written to:"
echo "  $METHOD_OUT"
