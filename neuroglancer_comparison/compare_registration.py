#!/usr/bin/env python3
"""Standalone Neuroglancer 3D comparison: sample vs elastix / ANTs / LDDMM.

Builds Neuroglancer *precomputed* image layers for the fixed experimental sample
and each method's CCF template warped into sample space, then serves them over a
CORS-enabled local HTTP server and prints a ready-to-open Neuroglancer URL.

All registered templates live in the same (sample) space and grid, so the layers
overlay exactly — toggle a layer name or adjust its opacity to compare how each
method aligns the CCF to the sample.

Reuses `build_precomputed.convert` (TIFF -> precomputed) and the CORS handler
from `cors_server`. Runs headless on the server; view by SSH-forwarding the port.

Example
-------
    python neuroglancer_comparison/compare_registration.py \
        --fixed stitched/<sample>_Ex_488_Ch1_fullres_25um.tif \
        --output-root scratch/Proj_reg_brain/<run>/registration_runs \
        --resolution-nm 25000

Then locally:  ssh -N -L 8085:localhost:8085 user@server  and open the printed URL.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_precomputed import convert          # noqa: E402
from cors_server import Handler                 # noqa: E402

# Per-method display color (fixed sample stays gray). Order defines panel order.
METHOD_COLORS = {
    "elastix": ("red", "vec3(v, 0.0, 0.0)"),
    "ants": ("green", "vec3(0.0, v, 0.0)"),
    "emlddmm": ("blue", "vec3(0.0, 0.0, v)"),
}
GRAY_RGB = "vec3(v)"


def shader(rgb_expr: str) -> str:
    return ("void main() { float v = toNormalized(getDataValue()); "
            f"emitRGB({rgb_expr}); }}")


def image_layer(name: str, rgb_expr: str, opacity: float) -> dict:
    return {
        "type": "image",
        "source": f"precomputed://http://localhost:{PORT}/{name}",
        "shader": shader(rgb_expr),
        "opacity": opacity,
        "blend": "additive",
    }


def find_registered_template(output_root: Path, method: str, stem: str) -> Path | None:
    exact = output_root / method / f"{stem}_registered_CCFv3_template.tif"
    if exact.exists():
        return exact
    # fall back to any registered-template TIFF in the method dir
    hits = sorted((output_root / method).glob("*_registered_CCFv3_template.tif"))
    return hits[0] if hits else None


def build_layer(source: Path, out_dir: Path, name: str, chunk: int,
                res_nm: int, rebuild: bool) -> tuple[int, int, int]:
    """Build a precomputed layer (skip if present) and return (x, y, z) size."""
    layer_dir = out_dir / name
    info_path = layer_dir / "info"
    if info_path.exists() and not rebuild:
        info = json.loads(info_path.read_text())
        x, y, z = info["scales"][0]["size"]
        print(f"[{name}] precomputed exists, skipping ({x}x{y}x{z}). Use --rebuild to redo.")
        return x, y, z
    report = convert(source, layer_dir, chunk, res_nm)
    z, y, x = report["shape_zyx"]
    return x, y, z


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixed", type=Path, required=True,
                        help="fixed experimental sample TIFF (the stitched volume)")
    parser.add_argument("--output-root", type=Path, required=True,
                        help="registration_runs dir holding elastix/ ants/ emlddmm/")
    parser.add_argument("--methods", default="elastix,ants,emlddmm",
                        help="comma list of methods to include (default: all three)")
    parser.add_argument("--precomputed-dir", type=Path, default=None,
                        help="where to write precomputed layers "
                             "(default: <output-root>/../neuroglancer)")
    parser.add_argument("--port", type=int, default=8085)
    parser.add_argument("--resolution-nm", type=int, default=25000,
                        help="isotropic voxel size in nm (default 25000 = 25 um)")
    parser.add_argument("--chunk-size", type=int, default=64)
    parser.add_argument("--rebuild", action="store_true",
                        help="rebuild precomputed layers even if they exist")
    parser.add_argument("--no-serve", action="store_true",
                        help="build layers and print the URL, but do not serve")
    args = parser.parse_args()

    global PORT
    PORT = args.port

    stem = args.fixed.name
    for ext in (".tif", ".tiff"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break

    out_dir = args.precomputed_dir or (args.output_root.parent / "neuroglancer")
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- assemble the source volumes: fixed sample + each method's template ---
    sources: list[tuple[str, str, Path]] = [("original", GRAY_RGB, args.fixed)]
    for method in [m.strip() for m in args.methods.split(",") if m.strip()]:
        if method not in METHOD_COLORS:
            print(f"WARNING: unknown method '{method}', skipping", file=sys.stderr)
            continue
        reg = find_registered_template(args.output_root, method, stem)
        if reg is None:
            print(f"WARNING: no registered template for '{method}', skipping", file=sys.stderr)
            continue
        sources.append((method, METHOD_COLORS[method][1], reg))

    if len(sources) < 2:
        raise SystemExit("Need the fixed sample plus at least one registered method.")

    # --- build precomputed layers ---
    size_xyz = None
    for name, _rgb, src in sources:
        xyz = build_layer(src, out_dir, name, args.chunk_size, args.resolution_nm, args.rebuild)
        if size_xyz is None:
            size_xyz = xyz
        elif xyz != size_xyz:
            print(f"WARNING: {name} size {xyz} != sample {size_xyz}; overlay may misalign",
                  file=sys.stderr)

    x, y, z = size_xyz
    res_m = args.resolution_nm * 1e-9

    # --- build Neuroglancer state ---
    layers = []
    for i, (name, rgb, _src) in enumerate(sources):
        label = "Original LSFM (gray)" if name == "original" \
            else f"{name} ({METHOD_COLORS[name][0]})"
        opacity = 0.65 if name == "original" else 0.55
        layers.append({"name": label, **image_layer(name, rgb, opacity)})

    state = {
        "dimensions": {"x": [res_m, "m"], "y": [res_m, "m"], "z": [res_m, "m"]},
        "position": [x / 2, y / 2, z / 2],
        "crossSectionScale": 2.2,
        "projectionScale": max(x, y, z) * 8,
        "layers": layers,
        "layout": "4panel",
        "selectedLayer": {"layer": layers[0]["name"], "visible": True},
    }
    encoded = urllib.parse.quote(json.dumps(state, separators=(",", ":")), safe="")
    url = "https://neuroglancer-demo.appspot.com/#!" + encoded

    print("\n" + "=" * 70)
    print(f"Layers built in: {out_dir}")
    print(f"Volume size (x,y,z): {x} x {y} x {z} @ {args.resolution_nm/1000:g} um")
    print("Neuroglancer URL:\n")
    print(url)
    print("\nTo view from your laptop, forward the port then open the URL:")
    print(f"  ssh -N -L {PORT}:localhost:{PORT} <user>@<server>")
    print("=" * 70 + "\n")

    if args.no_serve:
        return

    handler = functools.partial(Handler, directory=str(out_dir))
    print(f"Serving {out_dir} at http://localhost:{PORT}  (Ctrl-C to stop)", flush=True)
    try:
        ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
