# Registration Benchmark Harness

This folder adds comparison pipelines beside the existing elastix run.

Default image roles:

- Fixed image: experimental sample `CB2_KP2_A1a_A.Ex_488.231.mip4.zlib.tif`
- Moving image: CCFv3 template `CCFv3_25um.coronal.tif`
- Moving labels: CCFv3 atlas `CCFv3_Atlas.ccf_2017.coronal.tif`

The goal for each method is the same:

1. Register the CCFv3 template into the experimental sample space.
2. Apply the same transform to the atlas labels.
3. Write method outputs under `registration_runs/<method>/`.
4. Evaluate registered template similarity and label coverage.

TIFF input conversion is handled by `volume_io.py` using tifffile and
SimpleITK/native VTK writing. Do not use the installed c3d 1.0.0 to read the
compressed experimental TIFF: it produced an almost empty converted volume.
Every conversion now verifies shape and voxel values before registration.

## Install/check

Activate the conda environment you want to use, then run:

```sh
registration_benchmark/install_registration_tools.sh
registration_benchmark/check_tools.sh
```

The installer attempts to add ANTs command-line tools from conda-forge, install
Python dependencies, clone emlddmm, and sparse-clone the mBrainAligner source.

mBrainAligner is not a normal macOS conda package. The wrapper expects a
compiled binary via:

```sh
export MBRAINALIGNER_BIN=/path/to/mBrainAligner/executable
```

## Run methods

Elastix wrapper around the current outputs:

```sh
registration_benchmark/run_elastix.sh
```

ANTs:

```sh
registration_benchmark/run_ants.sh
```

emlddmm experimental wrapper:

```sh
registration_benchmark/run_emlddmm.sh
```

mBrainAligner guarded wrapper:

```sh
registration_benchmark/run_mbrainaligner.sh
```

Run everything available:

```sh
registration_benchmark/run_all_available.sh
```

The all-method driver stages registration, transforms, and evaluation under:

```text
/Volumes/Seagate/scratch/Proj_reg_brain/<run-id>
```

After every enabled method passes validation, it performs one non-destructive
transfer of final results to:

```text
./Wenxi/Proj_reg_brain/<run-id>
```

The NAS copy excludes ANTs temporary files, emlddmm input work files, and
matplotlib cache files. Full intermediates remain on the SSD. At least 20 GiB
of free scratch space is required by default. Useful overrides include:

```sh
RUN_ID=my_run THREADS=8 EVALUATION_STRIDE=4 \
  registration_benchmark/run_all_available.sh
```

Set `INCLUDE_ELASTIX`, `INCLUDE_ANTS`, `INCLUDE_EMLDDMM`, or
`INCLUDE_MBRAINALIGNER` to `0` or `1` to select methods. mBrainAligner remains
disabled by default because no compatible local binary is configured.

## Evaluate

Evaluate all detected method outputs:

```sh
registration_benchmark/evaluate_registration.py
```

Outputs:

- `registration_runs/evaluation_metrics.csv`
- `registration_runs/evaluation_metrics.json`

Higher is better:

- `template_ncc_higher_better`
- `template_edge_ncc_higher_better`
- `template_mutual_information_higher_better`
- `template_nmi_higher_better`

Lower is better:

- `template_rmse_lower_better`
- `template_mae_lower_better`

Label summaries:

- `annotation_nonzero_fraction`
- `annotation_sample_mask_coverage`
- `annotation_unique_labels_downsampled`
