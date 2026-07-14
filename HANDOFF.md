# Brain Registration Project Handoff

Last updated: 2026-06-28

## Read This First

This project registers the Allen CCFv3 template and annotation atlas into an
experimental LSFM sample. The current method status is:

| Method | Status | Use for analysis? |
| --- | --- | --- |
| elastix/transformix | Completed | Yes |
| ANTs | Corrected run completed and validated | Yes |
| emlddmm/LDDMM | Corrected run completed and validated | Yes, with metric caveats |
| mBrainAligner | Source downloaded; no runnable macOS binary configured | No |

The authoritative corrected run is:

```text
./Wenxi/Proj_reg_brain/ssd_20260628_153426
```

Its complete scratch copy, including excluded intermediates, is:

```text
/Volumes/Seagate/scratch/Proj_reg_brain/ssd_20260628_153426
```

The earlier ANTs and emlddmm attempts are scientifically invalid because c3d
1.0.0 misread the compressed experimental TIFF as an almost empty volume.
They are retained only as diagnostics. Do not claim that mBrainAligner has
been run.

## Safety Boundary

The root SMB mount and `/Volumes/data` are explicitly off-limits. Do not read,
list, modify, or delete anything there.

The only approved writable output mount is:

```text
/Volumes/Wenxi
```

The approved high-I/O scratch location is:

```text
/Volumes/Seagate/scratch/Proj_reg_brain
```

The workspace contains an approved symbolic link to this mount:

```text
./Wenxi -> /Volumes/Wenxi
```

Prefer the workspace-relative link in commands when practical:

```text
./Wenxi/Proj_reg_brain/<run-id>
```

Keep this project's future runs organized under:

```text
/Volumes/Wenxi/Proj_reg_brain/<run-id>
```

Never modify or delete anything outside `/Volumes/Wenxi`. Do not use
`rsync --delete`. Existing remote folders must not be cleaned up without
explicit user approval.

Heavy direct writes can saturate the SMB share and make Finder appear hung.
`run_all_available.sh` now performs all registration and evaluation I/O on the
Seagate SSD, requires at least 20 GiB free, validates the outputs, and then
makes one non-destructive `rsync -av --partial` transfer to the NAS. Keep this
staged workflow for future runs.

## Workspace

Project root:

```text
/Users/wenxili/Desktop/Weil Cornell/Lab/Proj_reg_brain
```

The git worktree is dirty and most project data/scripts are untracked. Do not
reset, revert, or delete existing changes. Current notable status:

```text
M  new_runElastixTransformix_LSFM.sh
?? Par0000affine_rmc.txt
?? Par0000bspline_rmc.txt
?? elastixOutput/
?? transformixOutput/
?? registration_benchmark/
?? registration_runs/
?? external/
```

## Input Images

The image roles are important:

| Role | File |
| --- | --- |
| Fixed image / experimental sample | `CB2_KP2_A1a_A.Ex_488.231.mip4.zlib.tif` |
| Moving structural template | `CCFv3_25um.coronal.tif` |
| Moving subregion labels | `CCFv3_Atlas.ccf_2017.coronal.tif` |

Absolute input directory:

```text
/Users/wenxili/Desktop/Weil Cornell/Lab/Proj_reg_brain
```

Approximate input sizes are 16 MB, 147 MB, and 294 MB respectively.

## Installed Tools

The active environment was Miniforge base:

```text
/Users/wenxili/miniforge3
```

Verified tools and sources:

```text
ANTs scripts: /Users/wenxili/miniforge3/bin/
elastix:       /Users/wenxili/software/elastix-5.3.1/bin/elastix
transformix:   /Users/wenxili/software/elastix-5.3.1/bin/transformix
c3d:           /Applications/Convert3DGUI.app/Contents/bin/c3d
emlddmm:       external/emlddmm
mBrainAligner: external/vaa3d_tools/hackathon/mBrainAligner
```

Run this before new work:

```bash
registration_benchmark/check_tools.sh
```

Expected result: ANTs, c3d, and emlddmm source are found. mBrainAligner reports
a missing binary until `MBRAINALIGNER_BIN` points to a compiled executable.

## Key Scripts

```text
new_runElastixTransformix_LSFM.sh
registration_benchmark/config.sh
registration_benchmark/run_elastix.sh
registration_benchmark/run_ants.sh
registration_benchmark/run_emlddmm.sh
registration_benchmark/run_mbrainaligner.sh
registration_benchmark/run_all_available.sh
registration_benchmark/evaluate_registration.py
registration_benchmark/volume_io.py
registration_benchmark/test_volume_io.py
registration_benchmark/install_registration_tools.sh
registration_benchmark/check_tools.sh
```

`registration_benchmark/config.sh` contains the default input roles and c3d
path. `volume_io.py` is now the required validated TIFF/NIfTI/VTK converter
for ANTs and emlddmm. Override paths with `FIXED_IMAGE`, `TEMPLATE_IMAGE`,
`ATLAS_IMAGE`, and `OUTPUT_ROOT` when needed.

