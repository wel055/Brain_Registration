# Neuroglancer registration comparison

3D comparison of the fixed experimental sample against each method's CCF template
warped into sample space. All layers share the same grid, so they overlay
exactly — toggle a layer or change its opacity to compare alignment.

- Original LSFM sample: gray
- Elastix registered CCFv3: red
- ANTs registered CCFv3: green
- emlddmm/LDDMM registered CCFv3: blue

## Usage (Linux server)

One standalone script builds the Neuroglancer *precomputed* layers, serves them
over a CORS-enabled local HTTP server, and prints a ready-to-open URL:

```bash
source ~/miniforge3/etc/profile.d/conda.sh && conda activate brainreg
cd /home/wel4014/brain_registration

SAMPLE=20260115_20_36_34_AZ4_DB5_P60_WT_M3_A
RUN=scratch/Proj_reg_brain/ssd_20260708_215328

python neuroglancer_comparison/compare_registration.py \
  --fixed "stitched/${SAMPLE}_Ex_488_Ch1_fullres_25um.tif" \
  --output-root "$RUN/registration_runs"
```

The server is headless. To view from your laptop, forward the port and open the
printed URL in a browser:

```bash
ssh -N -L 8085:localhost:8085 <user>@<server>
```

Layers are cached under `<run>/neuroglancer/` and reused on subsequent runs
(pass `--rebuild` to regenerate). The display copies are independently
robust-normalized to uint8 — this changes only visualization intensity, not
geometry; the source TIFFs are untouched.

### Key options

| Option | Default | Meaning |
| --- | --- | --- |
| `--fixed` | — (required) | fixed sample TIFF (the stitched volume) |
| `--output-root` | — (required) | `registration_runs` dir with `elastix/ ants/ emlddmm/` |
| `--methods` | `elastix,ants,emlddmm` | which methods to include |
| `--precomputed-dir` | `<output-root>/../neuroglancer` | where to write layers |
| `--port` | `8085` | local HTTP server port |
| `--resolution-nm` | `25000` | isotropic voxel size (nm) |
| `--rebuild` | off | rebuild layers even if cached |
| `--no-serve` | off | build + print URL, don't serve |

## Components (modular)

- `compare_registration.py` — standalone orchestrator (build + serve + URL).
- `build_precomputed.py` — TIFF → Neuroglancer precomputed (robust uint8).
- `cors_server.py` — static HTTP server with Neuroglancer CORS headers.
- `open_comparison.py` — legacy macOS URL opener for the original toy run
  (hardcoded geometry / macOS `open`); superseded by `compare_registration.py`.
