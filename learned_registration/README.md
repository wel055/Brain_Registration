# Learned LSFM brain registration

This is an amortized counterpart to the elastix pipeline: train a dense-flow
network once, then warp the CCFv3 template and annotation into each new sample
space with one forward pass. It implements two compact, comparable models:

- `voxelmorph`: 3-D VoxelMorph-style convolutional U-Net.
- `transmorph`: memory-conscious TransMorph-style model with a transformer at
  the bottleneck. It is intentionally smaller than the paper's GPU configs.

The wrappers use the same fixed/template/annotation roles and output filenames
as `registration_benchmark`, so its evaluator discovers the results directly.

## Important validity boundary

Only one experimental LSFM brain is currently present. The included benchmark
therefore measures optimization and inference on that same pair. It is a
pipeline smoke test, **not evidence of generalization to unseen brains**. A
production experiment should split multiple brains by animal into training,
validation, and held-out test sets. Do not select checkpoints on the test set.

## Recreate the environment

All runtime dependencies in `environment.yml` have Conda packages. The model
implementations are project source, so no `voxelmorph` or TransMorph pip package
is required.

```bash
conda env create -f learned_registration/environment.yml
conda activate brainreg-learned
export PYTHON="$CONDA_PREFIX/bin/python"
```

The existing `brainreg` environment already supplies TIFF/SimpleITK tooling,
but currently lacks PyTorch/MONAI. The base Miniforge environment has PyTorch
and is used by default on this Mac. Package installation was not performed
because the execution environment could not resolve conda.anaconda.org.

## Train and benchmark both models

```bash
DEST=/Volumes/Seagate/scratch/Proj_reg_brain/ssd_20260628_153426 \
  STEPS=100 learned_registration/run_benchmark.sh
```

The default network grid is `96 x 48 x 80`, chosen for the 16 GiB M4 MacBook
Air. Increase it only after measuring memory use. The current PyTorch build does
not expose Metal, so runs use CPU.

## Apply a trained model

```bash
MODEL=voxelmorph learned_registration/run_learned_registration.sh \
  /path/to/new_fixed_brain.tif \
  /Volumes/Seagate/scratch/Proj_reg_brain/ssd_20260628_153426/learned_registration/models/voxelmorph.pt \
  /path/to/output
```

Inference JSON separates the neural forward/warp timing from TIFF input/output
and full-resolution resampling. The output deformation is estimated on the
network grid; this proof-of-concept does not claim cellular-resolution warps.
