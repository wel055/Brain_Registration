#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXTERNAL_DIR="$PROJECT_ROOT/external"
mkdir -p "$EXTERNAL_DIR"

if [ -z "${CONDA_PREFIX:-}" ]; then
	echo "Activate the conda environment you want to use first, then rerun this script." >&2
	exit 1
fi

echo "Installing into conda environment:"
echo "  $CONDA_PREFIX"
echo

conda install -y -c conda-forge \
	ants \
	numpy \
	scipy \
	scikit-image \
	matplotlib \
	tifffile \
	nibabel \
	simpleitk \
	h5py

python -m pip install pynrrd torch

if [ ! -d "$EXTERNAL_DIR/emlddmm/.git" ]; then
	git clone https://github.com/twardlab/emlddmm.git "$EXTERNAL_DIR/emlddmm"
else
	git -C "$EXTERNAL_DIR/emlddmm" pull --ff-only
fi

cat <<'MSG'
Skipping external/emlddmm/requirements.txt on this machine.
That file pins old NumPy/SciPy/scikit-image versions that do not build cleanly
on this Apple Silicon Python 3.10 environment. The script installed modern
runtime packages instead.
MSG

if [ ! -d "$EXTERNAL_DIR/vaa3d_tools/.git" ]; then
	git clone --depth 1 --filter=blob:none --sparse https://github.com/Vaa3D/vaa3d_tools.git "$EXTERNAL_DIR/vaa3d_tools"
	git -C "$EXTERNAL_DIR/vaa3d_tools" sparse-checkout set hackathon/mBrainAligner
else
	git -C "$EXTERNAL_DIR/vaa3d_tools" pull --ff-only
fi

cat <<'MSG'

Install notes:
  ANTs should provide antsRegistrationSyNQuick.sh and antsApplyTransforms in this conda env.
  emlddmm is cloned under external/emlddmm.
  mBrainAligner source is cloned under external/vaa3d_tools/hackathon/mBrainAligner.

mBrainAligner is not a normal macOS conda package. You will likely need a Linux/Windows
binary or a compiled executable, then set:

  export MBRAINALIGNER_BIN=/path/to/mBrainAligner/executable

Run:

  registration_benchmark/check_tools.sh

MSG
