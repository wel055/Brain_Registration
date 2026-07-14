#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/config.sh"
check_common_inputs

EMLDDMM_HOME="${EMLDDMM_HOME:-$PROJECT_ROOT/external/emlddmm}"
GRAPH_SCRIPT="$EMLDDMM_HOME/transformation_graph_v01.py"
VOLUME_IO="$PROJECT_ROOT/registration_benchmark/volume_io.py"
require_file "$GRAPH_SCRIPT" "emlddmm transformation graph script"
require_file "$VOLUME_IO" "validated volume conversion helper"

METHOD_OUT="$OUTPUT_ROOT/emlddmm"
WORK_DIR="$METHOD_OUT/work"
GRAPH_OUT="$METHOD_OUT/graph_output"
MPL_CACHE="$METHOD_OUT/mplconfig"
mkdir -p "$WORK_DIR" "$GRAPH_OUT" "$MPL_CACHE"
export PYTHONPATH="$EMLDDMM_HOME:${PYTHONPATH:-}"
export MPLCONFIGDIR="$MPL_CACHE"

FIXED_VTK="$WORK_DIR/fixed_sample.vtk"
TEMPLATE_VTK="$WORK_DIR/ccfv3_template.vtk"
ATLAS_VTK="$WORK_DIR/ccfv3_annotation.vtk"
CONFIG_JSON="$METHOD_OUT/emlddmm_config.json"
GRAPH_JSON="$METHOD_OUT/emlddmm_graph.json"

echo "Converting TIFF inputs to VTK for emlddmm..."
python "$VOLUME_IO" tiff-to-vtk "$FIXED_IMAGE" "$FIXED_VTK" --kind intensity
python "$VOLUME_IO" tiff-to-vtk "$TEMPLATE_IMAGE" "$TEMPLATE_VTK" --kind intensity
python "$VOLUME_IO" tiff-to-vtk "$ATLAS_IMAGE" "$ATLAS_VTK" --kind labels

cat > "$CONFIG_JSON" <<JSON
{
  "downI": [[16, 16, 16], [8, 8, 8]],
  "downJ": [[16, 16, 16], [8, 8, 8]],
  "n_iter": ${EMLDDMM_N_ITER:-[30, 15]},
  "v_start": [15, 5],
  "eA": [10.0, 5.0],
  "ev": [1.0, 0.5],
  "auto_stepsize_A": 5,
  "auto_stepsize_v": 5,
  "a": null,
  "sigmaM": 1.0,
  "sigmaB": 2.0,
  "epsilon": 0.05,
  "nt": 3,
  "n_draw": 0,
  "v_res_factor": 4.0
}
JSON

cat > "$GRAPH_JSON" <<JSON
{
  "space_image_path": [
    ["sample", "488", "$FIXED_VTK"],
    ["ccf", "template", "$TEMPLATE_VTK"],
    ["ccf", "annotation", "$ATLAS_VTK"]
  ],
  "registrations": [
    [["ccf", "template"], ["sample", "488"]]
  ],
  "configs": [
    "$CONFIG_JSON"
  ],
  "transform_all": true,
  "output": "$GRAPH_OUT"
}
JSON

INPUT_FLAG="--in"
if python "$GRAPH_SCRIPT" --help 2>&1 | grep -q -- "--infile"; then
	INPUT_FLAG="--infile"
fi

echo "Running emlddmm transformation graph..."
{
	python "$GRAPH_SCRIPT" "$INPUT_FLAG" "$GRAPH_JSON"
} 2>&1 | tee "$METHOD_OUT/emlddmm.log"

find "$METHOD_OUT" -type f | sort > "$METHOD_OUT/output_files.txt"

AFFINE_TRANSFORM="$GRAPH_OUT/ccf/sample_to_ccf/transforms/A.txt"
REGISTERED_TEMPLATE_VTK="$GRAPH_OUT/sample/ccf_to_sample/images/ccf_template_to_sample.vtk"
ANNOTATION_VTK="$GRAPH_OUT/sample/ccf_to_sample/images/ccf_annotation_to_sample.vtk"
require_file "$AFFINE_TRANSFORM" "emlddmm affine transform"
require_file "$REGISTERED_TEMPLATE_VTK" "emlddmm registered template"
require_file "$ANNOTATION_VTK" "emlddmm registered annotation"
python "$VOLUME_IO" validate-matrix "$AFFINE_TRANSFORM"
python "$VOLUME_IO" inspect "$REGISTERED_TEMPLATE_VTK" --require-nonzero
python "$VOLUME_IO" inspect "$ANNOTATION_VTK" --require-nonzero
python "$VOLUME_IO" vtk-to-tiff "$REGISTERED_TEMPLATE_VTK" "$METHOD_OUT/${SAMPLE_STEM}_registered_CCFv3_template.tif" --kind intensity
python "$VOLUME_IO" vtk-to-tiff "$ANNOTATION_VTK" "$METHOD_OUT/${SAMPLE_STEM}_annotation.tif" --kind labels

cat <<MSG
emlddmm finished and passed finite/nonzero output validation.

Outputs and generated configs:
  $METHOD_OUT

Because emlddmm output names depend on the graph runner version, inspect:
  $METHOD_OUT/output_files.txt

Then pass the warped template and warped annotation paths to:
  registration_benchmark/evaluate_registration.py --method emlddmm --registered-template ... --annotation ...
MSG
