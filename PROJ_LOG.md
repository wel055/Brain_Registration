# Project Log — Brain Registration (Server Migration)

Server: Linux (64 CPUs), project root `/home/wel4014/brain_registration`.
Goal: run the existing registration benchmark pipeline (elastix / ANTs / emlddmm
+ evaluation) on the **real** LSFM dataset, after migrating from the laptop where
it only ran on a down-sampled toy input.

This log tracks: what's been done, our conclusions, and what's still open.
See `HANDOFF.md` for the original (laptop-era) project description.

---

## 1. Dataset inspection — DONE

**Conclusion: the real data is NOT in the format the pipeline expects. A new
stitching/preprocessing stage is required before any registration can run.**

### What the pipeline expects (`registration_benchmark/config.sh`)
A single, already-stitched, down-sampled 3D volume TIFF as the fixed image:

| Role | File | Size |
| --- | --- | --- |
| Fixed sample | `CB2_KP2_A1a_A.Ex_488.231.mip4.zlib.tif` | 16 MB |
| Template | `CCFv3_25um.coronal.tif` | 147 MB |
| Atlas | `CCFv3_Atlas.ccf_2017.coronal.tif` | 294 MB |

The `.mip4.zlib` name + the commented `coronal_Ex_445_Ch0_stitched.tif` in
`new_runElastixTransformix_LSFM.sh:21` confirm the fixed image was a
**pre-stitched + MIP-downsampled** product. Nothing in the repo produces it;
the project's own notes state the wrappers "do not manually split and restitch
spatial chunks."

### What `data/` actually contains
`data -> /data2/Wenxi` holds **2 raw, unstitched tiled LSFM acquisitions,
~480 GB total** (244 GB + 235 GB):

```
<sample>/
  metadata.txt, metadata.json, TileSettings.ini, ASI_logging.txt
  Ex_488_Ch1/        full-res 488 channel: <X>/<X>_<Y>/000000.png … 073360.png
  Ex_488_Ch1_MIP/    same tiles, Z-MIP blocks only (15 planes)
  Ex_561_Ch3/  Ex_561_Ch3_MIP/    561 channel
```

Samples:
- `20260115_20_36_34_AZ4_DB5_P60_WT_M3_A`
- `20260220_11_25_58_AZ4_DB6_P60_GS_F1_A_Raw_Transferred`

| Property | Value |
| --- | --- |
| Tile grid | 4 (X) × 6 (Y) = **24 tiles** per channel |
| Channels | Ex_488_Ch1, Ex_561_Ch3 (+ `_MIP` variants) |
| Tile image | 2000 × 1600 px, int32 (16-bit data), PNG |
| Full-res Z | 3669 slices/tile (step 20) → 88,056 PNGs per channel |
| MIP variant | **15 Z-blocks/tile** (per-tile MIP over Z chunks) |
| Pixel size | 1.800 µm/pix (XY); Z step 2.00 µm |
| Position units | folder-name X/Y are in **0.1 µm units** (derived from Z-slice naming) |
| Tile overlap | **exactly 10%** in both X and Y (200 px X, 160 px Y) |
| MIP stitched canvas | ~7400 × 8800 px, 15 Z-planes |

### Decisions taken (user)
- Input source: **488 MIP tiles** (fast prototype path).
- Samples: **both**.
- Stitcher: **coordinate-based Python stitch** (recommended — no external
  stitching tool needed; use metadata X/Y stage positions + 10% overlap blend).

---

## 2. Environment setup — DONE

**Conclusion: server started essentially bare (system Python 3.10, no conda, no
registration binaries, no sudo). A self-contained Miniforge/conda env now
provides the full toolchain with no root required.**

### Starting state
- Ubuntu, Python 3.10.12, 64 CPUs, internet OK.
- No passwordless sudo → cannot `apt install`.
- No conda anywhere; `CONDA_PREFIX` empty.
- Registration binaries elastix/transformix/c3d/ANTs — all missing.
- Python missing: tifffile, imagecodecs, SimpleITK, nibabel, scikit-image.
- Disk: `/` (home) ~520 GB free; `/data2` ~623 GB free.

