#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FIXED_IMAGE="${FIXED_IMAGE:-$PROJECT_ROOT/CB2_KP2_A1a_A.Ex_488.231.mip4.zlib.tif}"
TEMPLATE_IMAGE="${TEMPLATE_IMAGE:-$PROJECT_ROOT/CCFv3_25um.coronal.tif}"
ATLAS_IMAGE="${ATLAS_IMAGE:-$PROJECT_ROOT/CCFv3_Atlas.ccf_2017.coronal.tif}"

OUTPUT_ROOT="${OUTPUT_ROOT:-$PROJECT_ROOT/registration_runs}"
THREADS="${THREADS:-8}"
# Server (Linux) migration: the macOS SSD/NAS mounts do not exist here. Both the
# high-I/O scratch area and the archived-final area live on local disk (`/` has
# ample free space). Override with SCRATCH_BASE / FINAL_BASE env vars if desired.
SCRATCH_BASE="${SCRATCH_BASE:-$PROJECT_ROOT/scratch/Proj_reg_brain}"
FINAL_BASE="${FINAL_BASE:-$PROJECT_ROOT/final_runs/Proj_reg_brain}"

ELASTIX_SCRIPT="${ELASTIX_SCRIPT:-$PROJECT_ROOT/new_runElastixTransformix_LSFM.sh}"

if [ -z "${C3D_BIN:-}" ]; then
	if [ -x "/Applications/Convert3DGUI.app/Contents/bin/c3d" ]; then
		C3D_BIN="/Applications/Convert3DGUI.app/Contents/bin/c3d"
	else
		C3D_BIN="$(command -v c3d || true)"
	fi
fi
export C3D_BIN

SAMPLE_BASENAME="$(basename "$FIXED_IMAGE")"
SAMPLE_STEM="${SAMPLE_BASENAME%.tif}"
SAMPLE_STEM="${SAMPLE_STEM%.tiff}"
export PROJECT_ROOT FIXED_IMAGE TEMPLATE_IMAGE ATLAS_IMAGE OUTPUT_ROOT THREADS SCRATCH_BASE FINAL_BASE
export ELASTIX_SCRIPT SAMPLE_STEM

require_file() {
	local path="$1"
	local label="$2"
	if [ ! -f "$path" ]; then
		echo "Missing $label: $path" >&2
		exit 1
	fi
}

require_executable() {
	local path="$1"
	local label="$2"
	if [ -z "$path" ] || [ ! -x "$path" ]; then
		echo "Missing $label executable: $path" >&2
		exit 1
	fi
}

check_common_inputs() {
	require_file "$FIXED_IMAGE" "fixed experimental sample"
	require_file "$TEMPLATE_IMAGE" "moving CCFv3 template"
	require_file "$ATLAS_IMAGE" "moving CCFv3 annotation atlas"
	require_executable "$C3D_BIN" "Convert3D/c3d"
	mkdir -p "$OUTPUT_ROOT"
}