Preferred complete run command:

```bash
registration_benchmark/run_all_available.sh
```

## Elastix

Parameter files:

```text
Par0000affine_rmc.txt
Par0000bspline_rmc.txt
```

Run command:

```bash
./new_runElastixTransformix_LSFM.sh \
  CB2_KP2_A1a_A.Ex_488.231.mip4.zlib.tif
```

The script uses the local CCF template and atlas paths internally. It uses
elastix for affine/B-spline registration, transformix to apply transforms, and
c3d to convert output formats.

Local elastix internals:

```text
elastixOutput/
```

Local final transformix images:

```text
transformixOutput/CB2_KP2_A1a_A.Ex_488.231.mip4.zlib._registered_CCFv3_template.tif
transformixOutput/CB2_KP2_A1a_A.Ex_488.231.mip4.zlib._annotation.tif
```

The first TIFF is the CCF template warped into sample space. The second is the
CCF annotation atlas warped into sample space with label-preserving settings.

Historical NAS copies from the earlier mount configuration are recorded below.
These paths are for provenance only; do not attempt to access `/Volumes/data`:

```text
/Volumes/data/research/Wenxi/Proj_reg_brain/elastixOutput
/Volumes/data/research/Wenxi/Proj_reg_brain/transformixOutput
```

## ANTs

Wrapper:

```bash
registration_benchmark/run_ants.sh
```

The wrapper converts TIFF to NIfTI with `volume_io.py`, verifies exact voxel
values and nonzero content, runs `antsRegistrationSyNQuick.sh -t s`, applies
the transform to labels with nearest-neighbor interpolation, validates the
deformation and warped volumes, and converts results back to TIFF.

Do not replace this conversion with the installed c3d 1.0.0. On this sample,
c3d converted a volume with 35,595,819 nonzero voxels into one with only 182
nonzero voxels. That bad input caused the earlier misleading registrations.

Do not point `ANTS_TMP_ROOT` at the NAS. The pipeline driver automatically
places it under the SSD run. For a standalone ANTs run, use:

```bash
RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOCAL_ROOT="/Volumes/Seagate/scratch/Proj_reg_brain/ants_${RUN_ID}"

OUTPUT_ROOT="$LOCAL_ROOT/registration_runs" \
ANTS_TMP_ROOT="$LOCAL_ROOT/ants_tmp" \
KEEP_ANTS_TMP=1 \
THREADS=8 \
registration_benchmark/run_ants.sh
```

Corrected, independently read-back ANTs outputs:

```text
./Wenxi/Proj_reg_brain/ssd_20260628_153426/registration_runs/ants
```

Important ANTs outputs there:

```text
CB2_KP2_A1a_A.Ex_488.231.mip4.zlib_registered_CCFv3_template.tif
CB2_KP2_A1a_A.Ex_488.231.mip4.zlib_annotation.tif
ants_0GenericAffine.mat
ants_1Warp.nii.gz
ants_1InverseWarp.nii.gz
ants_Warped.nii.gz
ants_InverseWarped.nii.gz
ants_registration.log
ants_apply_labels.log
```

Validation summary: registered template and annotation both have shape
`551x226x465`; the template is finite and 73.51% nonzero; the annotation is
`uint32`, finite, and 34.10% nonzero; the deformation field is finite and
97.53% nonzero.

## emlddmm / LDDMM

Wrapper:

```bash
registration_benchmark/run_emlddmm.sh
```

Compatibility and validation work in the wrapper:

- Uses `volume_io.py` to write emlddmm-compatible big-endian legacy VTK.
- Preserves intensity versus annotation types and expected VTK header order.
- Uses `transform_all: true`, required by this graph-runner version.
- Uses conservative coarse-scale optimization parameters.
- Rejects non-finite `A.txt`, empty warped images, and NaN outputs.

Corrected valid output folder:

```text
./Wenxi/Proj_reg_brain/ssd_20260628_153426/registration_runs/emlddmm
```

Important files:

```text
emlddmm.log
emlddmm_config.json
emlddmm_graph.json
graph_output/
CB2_KP2_A1a_A.Ex_488.231.mip4.zlib_registered_CCFv3_template.tif
CB2_KP2_A1a_A.Ex_488.231.mip4.zlib_annotation.tif
```

Validation summary: `A.txt` is finite; both TIFFs have shape `551x226x465`;
the registered template is finite and 84.79% nonzero; the annotation is
`uint32`, finite, and 35.50% nonzero.

An earlier corrected attempt completed registration but ran out of internal
disk space while reconstructing a volume. Its small diagnostic files remain
preserved in the previous run at:

```text
./Wenxi/Proj_reg_brain/corrected_20260628_142511/diagnostics/emlddmm_local_disk_failure
```

## mBrainAligner

Source exists at:

```text
external/vaa3d_tools/hackathon/mBrainAligner
```

It has not been run. The wrapper requires:

```bash
export MBRAINALIGNER_BIN=/path/to/compiled/mBrainAligner/executable
registration_benchmark/run_mbrainaligner.sh
```