### Miniforge vs Miniconda
Does not matter — same conda tool; only the default channel differs (Miniforge
→ conda-forge). We pull everything from conda-forge explicitly, so staying on
the pre-installed **Miniforge** is fine. No switch to Miniconda needed.

### What was installed
- **Miniforge** at `~/miniforge3` (batch install, no sudo).
- **conda env `brainreg`** (conda-forge): python 3.10, numpy 2.2, scipy 1.15,
  tifffile 2025, imagecodecs, scikit-image 0.25, SimpleITK 2.5, nibabel 5.4,
  pandas 2.3, matplotlib, PIL, **ants 2.6**, **convert3d 1.4.2** (c3d).
  - Note: conda-forge has NO `elastix` binary package (only Python wrappers),
    which caused the first `mamba create` to fail. Fixed by dropping `elastix`
    from the env and installing it separately (below).
  - c3d here is 1.4.2, newer than the 1.0.0 that previously corrupted
    compressed TIFFs; conversions still go through `volume_io.py`.
- **elastix / transformix 5.3.1** — official precompiled Ubuntu build extracted
  to `~/software/elastix-5.3.1` (exact version match to the laptop). Verified
  `--version` runs with bundled libs on `LD_LIBRARY_PATH`.

### How to activate the environment
```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate brainreg
export PATH="$HOME/software/elastix-5.3.1/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/software/elastix-5.3.1/lib:$LD_LIBRARY_PATH"
```

### Verified working
- Binaries: `elastix`, `transformix`, `c3d`, `antsRegistration`,
  `antsRegistrationSyNQuick.sh`, `antsApplyTransforms`.
- Python libs: all imports above succeed in-env.

---

## 3. Pipeline migration (macOS -> Linux) — DONE

**Conclusion: the registration scripts carried macOS-specific paths and BSD
shell idioms that would fail on this server. All were ported; `check_tools.sh`
now passes with the `brainreg` env active.**

Fixes applied:
- `registration_benchmark/config.sh` — `SCRATCH_BASE` and `FINAL_BASE` were
  `/Volumes/Seagate/...` and the `./Wenxi` NAS symlink (neither exists here).
  Repointed to local disk: `$PROJECT_ROOT/scratch/Proj_reg_brain` and
  `$PROJECT_ROOT/final_runs/Proj_reg_brain` (`/` has 511 GB free ≫ 20 GB req).
  `C3D_BIN` already falls back to the env `c3d` on non-macOS.
- `registration_benchmark/run_all_available.sh` — removed the hard
  `/Volumes/Seagate/scratch` mount check (the writability + free-space checks
  below it already cover the local scratch dir).
- `new_runElastixTransformix_LSFM.sh` — ported 8× BSD `sed -i ''` to GNU
  `sed -i`, and replaced the macOS `c3d` path with `${C3D_BIN:-$(command -v c3d)}`.
  (The script already self-configured `ELASTIX_DIR=$HOME/software/elastix-5.3.1`,
  which matches the install.)
- emlddmm dependency: `brainreg` env lacked `torch`; installing GPU PyTorch
  (conda-forge `pytorch-gpu`; driver 535 / CUDA 12.2, 2× RTX A5000).

`check_tools.sh` result (env active): fixed/template/atlas files OK, c3d OK
(env), ANTs OK (env), emlddmm source OK. Only mBrainAligner binary missing
(expected; disabled by default).

---

## 4. Open / not done

- [x] **Write the MIP-tile stitcher** — `registration_benchmark/stitch_mip_tiles.py`
      (coordinate-based Python, linear feathered blend, tqdm progress).
- [x] **Run the stitcher for both samples** — DONE (26.8 s total; each output
      (15, 8800, 7400) uint16, ~56% nonzero, ~618 MB) →
      `stitched/<sample>_Ex_488_Ch1_MIP_stitched.tif`. **This validated the
      stitching approach.**
