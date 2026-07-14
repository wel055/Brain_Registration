# Learned registration research and benchmark report

Date: 2026-06-29  
Machine: Apple M4 MacBook Air, 10 CPU cores, 8 GPU cores, 16 GiB RAM  
Dataset: one experimental LSFM brain; CCFv3 template and annotation atlas  
Network grid: 96 x 48 x 80; output grid: 551 x 226 x 465

## Selection

The implemented pair is VoxelMorph and a memory-conscious TransMorph variant.
VoxelMorph is the mature, simple convolutional baseline for amortized dense
registration. TransMorph adds global transformer context and is a useful
architectural contrast while remaining a one-forward-pass model.

mBrainAligner was not selected for this particular goal. It is highly relevant
to cross-modal whole mouse brain/LSFM registration, but its published automatic
global and local registration stages are C++ optimization modules. Its deep
network supports landmark detection; it is not a drop-in train-once dense-flow
model whose entire registration becomes a single inference call.

SynthMorph was also reviewed. Its distributed models target human brain MRI and
are not directly valid for cleared-mouse-brain LSFM/CCFv3. A mouse-LSFM-specific
retraining effort would be needed.

Primary references:

- VoxelMorph: https://github.com/voxelmorph/voxelmorph
- TransMorph: https://github.com/junyuchen245/TransMorph_Transformer_for_Medical_Image_Registration
- mBrainAligner paper: https://www.nature.com/articles/s41592-021-01334-w
- mBrainAligner source: https://github.com/Vaa3D/vaa3d_tools/tree/master/hackathon/mBrainAligner
- MONAI registration tutorials: https://github.com/Project-MONAI/tutorials/tree/main/3d_registration

## Benchmark result

All quality metrics use the existing evaluator at stride 4. Higher NCC is
better; lower RMSE is better. Learned inference is the median of five CPU
forward/warp calls and excludes TIFF loading, full-grid upsampling, and writing.

| Method | Training (s) | Inference core (s) | End-to-end (s) | NCC | Edge NCC | RMSE |
|---|---:|---:|---:|---:|---:|---:|
| VoxelMorph | 91.81 | 0.296 | 4.05 | 0.90525 | 0.71051 | 0.14808 |
| TransMorph-lite | 50.96 | 0.152 | 2.82 | 0.90542 | 0.70328 | 0.15326 |
| elastix | n/a | 116.7 registration | not comparable | 0.82817 | 0.62452 | 0.17094 |
| ANTs | n/a | 128.7 registration | not comparable | 0.85583 | 0.70492 | 0.15716 |
| emlddmm | n/a | not recorded | not recorded | 0.78032 | 0.41756 | 0.19150 |

The classical timings come from their existing logs and do not have identical
I/O boundaries to the learned core timing. The inference JSON files additionally
record an end-to-end wall time for the learned path.

## Validity and next experiment

This is an implementation and same-pair overfitting benchmark. There is only
one local experimental brain, and it was used for both optimization and
evaluation. The quality numbers therefore must not be reported as generalization
performance. The low-resolution deformation also does not establish
cellular-resolution label accuracy.

For a defensible benchmark, provide multiple brains and split by animal before
training. A useful minimum is 10-20 training brains plus separate validation
and held-out test brains, ideally spanning acquisition batches. Report landmark
target-registration error and atlas-label Dice when manual landmarks/labels are
available, along with foldings (negative Jacobian fraction), full end-to-end
latency, and peak memory. Classical methods should run on exactly the same test
brains and hardware.

## Conda environment audit

Existing environments include `brainreg`, which is the appropriate project
environment. It has Python 3.10, tifffile, SimpleITK, NumPy, and SciPy, but did
not have PyTorch, MONAI, VoxelMorph, or TensorFlow at audit time. Base Miniforge
has PyTorch 2.12 and was used to run this benchmark; it reported Metal/MPS as
unavailable, so computation ran on CPU.

The supplied `environment.yml` is Conda-only: PyTorch, MONAI, NumPy, SciPy,
tifffile, and SimpleITK all have conda-forge packages, including Apple Silicon
PyTorch. The model code is vendored in this project, so no source-only model
package is needed. Installation into `brainreg` was not completed because the
execution environment could not resolve `conda.anaconda.org`; rerun the command
below when Conda networking works:

```bash
CONDA_PKGS_DIRS=/Volumes/Seagate/scratch/Proj_reg_brain/conda_pkgs \
  conda env update -n brainreg -f learned_registration/environment.yml --prune=false
```

Review the solve plan before accepting it because `brainreg` contains older
pip-installed scientific packages. Creating the separate `brainreg-learned`
environment is safer and fully reproducible.
