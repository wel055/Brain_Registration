# Brain Registration Pipeline — Technical Report

Registers the Allen **CCFv3** template and annotation atlas into an experimental
**LSFM** (light-sheet fluorescence microscopy) mouse-brain sample, and benchmarks
three registration methods (**elastix**, **ANTs**, **emlddmm/LDDMM**) against each
other. This document describes the full end-to-end pipeline as run on the Linux
server after migration from the original macOS laptop: every stage, every script,
its parameters, and every environment variable.

Companion running log: [PROJ_LOG.md](PROJ_LOG.md).

---

## 1. What the pipeline does

```
raw tiled LSFM data (~480 GB)          Allen CCFv3 atlas
        │                                     │
        ▼  Stage 0: stitch + downsample       │
  stitched 3D volume (25 µm, CCF-oriented)    │
        │                                     │
        └──────────────┬──────────────────────┘
                       ▼  Stage 1: registration benchmark
        ┌────────────┬──────┴──────┬────────────┐
   elastix         ANTs        FireANTs       emlddmm
   (affine+Bspline)(SyN, CPU)  (greedy, GPU)  (LDDMM, GPU)
        │            │             │             │
        ▼            ▼             ▼             ▼
   CCF template + annotation warped into sample space
                       │
                       ▼  Stage 2: evaluation
        similarity metrics (whole-volume + per-subregion)
```

The **fixed** image (experimental sample) never moves; the smaller CCF template
and its label atlas are warped *into* sample space. Each method produces a
warped template (`*_registered_CCFv3_template.tif`) and a warped, label-preserving
annotation (`*_annotation.tif`).

---

## 2. Data

### Inputs

| Role | File | Notes |
| --- | --- | --- |
| Fixed / experimental sample | built in Stage 0 from `data/` | raw tiled LSFM |
| Moving structural template | `CCFv3_25um.coronal.tif` | 528×320×456, uint16 |
| Moving subregion labels | `CCFv3_Atlas.ccf_2017.coronal.tif` | 528×320×456, uint32 |

`data → /data2/Wenxi` holds two raw, unstitched tiled acquisitions (read-only source):

```
<sample>/
  metadata.txt, metadata.json, TileSettings.ini, ASI_logging.txt
  Ex_488_Ch1/        full-res: <X>/<X>_<Y>/000000.png … 073360.png   (3669 Z-planes)
  Ex_488_Ch1_MIP/    Z-MIP previews (15 planes)
  Ex_561_Ch3/  Ex_561_Ch3_MIP/
```

Samples: `20260115_20_36_34_AZ4_DB5_P60_WT_M3_A`,
`20260220_11_25_58_AZ4_DB6_P60_GS_F1_A_Raw_Transferred`.

Acquisition geometry (from `metadata.txt`, verified): 4 (X) × 6 (Y) = **24 tiles**,
each **2000 × 1600 px** at **1.8 µm/pix**, Z step **2.0 µm**; folder-name X/Y stage
positions are in **0.1 µm units**; **10 % tile overlap** in both axes.

---

## 3. Environment

The server started bare (system Python, no conda, no registration binaries, no
sudo). A self-contained Miniforge/conda environment provides the whole toolchain
without root.

| Component | Location / version |
| --- | --- |
| Miniforge (conda) | `~/miniforge3` |
| conda env `brainreg` | python 3.10, numpy, scipy, tifffile, imagecodecs, scikit-image, simpleitk, nibabel, pandas, matplotlib, tqdm, ipython |
| ANTs | `brainreg` env, `ants` 2.6 (conda-forge) |
| Convert3D (`c3d`) | `brainreg` env, `convert3d` 1.4.2 (conda-forge) |
| PyTorch (emlddmm) | `brainreg` env, `pytorch-gpu` 2.12, CUDA 12.9 (2× RTX A5000) |
| elastix / transformix | `~/software/elastix-5.3.1` (official precompiled Ubuntu build) |
| conda env `fireants` | isolated env for FireANTs (GPU): conda-forge `pytorch-gpu` 2.12 + `pip install --no-deps fireants hydra-core omegaconf`; kept separate from `brainreg` |

