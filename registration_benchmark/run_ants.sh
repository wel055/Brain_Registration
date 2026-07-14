#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/config.sh"
check_common_inputs

VOLUME_IO="$PROJECT_ROOT/registration_benchmark/volume_io.py"
require_file "$VOLUME_IO" "validated volume conversion helper"

ANTS_REG="${ANTS_REG:-$(command -v antsRegistrationSyNQuick.sh || true)}"
ANTS_APPLY="${ANTS_APPLY:-$(command -v antsApplyTransforms || true)}"
require_executable "$ANTS_REG" "ANTs antsRegistrationSyNQuick.sh"
require_executable "$ANTS_APPLY" "ANTs antsApplyTransforms"

METHOD_OUT="$OUTPUT_ROOT/ants"
mkdir -p "$METHOD_OUT"

if [ -n "${ANTS_TMP_ROOT:-}" ]; then
	TMP_ROOT="$ANTS_TMP_ROOT"
else
	mkdir -p "$SCRATCH_BASE"
	TMP_ROOT="$(mktemp -d "$SCRATCH_BASE/ants_registration.XXXXXX")"
fi
WORK_DIR="$TMP_ROOT/work"
PREFIX="$TMP_ROOT/ants_"
mkdir -p "$WORK_DIR"
if [ "${KEEP_ANTS_TMP:-0}" != "1" ]; then
	trap 'rm -rf "$TMP_ROOT"' EXIT
fi

FIXED_NII="$WORK_DIR/fixed_sample.nii.gz"
TEMPLATE_NII="$WORK_DIR/ccfv3_template.nii.gz"
ATLAS_NII="$WORK_DIR/ccfv3_annotation.nii.gz"

echo "Converting TIFF inputs to NIfTI for ANTs..."
python "$VOLUME_IO" tiff-to-nifti "$FIXED_IMAGE" "$FIXED_NII" --kind intensity
python "$VOLUME_IO" tiff-to-nifti "$TEMPLATE_IMAGE" "$TEMPLATE_NII" --kind intensity
python "$VOLUME_IO" tiff-to-nifti "$ATLAS_IMAGE" "$ATLAS_NII" --kind labels

echo "Running ANTs template-to-sample registration..."
{
	"$ANTS_REG" \
		-d 3 \
		-f "$FIXED_NII" \
		-m "$TEMPLATE_NII" \
		-o "$PREFIX" \
		-n "$THREADS" \
		-p "${ANTS_PRECISION:-f}" \
		-t s
} 2>&1 | tee "$METHOD_OUT/ants_registration.log"

REGISTERED_TEMPLATE_NII="${PREFIX}Warped.nii.gz"
ANNOTATION_NII="$TMP_ROOT/${SAMPLE_STEM}_annotation.nii.gz"
require_file "$REGISTERED_TEMPLATE_NII" "ANTs registered template"
require_file "${PREFIX}0GenericAffine.mat" "ANTs affine transform"
require_file "${PREFIX}1Warp.nii.gz" "ANTs deformation field"
python "$VOLUME_IO" inspect "$REGISTERED_TEMPLATE_NII" --require-nonzero
python "$VOLUME_IO" inspect "${PREFIX}1Warp.nii.gz"

echo "Applying ANTs transforms to the atlas labels..."
{
	"$ANTS_APPLY" \
		-d 3 \
		-i "$ATLAS_NII" \
		-r "$FIXED_NII" \
		-o "$ANNOTATION_NII" \
		-n NearestNeighbor \
		-t "${PREFIX}1Warp.nii.gz" \
		-t "${PREFIX}0GenericAffine.mat"
} 2>&1 | tee "$METHOD_OUT/ants_apply_labels.log"

echo "Converting ANTs outputs to TIFF..."
python "$VOLUME_IO" inspect "$ANNOTATION_NII" --require-nonzero
python "$VOLUME_IO" nifti-to-tiff "$REGISTERED_TEMPLATE_NII" "$METHOD_OUT/${SAMPLE_STEM}_registered_CCFv3_template.tif" --kind intensity
python "$VOLUME_IO" nifti-to-tiff "$ANNOTATION_NII" "$METHOD_OUT/${SAMPLE_STEM}_annotation.tif" --kind labels
cp -p "${PREFIX}"* "$METHOD_OUT/" 2>/dev/null || true
cp -p "$ANNOTATION_NII" "$METHOD_OUT/" 2>/dev/null || true

echo "ANTs outputs written to:"
echo "  $METHOD_OUT"