- [x] **Port pipeline scripts to Linux** (see section 3).
- [x] **Confirm emlddmm deps in-env** — installing torch (GPU) into `brainreg`.
- [ ] **GEOMETRY DECISION (next)** — the MIP stitch is a 15-plane *slab*; the
      registration wrappers use isotropic voxel spacing, so a 15-plane volume
      cannot register meaningfully to the 3D CCF (528×320×456). Decide between:
      (a) run the MIP once as a mechanical server smoke-test (needs an XY
      downsample to ~25 µm to be tractable), or (b) build the **full-res 488**
      path (3669 Z → a real 3D volume like the toy 551×226×465) for the actual
      analysis. **Deferred per user; discuss before running registration.**
- [ ] **Run the registration benchmark** (elastix, ANTs, emlddmm) + evaluation,
      once the geometry decision is made.

### FireANTs GPU method added (DONE)
- Added **FireANTs** (GPU-accelerated ANTs-style registration,
  github.com/rohitrango/FireANTs) as a 4th method, since profiling showed
  registration (esp. emlddmm) is the pipeline bottleneck and ANTs/elastix are
  CPU-only.
- **Isolated env** `fireants` (its pip build pulls torch cu130, which the 535 /
  CUDA-12.2 driver can't run). Recreated on conda-forge `pytorch-gpu` (2.12,
  cu129 — works on this driver, like `brainreg`) + `pip install --no-deps
  fireants hydra-core omegaconf antlr4`. Kept separate so `brainreg` (the proven
  3-method env) is untouched.
- **Driver** `registration_benchmark/fireants_register.py`: FireANTs affine +
  greedy (compositive), on GPU. Warps template (bilinear) and annotation. This
  build has no fused ops (`use_ffo=False`), so the native segmentation path would
  force one-hot (infeasible for ~600 Allen labels, IDs up to 6e8). Instead:
  compact-remap labels to 0..N (exact in float32) → warp with plain `nearest` →
  map back. Verified warped labels stay a discrete subset of the originals.
- **Wrapper** `registration_benchmark/run_fireants.sh`: mirrors `run_ants.sh`
  (TIFF→NIfTI via volume_io in `brainreg`, run driver via the `fireants` env
  python `$FIREANTS_PY`, NIfTI→TIFF back), standard output names.
- **Wiring**: `run_all_available.sh` (`INCLUDE_FIREANTS`, `FIREANTS_TMP_ROOT`,
  timed via `run_timed`, excluded from final rsync); `evaluate_registration.py`
  and `subregion_report.py` method lists; `run_pipeline.sh` (`--methods` now
  includes `fireants` by default, `--fireants-iters`).
- **Timing**: per-method wall-clock already logged by `run_all_available.sh` to
  `timings.csv` / `timings.json` in the run dir (all four methods).
- Verified end-to-end on real data (small iters): outputs (634,294,533),
  discrete labels, evaluator + report discover `fireants`. Real run uses
  `--fireants-iters 200,100,50`.
- Env knobs: `FIREANTS_PY`, `FIREANTS_SCALES`, `FIREANTS_AFFINE_ITERS`,
  `FIREANTS_GREEDY_ITERS`.

### One-command pipeline wrapper (DONE)
- Built `run_pipeline.sh` at project root: takes the **path to a sample folder**
  and runs all three stages (stitch → registration+eval → subregion tables) on
  that one sample. Self-activates the conda env + elastix; derives data-root and
  sample name from the path; prints resolved config and output paths.
- Output is parametrized via `--output-dir` (default `./results`): everything for
  a sample goes under `<output-dir>/<sample>/` — `stitched/` (fixed image, shared
  across runs), `<run-id>/` (cleaned results: registration_runs, metrics, tables),
  and `.scratch/<run-id>/` (full intermediates). Achieved by rooting
  `SCRATCH_BASE`/`FINAL_BASE` at the sample dir; the subregion report runs on the
  cleaned final run. `--run-id` defaults to `run_<timestamp>`.
- Parametrized knobs (all defaulted): `--channel`, `--target-um`, `--threads`,
  `--eval-stride`, `--methods`, `--emlddmm-iters` (LDDMM "number of steps"),
  `--orient`, `--flip`, `--report-level`, `--run-id`, `--conda-env`,
  `--elastix-dir`, `--skip-stitch`, `--no-report`.
