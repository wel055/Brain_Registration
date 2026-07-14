#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/config.sh"

echo "Project root: $PROJECT_ROOT"
echo "Conda prefix: ${CONDA_PREFIX:-not active}"
echo

check_cmd() {
	local label="$1"
	local cmd="$2"
	if command -v "$cmd" >/dev/null 2>&1; then
		echo "OK   $label: $(command -v "$cmd")"
	else
		echo "MISS $label: $cmd not found in PATH"
	fi
}

check_path() {
	local label="$1"
	local path="$2"
	if [ -e "$path" ]; then
		echo "OK   $label: $path"
	else
		echo "MISS $label: $path"
	fi
}

check_path "fixed sample" "$FIXED_IMAGE"
check_path "template" "$TEMPLATE_IMAGE"
check_path "annotation atlas" "$ATLAS_IMAGE"
check_path "c3d" "$C3D_BIN"
echo

check_cmd "ANTs antsRegistrationSyNQuick.sh" antsRegistrationSyNQuick.sh
check_cmd "ANTs antsApplyTransforms" antsApplyTransforms
check_cmd "ANTs antsRegistration" antsRegistration
echo

check_path "emlddmm source" "$PROJECT_ROOT/external/emlddmm"
check_path "emlddmm transformation graph script" "$PROJECT_ROOT/external/emlddmm/transformation_graph_v01.py"
echo

check_path "mBrainAligner source" "$PROJECT_ROOT/external/vaa3d_tools/hackathon/mBrainAligner"
if [ -n "${MBRAINALIGNER_BIN:-}" ]; then
	check_path "mBrainAligner binary" "$MBRAINALIGNER_BIN"
else
	echo "MISS mBrainAligner binary: set MBRAINALIGNER_BIN to the compiled executable"
fi