The upstream project is oriented toward Linux/Windows/Vaa3D. A Linux machine
or container is likely the practical next step. Do not describe it as a
working deep-learning benchmark until a compatible binary/model and real
outputs are verified.

## Evaluation

Evaluator:

```bash
registration_benchmark/evaluate_registration.py
```

Outputs:

```text
./Wenxi/Proj_reg_brain/ssd_20260628_153426/evaluation_metrics.csv
./Wenxi/Proj_reg_brain/ssd_20260628_153426/evaluation_metrics.json
./Wenxi/Proj_reg_brain/ssd_20260628_153426/subregion_metrics.csv
./Wenxi/Proj_reg_brain/ssd_20260628_153426/subregion_metrics.json
```

Whole-volume metrics compare the fixed experimental sample with the registered
template:

- NCC, edge NCC, mutual information, and NMI: higher is better.
- RMSE and MAE: lower is better.

Per-subregion metrics use each warped atlas label as a region of interest and
report regional similarity, foreground fractions, and boundary-edge measures.
These are proxy metrics, not anatomical ground truth. A stronger benchmark
would require manual landmarks or manually segmented reference regions.

Corrected three-way evaluation (`stride=4`):

| Metric | Elastix | ANTs | emlddmm | Better |
| --- | ---: | ---: | ---: | --- |
| Template NCC | 0.8282 | **0.8558** | 0.7803 | Higher |
| Edge NCC | 0.6245 | **0.7049** | 0.4176 | Higher |
| Mutual information | 0.6249 | **0.7355** | 0.4688 | Higher |
| NMI | 0.2635 | **0.3064** | 0.2054 | Higher |
| RMSE | 0.1709 | **0.1572** | 0.1915 | Lower |
| Weighted subregion NCC | 0.3336 | **0.3765** | 0.1826 | Higher |
| Weighted subregion RMSE | 0.2353 | **0.2248** | 0.2633 | Lower |
| Median boundary-edge enrichment | 0.9509 | 0.9645 | **0.9745** | Higher |

ANTs is the strongest method on the global intensity and weighted subregion
metrics. emlddmm leads only the median boundary-edge enrichment proxy. These
metrics do not establish anatomical correctness by themselves; inspect the
overlays in Fiji and prefer manual landmarks or manually segmented regions for
a definitive comparison.

Relevant metric references previously identified:

- Viola and Wells, 1997, mutual-information registration.
- Studholme et al., 1999, normalized mutual information.
- Avants et al., 2011, ANTs registration evaluation.
- Klein et al., 2009, nonlinear registration benchmark methodology.

## Remote Folder Interpretation

Use this corrected run for the current comparison:

```text
./Wenxi/Proj_reg_brain/ssd_20260628_153426/
  registration_runs/
    ants/
    emlddmm/
    elastix/
  evaluation_metrics.csv
  evaluation_metrics.json
  subregion_metrics.csv
  subregion_metrics.json
  PIPELINE_STATUS.txt
  RUN_NOTES.txt
  scripts/
```

`PIPELINE_STATUS.txt` reads `complete`. The SSD run occupies about 7.1 GB;
the NAS copy is about 5.2 GB because transient ANTs files, emlddmm input work
files, and cache files are excluded.

All future output should use the same basic layout:

```text
/Volumes/Wenxi/Proj_reg_brain/<run-id>/
  registration_runs/
    ants/
    emlddmm/
    elastix/
  evaluation_metrics.csv
  evaluation_metrics.json
  subregion_metrics.csv
  subregion_metrics.json
  RUN_NOTES.txt
```

## Recommended Next Session

1. Confirm the approved output mount exists at `/Volumes/Wenxi`. Do not inspect
   `/Volumes/data`.
2. Open the sample plus each method's registered template in Fiji and inspect
   the same anatomical planes and boundaries.
3. Review `subregion_metrics.csv` for poor-performing labels and verify those
   regions visually.
4. Add manual landmarks or manually segmented reference regions before making
   a publication-level method claim.
5. Move mBrainAligner execution to a compatible Linux/Vaa3D environment if it
   is still required.
6. Ask before reorganizing or deleting any NAS folder.

## Quick Resume Commands

```bash
cd "/Users/wenxili/Desktop/Weil Cornell/Lab/Proj_reg_brain"
registration_benchmark/check_tools.sh
git status --short
```

Check the approved output mount through the workspace symlink without
modifying it:

```bash
ls -la "./Wenxi/"
```

Start a new staged SSD-to-NAS benchmark:

```bash
registration_benchmark/run_all_available.sh
```

Copy a completed scratch run manually only when necessary:

```bash
rsync -av --partial \
  "/Volumes/Seagate/scratch/Proj_reg_brain/LOCAL_RUN/" \
  "./Wenxi/Proj_reg_brain/NEW_RUN/"
```

If the SMB mount disappears, stop remote work, remount in Finder, verify the
path again, and resume with `rsync --partial`. Keep computation local.