- To make threads/steps configurable end-to-end, added env hooks to the
  underlying scripts (defaults unchanged): `new_runElastixTransformix_LSFM.sh`
  now honors `ELASTIX_THREADS` / `TRANSFORMIX_THREADS`; `run_emlddmm.sh` honors
  `EMLDDMM_N_ITER` for the config JSON `n_iter`.
- Run: `./run_pipeline.sh data/<sample_folder> [options]` (see README §5.1).

### Per-method runtime timing (DONE)
- `run_all_available.sh` now times each method with a `run_timed` helper (wraps
  each method call, `date +%s.%N` before/after) and writes `timings.csv` +
  `timings.json` (seconds + minutes) into the run dir. Written incrementally so a
  later failure keeps completed timings.
- Measures each wrapper's **full wall-clock** (registration + that method's format
  conversions) — the true per-method cost, consistently measured across methods.
  (Tool-core times are still in each method's own log, e.g. elastix.log
  "Total time elapsed", ants_registration.log "Total elapsed time".)
- Motivation: the deck's first runtime numbers were reconstructed post-hoc
  (elastix/ANTs from logs, emlddmm from dir-mtime gap). This makes them measured
  and reproducible. Re-run the pipeline to populate `timings.json`; the deck can
  then be regenerated from real numbers.

### elastix caching bug (found via timing, FIXED)
- First timed run showed `elastix: 0.4s` (vs ANTs 771s, emlddmm 242s). The timer
  was correct — `run_elastix.sh` had **skipped** the actual registration: it
  reuses a cached result from the shared `PROJECT_ROOT/transformixOutput/` dir
  (keyed by filename stem) unless missing or `FORCE_RUN=1`. A stale July-8 result
  was present, so elastix only `cp`'d it (0.4s), never re-ran.
- Latent correctness risk beyond timing: re-stitching a sample (e.g. new
  orientation) with the same output filename would silently reuse the stale
  elastix result.
- Fix: `run_all_available.sh` now sets `export FORCE_RUN="${FORCE_RUN:-1}"`, so a
  benchmark run always recomputes elastix (real timing + no stale reuse). Override
  with `FORCE_RUN=0` to allow the cache. ANTs/emlddmm wrappers have no such cache.
- To get a correct elastix time on an existing sample: re-run with `--skip-stitch`
  (stitch is cached, elastix now recomputes in ~2 min).

### Per-subregion named comparison table (DONE)
- Built `registration_benchmark/subregion_report.py` — Klein-2009-style tables,
  **one table per method** (elastix/ANTs/emlddmm), region rows × metric columns
  (NCC, Edge NCC, MI, NMI, RMSE, MAE). Reuses `evaluate_registration.py` metrics.
- Region names from the Allen CCFv3 ontology, fetched and saved as
  `registration_benchmark/allen_ccf_structures.csv` (Allen API `graph_id=1`,
  1327 structures). Emits acronym + full name (e.g. HPF → "Hippocampal formation").
- Aggregation: 600+ leaf labels → ancestors at ontology depth (`--level`,
  default 5 ≈ Allen summary structures, ~82 grey regions); `--grey-only` (default)
  drops fiber tracts/ventricles/unmapped via grey-matter root (id 8).
- L/R split across mid-sagittal plane (L-R = axis 2); naming nominal (inherits the
  unresolved L-R chirality; swap with `--low-side-name`/`--high-side-name`).
- Outputs `subregion_report.csv` + `subregion_report.html` (best-per-region
  highlighted). For sample 1: 140 regions × 3 methods.
- **Metrics are intensity-agreement proxies, NOT Jaccard vs ground truth** — no
  manual segmentation of the sample exists. Same table shape as the reference
  paper, different meaning. True overlap accuracy would need manual segmentation
  (deferred per user).
- Run:
  ```bash
  python registration_benchmark/subregion_report.py \
    --run-dir scratch/Proj_reg_brain/ssd_20260708_215328 \
    --fixed "$PWD/stitched/20260115_..._Ex_488_Ch1_fullres_25um.tif"
  ```

