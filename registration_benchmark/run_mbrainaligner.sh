#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "$0")/config.sh"
check_common_inputs

METHOD_OUT="$OUTPUT_ROOT/mbrainaligner"
WORK_DIR="$METHOD_OUT/work"
mkdir -p "$WORK_DIR"

SOURCE_DIR="${MBRAINALIGNER_SOURCE:-$PROJECT_ROOT/external/vaa3d_tools/hackathon/mBrainAligner}"
BIN="${MBRAINALIGNER_BIN:-}"

cat > "$METHOD_OUT/README_run_notes.txt" <<MSG
mBrainAligner notes
===================

Inputs prepared for comparison:
  fixed experimental sample: $FIXED_IMAGE
  moving template:           $TEMPLATE_IMAGE
  moving annotation atlas:   $ATLAS_IMAGE

mBrainAligner source expected at:
  $SOURCE_DIR

This project is not a normal macOS conda package. The upstream code is oriented
toward Windows/Linux binaries and Vaa3D tooling. To run it here, provide a
compiled executable or run it on a Linux workstation/container, then set:

  export MBRAINALIGNER_BIN=/path/to/mBrainAligner/executable

The wrapper will then call that binary and save logs under:
  $METHOD_OUT

For fair comparison, the desired output should be:
  1. CCF/template warped into sample space
  2. CCF annotation labels warped into sample space with nearest-neighbor labels

MSG

if [ -z "$BIN" ] || [ ! -x "$BIN" ]; then
	cat "$METHOD_OUT/README_run_notes.txt"
	exit 2
fi

echo "Running mBrainAligner binary:"
echo "  $BIN"

{
	"$BIN" \
		--fixed "$FIXED_IMAGE" \
		--moving "$TEMPLATE_IMAGE" \
		--atlas "$ATLAS_IMAGE" \
		--output "$METHOD_OUT"
} 2>&1 | tee "$METHOD_OUT/mbrainaligner.log"

find "$METHOD_OUT" -type f | sort > "$METHOD_OUT/output_files.txt"
