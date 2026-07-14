#!/usr/bin/env python3
"""Coordinate-based stitcher for raw tiled LSFM MIP data.

============================ WARNING — DO NOT USE FOR REGISTRATION ============
This script is NOT part of the working pipeline and its output does NOT work as
a registration input. The MIP channel has only ~15 Z-blocks, so this produces a
thin ~15 x 8800 x 7400 SLAB ("the weird-shaped brain"), not a 3D volume. Because
the registration wrappers use isotropic voxel spacing, that slab cannot register
to the 320-plane 3D CCF and yields anatomically meaningless results.

Kept ONLY to validate the coordinate-based stitching mechanics (correct tile
placement / seam blending). For any real fixed image use ``stitch_fullres.py``
(full-res channel, e.g. Ex_488_Ch1), which builds a proper ~cube 3D volume.
==============================================================================

Turns a raw tiled acquisition (``<sample>/Ex_488_Ch1_MIP/<X>/<X>_<Y>/<z>.png``)
into a single 3D volume TIFF. (Historically described as a ``FIXED_IMAGE`` — see
the warning above: it is not usable as one.)

Placement is purely coordinate-based: tile origins come from the stage
positions encoded in the folder names, converted to pixels via the objective
pixel size. Overlaps are combined with a linear feathered (distance-ramp) blend
so seams are smooth. See PROJ_LOG.md for the derived geometry.

Derived geometry (verified for the current data):
  * folder-name X/Y stage positions are in units of 0.1 um
  * XY pixel size 1.800 um/pix, tiles 2000 (X) x 1600 (Y)
  * grid 4 (X) x 6 (Y) = 24 tiles, ~10% overlap in both axes
  * MIP variant has 15 Z-blocks per tile -> output volume is 15 planes thick

Output volume axis order is (Z, Y, X), uint16, zlib-compressed TIFF.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image
from tqdm import tqdm

# --- Acquisition constants (from metadata.txt) -----------------------------
POS_UNIT_UM = 0.1      # folder-name X/Y positions are in 0.1 um units
PIXEL_SIZE_UM = 1.800  # XY pixel size (um/pix)
# pixels per position-unit = POS_UNIT_UM / PIXEL_SIZE_UM
PX_PER_UNIT = POS_UNIT_UM / PIXEL_SIZE_UM

DEFAULT_SAMPLES = [
    "20260115_20_36_34_AZ4_DB5_P60_WT_M3_A",
    "20260220_11_25_58_AZ4_DB6_P60_GS_F1_A_Raw_Transferred",
]


def list_tiles(channel_dir: Path):
    """Return [(X, Y, tile_dir), ...] for one <sample>/<channel> directory."""
    tiles = []
    for xname in sorted(os.listdir(channel_dir)):
        xdir = channel_dir / xname
        if not (xdir.is_dir() and xname.isdigit()):
            continue
        for tname in sorted(os.listdir(xdir)):
            tdir = xdir / tname
            parts = tname.split("_")
            if not (tdir.is_dir() and len(parts) == 2
                    and parts[0].isdigit() and parts[1].isdigit()):
                continue
            tiles.append((int(parts[0]), int(parts[1]), tdir))
    return tiles


def zblock_names(tile_dir: Path):
    return sorted(f for f in os.listdir(tile_dir) if f.endswith(".png"))


def feather_weight(h: int, w: int, ramp: int) -> np.ndarray:
    """Linear ramp weight, rising from ~0 at each edge to 1 over `ramp` px.

    Gives feathered blending in the overlap regions so tile seams disappear.
    """
    def ramp1d(n):
        r = np.ones(n, dtype=np.float32)
        if ramp > 0:
            edge = np.linspace(1.0 / (ramp + 1), 1.0, ramp, dtype=np.float32)
            r[:ramp] = edge
            r[-ramp:] = edge[::-1]
        return r
    wy = ramp1d(h)
    wx = ramp1d(w)
    return np.outer(wy, wx)


def stitch_sample(sample_dir: Path, channel: str, out_path: Path):
    channel_dir = sample_dir / channel
    tiles = list_tiles(channel_dir)
    if not tiles:
        raise SystemExit(f"No tiles found under {channel_dir}")

    znames = zblock_names(tiles[0][2])
    nz = len(znames)

    # probe one tile for size/dtype
    probe = np.asarray(Image.open(tiles[0][2] / znames[0]))
    th, tw = probe.shape  # tile height (Y), width (X)

    # pixel origin of every tile, normalised so the min corner is (0, 0)
    xmin = min(t[0] for t in tiles)
    ymin = min(t[1] for t in tiles)
    placed = []
    for X, Y, tdir in tiles:
        ox = int(round((X - xmin) * PX_PER_UNIT))  # column offset (X)
        oy = int(round((Y - ymin) * PX_PER_UNIT))  # row offset (Y)
        placed.append((ox, oy, tdir))

    canvas_w = max(ox for ox, _, _ in placed) + tw
    canvas_h = max(oy for _, oy, _ in placed) + th

    # feather ramp = half the smallest overlap, so weights hit ~0 at the seam
    xs = sorted({t[0] for t in tiles})
    ys = sorted({t[1] for t in tiles})
    xstep_px = int(round((xs[1] - xs[0]) * PX_PER_UNIT)) if len(xs) > 1 else tw
    ystep_px = int(round((ys[1] - ys[0]) * PX_PER_UNIT)) if len(ys) > 1 else th
    overlap_x = max(0, tw - xstep_px)
    overlap_y = max(0, th - ystep_px)
    ramp = max(1, min(overlap_x, overlap_y) // 2)
    weight_tile = feather_weight(th, tw, ramp)

    print(f"[{sample_dir.name}]")
    print(f"  channel        : {channel}")
    print(f"  tiles          : {len(tiles)}  ({len(xs)} X x {len(ys)} Y)")
    print(f"  tile size      : {tw} x {th} px, dtype {probe.dtype}")
    print(f"  z-planes       : {nz}")
    print(f"  overlap        : {overlap_x} px (X), {overlap_y} px (Y); feather ramp {ramp} px")
    print(f"  canvas         : {canvas_w} x {canvas_h} px")
    print(f"  output volume  : (Z={nz}, Y={canvas_h}, X={canvas_w}) uint16 -> {out_path}")

    volume = np.zeros((nz, canvas_h, canvas_w), dtype=np.uint16)

    # progress bar over every (z-plane, tile) placement
    total = nz * len(placed)
    with tqdm(total=total, desc=f"stitch {sample_dir.name}", unit="tile") as bar:
        for zi, zname in enumerate(znames):
            accum = np.zeros((canvas_h, canvas_w), dtype=np.float32)
            wsum = np.zeros((canvas_h, canvas_w), dtype=np.float32)
            for ox, oy, tdir in placed:
                img = np.asarray(Image.open(tdir / zname)).astype(np.float32)
                accum[oy:oy + th, ox:ox + tw] += img * weight_tile
                wsum[oy:oy + th, ox:ox + tw] += weight_tile
                bar.update(1)
            np.divide(accum, wsum, out=accum, where=wsum > 0)
            volume[zi] = np.clip(np.rint(accum), 0, 65535).astype(np.uint16)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(out_path, volume, compression="zlib")
    nonzero = float((volume > 0).mean()) * 100.0
    print(f"  done           : max={int(volume.max())}, nonzero={nonzero:.1f}%, "
          f"file={out_path.stat().st_size / 1e6:.1f} MB\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default="data",
                    help="root holding the sample folders (default: data)")
    ap.add_argument("--samples", nargs="*", default=DEFAULT_SAMPLES,
                    help="sample folder names to stitch (default: both)")
    ap.add_argument("--channel", default="Ex_488_Ch1_MIP",
                    help="channel subfolder to stitch (default: Ex_488_Ch1_MIP)")
    ap.add_argument("--out-dir", default="stitched",
                    help="output directory for stitched TIFFs (default: stitched/)")
    args = ap.parse_args()

    data_root = Path(args.data_root)
    out_dir = Path(args.out_dir)

    t0 = time.time()
    for name in args.samples:
        sample_dir = data_root / name
        out_path = out_dir / f"{name}_{args.channel}_stitched.tif"
        stitch_sample(sample_dir, args.channel, out_path)
    print(f"All samples stitched in {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