### Orientation (verified empirically)
- Registration tools do **not** auto-correct gross orientation here: `volume_io.py`
  writes identity-direction NIfTI, elastix uses GeometricalCenter init, ANTs uses
  center-of-mass init — all local optimizers that fix only translation + modest
  rotation, never axis swaps / reflections.
- Compared sample vs CCF mid-slices along all 3 axes. Sample acquisition axes are
  **Z = D-V, Y = A-P, X = L-R**; CCF 'coronal' is **(A-P, D-V, L-R)**. Fix =
  `np.transpose(volume, (1,0,2))` — verified: reoriented sample matches CCF
  coronal/horizontal/sagittal axis-for-axis. Baked into `stitch_fullres.py` as
  `--orient ccf` (default).
- A-P and D-V **directions** confirmed matched (anterior/dorsal at index 0 in both).
  **L-R handedness is undetermined** from symmetric anatomy and is a *reflection*
  affine won't fix — resolve by running both chiralities (`--flip 2`) and comparing
  metrics + lateralization, or from a known asymmetric landmark.

### First full-res run (sample 1) — issues found & fixed
- Run `scratch/Proj_reg_brain/ssd_20260708_215328`: **elastix and ANTs completed**
  (warped template + annotation written); **emlddmm failed** at
  `import IPython` (ModuleNotFoundError). Fix: `mamba install ipython` into
  `brainreg`; emlddmm now imports.
- Bug: `run_all_available.sh` did not forward `FIXED_IMAGE` to
  `evaluate_registration.py`, whose `--fixed` defaults to the old toy image.
  With a differently-shaped stitched fixed image the evaluator would crash on a
  shape mismatch. Fixed by adding `--fixed "$FIXED_IMAGE"` to the eval call.
- Resume (reusing completed elastix/ANTs): set `OUTPUT_ROOT` to the failed run's
  `registration_runs`, `FIXED_IMAGE` to the stitched volume, run
  `run_emlddmm.sh`, then `evaluate_registration.py --fixed ...`.
- emlddmm re-ran fine after the IPython fix (wrote standard-named
  `..._registered_CCFv3_template.tif` / `..._annotation.tif`).
- Bug: `evaluate_registration.py` hardcoded `SAMPLE_STEM` = the toy image name,
  so `default_methods()` couldn't find our outputs ("No registered template
  outputs found"). Fixed: derive the stem from `--fixed` (new `sample_stem()`).

**MILESTONE (2026-07-08): full pipeline ran end-to-end on real full-res sample 1**
(`20260115_..._WT_M3_A`, 488, 25 um, CCF-oriented). Three-way evaluation
(stride 4), * = best:

| Metric | elastix | ANTs | emlddmm |
| --- | ---: | ---: | ---: |
| Template NCC ↑ | 0.7518 | **0.8085** | 0.6589 |
| Edge NCC ↑ | 0.5519 | **0.6400** | 0.2742 |
| Mutual info ↑ | 0.5726 | **0.7095** | 0.4219 |
| NMI ↑ | 0.2181 | **0.2690** | 0.1639 |
| RMSE ↓ | 0.2063 | **0.1816** | 0.2318 |
| Subregion NCC ↑ | 0.2014 | **0.2652** | 0.1520 |
| Boundary edge ↑ | 0.9698 | 0.9857 | **0.9874** |

ANTs strongest (matches the earlier toy-data pattern). Metrics are proxies, not
anatomical ground truth — still need visual overlay verification in Fiji and the
L-R chirality check.

### Caveats / notes
- The MIP path yields only **15 Z-planes** → a very thin slab. Registration
  wrappers convert to NIfTI with isotropic spacing (`volume_io.py` default
  `spacing=(1,1,1)`), so the slab cannot register meaningfully to the full 3D
  CCF. Good enough only as a mechanical toolchain smoke-test. The **full-res
  488 path** (3669 Z) is required for a real anatomical result.
- Raw data (`/data2/Wenxi`) is read-only source; do not modify. Write stitched
  outputs and runs under the project dir or a server scratch location.
- mBrainAligner: still not run (needs a compiled Linux binary); out of scope
  for now.

---

_Last updated: 2026-07-08_