### Activation (prepend to every run)

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate brainreg
export PATH="$HOME/software/elastix-5.3.1/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/software/elastix-5.3.1/lib:$LD_LIBRARY_PATH"
```

Verify with `registration_benchmark/check_tools.sh` (expects all tools OK;
mBrainAligner binary intentionally absent).

---

## 4. Pipeline stages

### Stage 0 — Preprocessing (stitching + downsampling)

#### `registration_benchmark/stitch_fullres.py` *(the real-analysis stitcher)*

Reads the full Z-stack, mean-projects Z-blocks, feather-blends the 4×6 tiles by
their stage coordinates, downsamples to an isotropic target voxel size, and
reorients to match the CCF. Output: `(Z, Y, X)` uint16, zlib TIFF, with a tqdm
progress bar over all `Z_out × 24` tile placements.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--data-root` | `data` | root holding the sample folders |
| `--samples` | both samples | sample folder name(s) to stitch |
| `--channel` | `Ex_488_Ch1` | full-res channel subfolder |
| `--out-dir` | `stitched` | output directory |
| `--target-um` | `25.0` | isotropic output voxel size (µm), ~CCF scale |
| `--limit-z` | `None` | cap output Z-planes (timing tests only) |
| `--orient` | `ccf` | `ccf` = swap axes to CCF order (A-P, D-V, L-R); `none` = raw |
| `--flip` | `""` | comma-list of output axes to flip (CCF order 0=A-P,1=D-V,2=L-R) |

Fixed acquisition constants (from `metadata.txt`): `PIXEL_SIZE_UM = 1.8`,
`Z_STEP_UM = 2.0`, `POS_UNIT_UM = 0.1`. Downsample factors at 25 µm: XY /13.89,
Z /12.5 → output ~**(294, 634, 533)** (A-P, D-V, L-R), voxels 25 µm isotropic.

Orientation was determined empirically (sample vs CCF mid-slices along each axis):
sample axes are **Z=D-V, Y=A-P, X=L-R**; CCF is **(A-P, D-V, L-R)**; fix =
`transpose(1,0,2)`, applied by `--orient ccf` (default). A-P/D-V directions match;
**L-R handedness is undetermined** and is a reflection (not fixable by affine) —
use `--flip 2` to test the other chirality.

Output file: `stitched/<sample>_<channel>_fullres_25um.tif`.

#### `registration_benchmark/stitch_mip_tiles.py` *(stitching validation only)*

Same coordinate-based feather-blend but on the 15-plane MIP previews, no
downsample. Produced a 15×8800×7400 slab — useful to validate the stitching
mechanics but **not a valid registration input** (a thin slab cannot register to
the 3D CCF under the pipeline's isotropic voxel spacing). Superseded by
`stitch_fullres.py`. Args: `--data-root`, `--samples`, `--channel`
(default `Ex_488_Ch1_MIP`), `--out-dir`.

---

### Stage 1 — Registration benchmark

#### `registration_benchmark/run_all_available.sh` *(orchestrator)*

Sources `config.sh`, creates a timestamped run under `SCRATCH_BASE`, runs each
enabled method, evaluates, then copies validated results to `FINAL_BASE`.

| Env var | Default | Meaning |
| --- | --- | --- |
| `RUN_ID` | `ssd_<timestamp>` | run identifier |
| `SCRATCH_RUN` | `$SCRATCH_BASE/$RUN_ID` | working (high-I/O) run dir |
| `FINAL_RUN` | `$FINAL_BASE/$RUN_ID` | archived run dir |
| `MIN_SCRATCH_GB` | `20` | minimum free scratch space required |
| `EVALUATION_STRIDE` | `4` | evaluation downsample stride |
| `INCLUDE_ELASTIX` | `1` | run elastix |
| `INCLUDE_ANTS` | `1` | run ANTs |
| `INCLUDE_EMLDDMM` | `1` | run emlddmm |
| `INCLUDE_MBRAINALIGNER` | `0` | run mBrainAligner (no binary; off) |

It sets and exports `OUTPUT_ROOT=$SCRATCH_RUN/registration_runs`,
`ANTS_TMP_ROOT=$SCRATCH_RUN/ants_tmp`, `KEEP_ANTS_TMP=1`.

It also **times each method** (full wall-clock of each wrapper: registration +
that method's format conversions) and writes `timings.csv` + `timings.json`
(seconds and minutes) into the run dir — the authoritative measured per-method
runtimes. Timings are written incrementally, so a later failure keeps the
completed ones.

#### `registration_benchmark/config.sh` *(shared configuration)*

| Env var | Default | Meaning |
| --- | --- | --- |
| `FIXED_IMAGE` | `CB2_KP2_A1a_A.Ex_488.231.mip4.zlib.tif` | fixed sample (override with the stitched volume) |
| `TEMPLATE_IMAGE` | `CCFv3_25um.coronal.tif` | moving template |
| `ATLAS_IMAGE` | `CCFv3_Atlas.ccf_2017.coronal.tif` | moving annotation |
| `OUTPUT_ROOT` | `registration_runs` | per-method output root |
| `THREADS` | `8` | CPU threads for ANTs (`-n`); elastix hardcodes 24/16 internally |
| `SCRATCH_BASE` | `scratch/Proj_reg_brain` | high-I/O run area (local disk) |
| `FINAL_BASE` | `final_runs/Proj_reg_brain` | archived-run area (local disk) |
| `ELASTIX_SCRIPT` | `new_runElastixTransformix_LSFM.sh` | elastix driver |
| `C3D_BIN` | `$(command -v c3d)` | Convert3D binary (env `c3d`) |

Derives and exports `SAMPLE_STEM` from `FIXED_IMAGE` basename — drives all output
filenames and evaluator discovery.

#### elastix — `run_elastix.sh` → `new_runElastixTransformix_LSFM.sh <fixed.tif>`

- `elastix -threads 24` with affine then B-spline parameter files
  (`Par0000affine_rmc.txt`, `Par0000bspline_rmc.txt`); moving = CCF template.
  - Affine: `AdvancedMattesMutualInformation`, `AffineTransform`,
    `NumberOfResolutions 6`, `AdaptiveStochasticGradientDescent`,
    `MaximumNumberOfIterations 5000`, `RandomCoordinate` sampler,
    `AutomaticTransformInitialization true` (GeometricalCenter).
  - B-spline: `BSplineTransform`, `NumberOfResolutions 3`,
    `FinalGridSpacingInVoxels 25`.
- `transformix -threads 16` applies the transform to the template and, with
  `FinalBSplineInterpolationOrder 0` (nearest-neighbor), to the annotation atlas.
- `c3d` converts elastix NRRD results to TIFF.
- Uses `ELASTIX_DIR=$HOME/software/elastix-5.3.1`; `CONVERT3D_BIN` = `$C3D_BIN`.
- Env: `FORCE_RUN=1` re-runs even if outputs exist.

#### ANTs — `run_ants.sh`

- Converts inputs TIFF→NIfTI with `volume_io.py` (validated, exact voxel values).
- `antsRegistrationSyNQuick.sh -d 3 -f <fixed> -m <template> -o ants_ -n $THREADS -p f -t s`
  (`-t s` = rigid + affine + SyN; `-p f` = float precision).
- `antsApplyTransforms -d 3 -n NearestNeighbor` warps the label atlas.
- Validates the warped template, deformation field, and annotation; converts back
  to TIFF.

| Env var | Default | Meaning |
| --- | --- | --- |
| `ANTS_REG` | `$(command -v antsRegistrationSyNQuick.sh)` | registration driver |
| `ANTS_APPLY` | `$(command -v antsApplyTransforms)` | transform applier |
| `ANTS_TMP_ROOT` | `mktemp` under `SCRATCH_BASE` | intermediate dir |
| `KEEP_ANTS_TMP` | `0` | keep intermediates if `1` |
| `ANTS_PRECISION` | `f` | `-p` flag (float/double) |
| `THREADS` | from config | `-n` thread count |

#### FireANTs (GPU) — `run_fireants.sh` → `fireants_register.py`

GPU-accelerated ANTs-style registration ([FireANTs](https://github.com/rohitrango/FireANTs)),
added because registration is the pipeline bottleneck and elastix/ANTs are CPU-only.

- `run_fireants.sh` mirrors `run_ants.sh`: TIFF→NIfTI via `volume_io.py` (in
  `brainreg`), runs the driver via the **isolated `fireants` env** python
  (`$FIREANTS_PY`), then NIfTI→TIFF back, with the standard output names.
- `fireants_register.py` runs FireANTs **affine + greedy (compositive)** on GPU,
  warps the template (bilinear). For labels: this build has no fused ops, so the
  native segmentation path would force one-hot (infeasible for ~600 Allen labels,
  IDs up to 6e8) — instead labels are **compact-remapped to 0..N (exact in
  float32), warped with plain nearest-neighbour, then mapped back**.
- Runs in its own conda env because its torch build differs from `brainreg`'s
  (see §3). Off by default in `run_all_available.sh` (`INCLUDE_FIREANTS=0`);
  enabled by the wrapper's `--methods` (which includes `fireants` by default).

| Env var | Default | Meaning |
| --- | --- | --- |
| `FIREANTS_PY` | `~/miniforge3/envs/fireants/bin/python` | interpreter for the driver |
| `FIREANTS_SCALES` | `4,2,1` | multi-resolution scales |
| `FIREANTS_AFFINE_ITERS` | `200,100,50` | affine iterations per scale |
| `FIREANTS_GREEDY_ITERS` | `200,100,50` | greedy/deformable iterations per scale |
| `FIREANTS_TMP_ROOT` / `KEEP_FIREANTS_TMP` | run dir / `1` | intermediates |

#### emlddmm / LDDMM — `run_emlddmm.sh`

- Converts inputs TIFF→legacy big-endian VTK with `volume_io.py`.
- Writes a config JSON (two-scale coarse LDDMM) and a graph JSON, then runs
  `external/emlddmm/transformation_graph_v01.py`.
- Config: `downI/downJ [[16,16,16],[8,8,8]]` (16× then 8× downsampling),
  `n_iter [30,15]`, `nt 3`, `sigmaM 1.0`, `sigmaB 2.0`, `epsilon 0.05`,
  `v_res_factor 4.0`; graph uses `transform_all: true`.
- Env: `EMLDDMM_HOME` (default `external/emlddmm`); sets `PYTHONPATH`,
  `MPLCONFIGDIR`. Requires `torch` (GPU used automatically).
- Version-dependent output names are catalogued in `emlddmm/output_files.txt`;
  the wrapper also writes standard-named `*_registered_CCFv3_template.tif` /
  `*_annotation.tif`, plus QC JPEGs under `graph_output/.../qc/`.

#### `registration_benchmark/volume_io.py` *(validated converter, used by ANTs & emlddmm)*

Subcommands: `tiff-to-nifti`, `tiff-to-vtk`, `nifti-to-tiff`, `vtk-to-tiff`
(each `--kind intensity|labels`, optional `--spacing Z Y X`, default `1 1 1`),
`inspect [--require-nonzero]`, `validate-matrix`. Verifies exact voxel values on
round-trip and rejects empty/non-finite volumes. **Not** `c3d` for conversion —
c3d 1.0.0 previously corrupted compressed TIFFs; conversions go through this.

---

### Stage 2 — Evaluation

#### `registration_benchmark/evaluate_registration.py`

Compares each method's warped template against the fixed sample (whole-volume) and
uses each warped atlas label as an ROI (per-subregion). Metrics are **proxies**,
not anatomical ground truth.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--fixed` | toy image (**must override**) | fixed sample; the stem also drives output discovery |
| `--output-root` | `registration_runs` | scans `elastix/ ants/ emlddmm/ …` subdirs |
| `--stride` | `2` (we use `4`) | evaluation downsample stride |
| `--method` | — | evaluate a single explicit method |
| `--registered-template`, `--annotation` | — | explicit paths (with `--method`) |
| `--min-label-voxels` | `50` | drop tiny labels from subregion metrics |
| `--out-csv`, `--out-json` | `registration_runs/…` | whole-volume outputs |
| `--out-subregion-csv`, `--out-subregion-json` | `registration_runs/…` | per-region outputs |

Whole-volume: NCC, edge NCC, mutual information, NMI (higher better); RMSE, MAE
(lower better). Per-subregion: weighted NCC/RMSE, boundary-edge enrichment.

#### `registration_benchmark/subregion_report.py` *(named per-region comparison table)*

Produces a Klein-2009-style comparison: **one table per method** (elastix / ANTs /
emlddmm), with **anatomical region rows** and the six **metrics as columns**
(**NCC, Edge NCC, Mutual Info, NMI, RMSE, MAE**). Reuses the exact metric functions
from `evaluate_registration.py`, and for each region computes the scores between the
warped template and the fixed sample within that region's mask.

- **Names** come from the Allen CCFv3 ontology, saved locally as
  `registration_benchmark/allen_ccf_structures.csv` (fetched from the Allen API,
  `graph_id=1`). Both the acronym (row label, e.g. `L_HPF`) and the full name
  (e.g. "Hippocampal formation") are emitted.
- **Aggregation:** 600+ fine leaf labels are collapsed to ancestors at a chosen
  ontology depth (`--level`, default 5 ≈ Allen "summary structures", ~82 grey
  regions). `--grey-only` (default) drops fiber tracts, ventricles, and unmapped
  labels via the grey-matter root (structure id 8).
- **Hemisphere split:** each region is split across the mid-sagittal plane
  (L–R = axis 2 after reorientation) into `L_`/`R_` rows. Naming is **nominal**
  and inherits the unresolved L–R chirality (swap with `--low-side-name` /
  `--high-side-name`).

| Argument | Default | Meaning |
| --- | --- | --- |
| `--run-dir` | *(required)* | run dir containing `registration_runs/` |
| `--fixed` | *(required)* | fixed sample TIFF (stem drives output discovery) |
| `--output-root` | `<run-dir>/registration_runs` | per-method outputs location |
| `--ontology` | `allen_ccf_structures.csv` | Allen structure list |
| `--stride` | `4` | evaluation downsample stride |
| `--level` | `5` | ontology depth to aggregate to (higher = finer) |
| `--grey-only` / `--include-non-grey` | grey-only | keep grey matter only, or include tracts/ventricles |
| `--min-voxels` | `50` | drop regions smaller than this (per hemisphere) |
| `--low-side-name` / `--high-side-name` | `L` / `R` | hemisphere labels |
| `--out-dir` | `<run-dir>` | where to write the report |

Outputs: `subregion_report.csv` (tidy long: region, acronym, full name,
hemisphere, method, n_voxels, + 6 metrics) and `subregion_report.html`
(one table per method, region rows × 6 metric columns).

**Caveat:** these are intensity-agreement **proxies**, not Jaccard overlap vs a
manual segmentation — same table shape as the reference paper, different meaning.

Run:

```bash
python registration_benchmark/subregion_report.py \
  --run-dir scratch/Proj_reg_brain/ssd_20260708_215328 \
  --fixed "$PWD/stitched/20260115_20_36_34_AZ4_DB5_P60_WT_M3_A_Ex_488_Ch1_fullres_25um.tif"
```

---

### Stage 3 — 3D visualization (Neuroglancer)

Overlay the fixed sample and each method's warped CCF template in an interactive
3D viewer to judge alignment by eye. All layers share the sample-space grid, so
they register exactly; toggle a layer or change its opacity to compare.

- Original LSFM sample: **gray** · elastix: **red** · ANTs: **green** · emlddmm/LDDMM: **blue**

Script: `neuroglancer_comparison/compare_registration.py`. It builds Neuroglancer
*precomputed* layers (cached under `<run>/neuroglancer/`), serves them over a
CORS-enabled local HTTP server, and prints a viewer URL.

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate brainreg
cd /home/wel4014/brain_registration
SAMPLE=20260115_20_36_34_AZ4_DB5_P60_WT_M3_A
RUN=scratch/Proj_reg_brain/ssd_20260708_215328

python neuroglancer_comparison/compare_registration.py \
  --fixed "stitched/${SAMPLE}_Ex_488_Ch1_fullres_25um.tif" \
  --output-root "$RUN/registration_runs"
```

**Two URLs — this is the common point of confusion:**

| URL | What it is | What to do |
| --- | --- | --- |
| `http://localhost:8085` | the **data** server (raw volume chunks) | leave running; a directory listing here is normal — it is *not* the viewer |
| `https://neuroglancer-demo.appspot.com/#!{…}` | the **viewer** (printed by the script) | **open this one** in your browser |

The viewer is a web app that loads in your browser and pulls the volume data from
`localhost:8085` in the background. Because the server is headless, forward the
port from your laptop first, keep that data server running, then open the printed
viewer URL:

```bash
ssh -N -L 8085:localhost:8085 <user>@<server>
```

In the viewer: layer names are tabs at top-left (click to toggle, hover to set
opacity). If panels look black, click the home/reset icon or scroll to zoom.

Options: `--methods` (subset), `--rebuild` (regenerate cached layers), `--port`,
`--no-serve` (just print the URL). For another sample/run, point `--fixed` and
`--output-root` at it. Full details in
[`neuroglancer_comparison/README.md`](neuroglancer_comparison/README.md).

> The viewer is Google's public host fetching from `localhost` (browsers allow
> this for localhost). If layers never load and the console shows a mixed-content
> error, serve a local copy of the Neuroglancer app instead — ask to enable it.

---

## 5. End-to-end run (one sample)

### 5.1 The one-command wrapper — `run_pipeline.sh` *(recommended)*

Runs all three stages for one sample. Takes the **path to the sample folder** and
handles environment activation, path derivation, stitching, registration,
evaluation, and the subregion tables. Run inside `tmux` (registration takes hours).

```bash
cd /home/wel4014/brain_registration
./run_pipeline.sh data/20260115_20_36_34_AZ4_DB5_P60_WT_M3_A
```

All outputs for the sample are written under **`<output-dir>/<sample-name>/`**:

```
<output-dir>/<sample>/
  stitched/<sample>_<channel>_fullres_25um.tif   # fixed image (shared across runs)
  <run-id>/                                       # cleaned results
    registration_runs/{elastix,ants,fireants,emlddmm}/...
    evaluation_metrics.csv / .json
    subregion_metrics.csv / .json
    timings.csv / timings.json          # per-method wall-clock
    subregion_report.csv / .html
    timings.csv / timings.json                    # measured per-method runtimes
  .scratch/<run-id>/                              # full intermediates (ANTs tmp, emlddmm work)
```

It self-activates the conda env and elastix; every knob has a default and is
overridable:

| Option | Default | Meaning |
| --- | --- | --- |
| `--output-dir DIR` | `./results` | results root; a `<sample>` subfolder is created here |
| `--channel NAME` | `Ex_488_Ch1` | full-res channel subfolder |
| `--target-um N` | `25` | isotropic output voxel size (µm) |
| `--threads N` | `32` | CPU threads (ANTs + elastix + transformix) |
| `--eval-stride N` | `4` | evaluation / report downsample stride |
| `--methods LIST` | `elastix,ants,fireants,emlddmm` | which methods to run (`fireants` = GPU) |
| `--emlddmm-iters JSON` | `[30, 15]` | emlddmm iterations per scale (the "number of steps") |
| `--fireants-iters CSV` | `200,100,50` | FireANTs iterations per scale |
| `--orient MODE` | `ccf` | axis reorientation (`ccf` or `none`) |
| `--flip AXES` | none | axes to flip, CCF order `0,1,2` (e.g. `2` for L–R) |
| `--report-level N` | `5` | ontology aggregation depth for the tables |
| `--run-id ID` | `run_<timestamp>` | run identifier (subfolder under the sample) |
| `--conda-env NAME` | `brainreg` | conda env to activate |
| `--elastix-dir DIR` | `~/software/elastix-5.3.1` | elastix install prefix |
| `--skip-stitch` | off | reuse an existing stitched volume (skip stage 0) |
| `--no-report` | off | skip the subregion tables (stage 2) |

Examples:

```bash
# other sample -> its own results/<sample>/ folder, custom output root
./run_pipeline.sh data/20260220_11_25_58_AZ4_DB6_P60_GS_F1_A_Raw_Transferred \
    --output-dir /data2/Wenxi/results --threads 48 --methods ants,elastix

# quick emlddmm (fewer LDDMM iterations), reuse a prior stitch
./run_pipeline.sh data/<sample> --emlddmm-iters "[15, 8]" --skip-stitch
```

The wrapper prints the resolved config, then the run dir and key output paths at
the end.

### 5.2 Manual, stage by stage *(advanced / debugging)*

Equivalent to the wrapper, with the §3 activation block sourced first:

```bash
cd /home/wel4014/brain_registration
SAMPLE=20260115_20_36_34_AZ4_DB5_P60_WT_M3_A

# Stage 0: full-res stitch -> 25 µm CCF-oriented volume (~25 min, progress bar)
python registration_benchmark/stitch_fullres.py --samples "$SAMPLE" \
  && \
# Stages 1+2: benchmark (elastix + ANTs + emlddmm) + evaluation (~1-3 h)
FIXED_IMAGE="$PWD/stitched/${SAMPLE}_Ex_488_Ch1_fullres_25um.tif" \
THREADS=32 \
registration_benchmark/run_all_available.sh
```

Approximate timing (measured on this server): full-res stitch ~25 min/sample;
registration + evaluation ~1–3 h/sample (ANTs SyN dominates).

Outputs land in `scratch/Proj_reg_brain/<RUN_ID>/` and are copied to
`final_runs/Proj_reg_brain/<RUN_ID>/`:

```
registration_runs/{elastix,ants,fireants,emlddmm}/<stem>_registered_CCFv3_template.tif
registration_runs/{elastix,ants,fireants,emlddmm}/<stem>_annotation.tif
evaluation_metrics.csv / .json
subregion_metrics.csv / .json
timings.csv / timings.json          # per-method wall-clock (seconds + minutes)
```

---

## 6. Environment variables — quick reference

| Variable | Used by | Default |
| --- | --- | --- |
| `FIXED_IMAGE` | all | toy image (set to the stitched volume) |
| `TEMPLATE_IMAGE` / `ATLAS_IMAGE` | all | CCF template / annotation |
| `OUTPUT_ROOT` | all methods, eval | set by orchestrator |
| `THREADS` | ANTs (`-n`) | `8` (we use `32`) |
| `ELASTIX_THREADS` / `TRANSFORMIX_THREADS` | elastix / transformix | `24` / `16` (wrapper sets both to `--threads`) |
| `EMLDDMM_N_ITER` | emlddmm | `[30, 15]` iterations per scale (wrapper: `--emlddmm-iters`) |
| `FIREANTS_PY` / `FIREANTS_SCALES` / `FIREANTS_AFFINE_ITERS` / `FIREANTS_GREEDY_ITERS` | FireANTs | env python / `4,2,1` / `200,100,50` / `200,100,50` |
| `SCRATCH_BASE` / `FINAL_BASE` | orchestrator | local `scratch/` / `final_runs/` |
| `RUN_ID`, `SCRATCH_RUN`, `FINAL_RUN` | orchestrator | timestamped |
| `MIN_SCRATCH_GB` | orchestrator | `20` |
| `EVALUATION_STRIDE` | orchestrator | `4` |
| `INCLUDE_ELASTIX/ANTS/EMLDDMM/FIREANTS/MBRAINALIGNER` | orchestrator | `1/1/1/0/0` (wrapper enables via `--methods`) |
| `ANTS_TMP_ROOT`, `KEEP_ANTS_TMP`, `ANTS_PRECISION` | ANTs | run dir / `1` / `f` |
| `FORCE_RUN` | elastix | `0` |
| `EMLDDMM_HOME` | emlddmm | `external/emlddmm` |
| `C3D_BIN` | config/elastix | env `c3d` |

---

## 7. Results — sample 1 (`AZ4_DB5_M3`, 488, 25 µm)

Three-way evaluation (`stride 4`), **bold = best**:

| Metric | elastix | ANTs | emlddmm | Better |
| --- | ---: | ---: | ---: | --- |
| Template NCC | 0.7518 | **0.8085** | 0.6589 | higher |
| Edge NCC | 0.5519 | **0.6400** | 0.2742 | higher |
| Mutual information | 0.5726 | **0.7095** | 0.4219 | higher |
| NMI | 0.2181 | **0.2690** | 0.1639 | higher |
| RMSE | 0.2063 | **0.1816** | 0.2318 | lower |
| Weighted subregion NCC | 0.2014 | **0.2652** | 0.1520 | higher |
| Median boundary-edge enrichment | 0.9698 | 0.9857 | **0.9874** | higher |

**ANTs is strongest** on all global and weighted-subregion metrics (consistent
with the earlier toy-data benchmark); emlddmm leads only the boundary-edge proxy.
These metrics do not establish anatomical correctness — verify overlays visually.

---

## 8. macOS → Linux migration fixes

Applied so future runs work end-to-end on the server:

- `config.sh` — repointed `SCRATCH_BASE` / `FINAL_BASE` from macOS
  `/Volumes/Seagate` and the `./Wenxi` NAS symlink to local disk.
- `run_all_available.sh` — removed the hard `/Volumes/Seagate/scratch` mount
  check; added `--fixed "$FIXED_IMAGE"` to the evaluator call.
- `new_runElastixTransformix_LSFM.sh` — ported BSD `sed -i ''` → GNU `sed -i`
  (×8); macOS `c3d` path → env `c3d`.
- `evaluate_registration.py` — derive `SAMPLE_STEM` from `--fixed` instead of a
  hardcoded toy filename (output discovery now works for any fixed image).
- Env — installed `ipython` (emlddmm import) and `pytorch-gpu` into `brainreg`.

---

## 9. Limitations & next steps

- **L–R handedness** of the stitched volume is undetermined from symmetric
  anatomy and is a reflection affine can't fix — resolve by running both
  chiralities (`--flip 2`) and comparing, or from a known asymmetric landmark.
- **Metrics are proxies.** Confirm anatomy by overlaying warped templates on the
  fixed sample in Fiji; prefer manual landmarks / segmented reference regions for
  a publication-level claim.
- **Sample 2** not yet run — use the §5 command with the other sample name.
- **mBrainAligner** not run (no compiled Linux binary; disabled).
- The **561 channel** and **full XY resolution** are available if a higher-detail
  fixed image is later required.
