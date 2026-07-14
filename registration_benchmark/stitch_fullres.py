#!/usr/bin/env python3
"""Full-resolution coordinate-based stitcher for raw tiled LSFM data.

Unlike ``stitch_mip_tiles.py`` (which stitches the 15-plane MIP previews into a
flat slab), this reads the full Z-stack (``Ex_488_Ch1/<X>/<X>_<Y>/<z>.png``,
~3669 planes/tile) and produces a roughly-isotropic 3D volume downsampled to a
target voxel size (~25 um), comparable to the CCF template (528x320x456) and
the original toy fixed image (551x226x465).

Pipeline, per output Z-plane:
  * average the block of input Z-planes that map to it (Z downsample)
  * feather-blend the 4x6 tiles by their stage coordinates (10% overlap)
  * downsample the blended plane in XY to the target voxel size

Output volume axis order is (Z, Y, X), uint16, zlib-compressed TIFF.
Add a progress bar over (output-plane x tile) placements.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image
from skimage.transform import resize
from tqdm import tqdm

# --- Acquisition constants (from metadata.txt) -----------------------------
POS_UNIT_UM = 0.1       # folder-name X/Y positions are in 0.1 um units
PIXEL_SIZE_UM = 1.800   # XY pixel size (um/pix)
Z_STEP_UM = 2.0         # spacing between successive full-res Z-planes (um)
PX_PER_UNIT = POS_UNIT_UM / PIXEL_SIZE_UM

DEFAULT_SAMPLES = [
    "20260115_20_36_34_AZ4_DB5_P60_WT_M3_A",
    "20260220_11_25_58_AZ4_DB6_P60_GS_F1_A_Raw_Transferred",
]


def list_tiles(channel_dir: Path):
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


def zplane_names(tile_dir: Path):
    return sorted(f for f in os.listdir(tile_dir) if f.endswith(".png"))


def feather_weight(h: int, w: int, ramp: int) -> np.ndarray:
    def ramp1d(n):
        r = np.ones(n, dtype=np.float32)
        if ramp > 0:
            edge = np.linspace(1.0 / (ramp + 1), 1.0, ramp, dtype=np.float32)
            r[:ramp] = edge
            r[-ramp:] = edge[::-1]
        return r
    return np.outer(ramp1d(h), ramp1d(w))


def apply_orientation(volume: np.ndarray, orient: str, flip_axes) -> np.ndarray:
    """Reorient the built (Z=D-V, Y=A-P, X=L-R) volume to match the CCF.

    The Allen CCFv3 'coronal' volume is stored as (A-P, D-V, L-R). This sample's
    acquisition maps to (D-V, A-P, L-R), verified by comparing mid-slices along
    each axis against the CCF (see PROJ_LOG.md). `orient='ccf'` swaps the first
    two axes so the output axis order is (A-P, D-V, L-R). A-P and D-V directions
    line up; L-R handedness is undetermined from symmetric anatomy, so
    `flip_axes` lets you flip axes (in output/CCF order) to test both chiralities.
    """
    if orient == "ccf":
        volume = np.transpose(volume, (1, 0, 2))  # (D-V, A-P, L-R) -> (A-P, D-V, L-R)
    for ax in flip_axes:
        volume = np.flip(volume, axis=ax)
    return np.ascontiguousarray(volume)


def stitch_sample(sample_dir: Path, channel: str, out_path: Path,
                  target_um: float, limit_z: int | None,
                  orient: str = "ccf", flip_axes=()):
    channel_dir = sample_dir / channel
    tiles = list_tiles(channel_dir)
    if not tiles:
        raise SystemExit(f"No tiles found under {channel_dir}")

    znames = zplane_names(tiles[0][2])
    nz_in = len(znames)

    probe = np.asarray(Image.open(tiles[0][2] / znames[0]))
    th, tw = probe.shape

    # full-res tile pixel origins, min corner at (0, 0)
    xmin = min(t[0] for t in tiles)
    ymin = min(t[1] for t in tiles)
    placed = [(int(round((X - xmin) * PX_PER_UNIT)),
               int(round((Y - ymin) * PX_PER_UNIT)), tdir)
              for X, Y, tdir in tiles]
    full_w = max(ox for ox, _, _ in placed) + tw
    full_h = max(oy for _, oy, _ in placed) + th

    # feather ramp from the smallest overlap
    xs = sorted({t[0] for t in tiles})
    ys = sorted({t[1] for t in tiles})
    xstep = int(round((xs[1] - xs[0]) * PX_PER_UNIT)) if len(xs) > 1 else tw
    ystep = int(round((ys[1] - ys[0]) * PX_PER_UNIT)) if len(ys) > 1 else th
    ramp = max(1, min(tw - xstep, th - ystep) // 2)
    weight_tile = feather_weight(th, tw, ramp)

    # downsample factors
    xy_factor = target_um / PIXEL_SIZE_UM          # e.g. 25 / 1.8
    z_factor = target_um / Z_STEP_UM               # e.g. 25 / 2.0
    out_w = max(1, int(round(full_w / xy_factor)))
    out_h = max(1, int(round(full_h / xy_factor)))
    nz_out = max(1, int(round(nz_in / z_factor)))
    if limit_z is not None:
        nz_out = min(nz_out, limit_z)

    print(f"[{sample_dir.name}]")
    print(f"  channel        : {channel}")
    print(f"  tiles          : {len(tiles)}  ({len(xs)} X x {len(ys)} Y)")
    print(f"  input          : {nz_in} Z-planes/tile, {tw}x{th} px @ {PIXEL_SIZE_UM} um")
    print(f"  target voxel   : {target_um} um  (XY /{xy_factor:.2f}, Z /{z_factor:.2f})")
    print(f"  full canvas    : {full_w} x {full_h} px")
    print(f"  output volume  : (Z={nz_out}, Y={out_h}, X={out_w}) uint16 -> {out_path}")

    volume = np.zeros((nz_out, out_h, out_w), dtype=np.uint16)

    total = nz_out * len(placed)
    with tqdm(total=total, desc=f"stitch {sample_dir.name}", unit="tile") as bar:
        for zo in range(nz_out):
            z_lo = int(round(zo * z_factor))
            z_hi = min(nz_in, int(round((zo + 1) * z_factor)))
            z_hi = max(z_hi, z_lo + 1)
            block = znames[z_lo:z_hi]

            accum = np.zeros((full_h, full_w), dtype=np.float32)
            wsum = np.zeros((full_h, full_w), dtype=np.float32)
            for ox, oy, tdir in placed:
                # mean-project this tile's Z-block into one plane
                stack = np.zeros((th, tw), dtype=np.float32)
                for zname in block:
                    stack += np.asarray(Image.open(tdir / zname), dtype=np.float32)
                stack /= len(block)
                accum[oy:oy + th, ox:ox + tw] += stack * weight_tile
                wsum[oy:oy + th, ox:ox + tw] += weight_tile
                bar.update(1)
            np.divide(accum, wsum, out=accum, where=wsum > 0)

            # XY downsample the blended plane to target voxel size
            plane = resize(accum, (out_h, out_w), order=1,
                           anti_aliasing=True, preserve_range=True)
            volume[zo] = np.clip(np.rint(plane), 0, 65535).astype(np.uint16)

    volume = apply_orientation(volume, orient, flip_axes)
    print(f"  orientation    : orient={orient}, flip={list(flip_axes)} "
          f"-> final shape {volume.shape} (CCF order A-P, D-V, L-R)")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(out_path, volume, compression="zlib")
    nonzero = float((volume > 0).mean()) * 100.0
    print(f"  done           : shape {volume.shape}, max={int(volume.max())}, "
          f"nonzero={nonzero:.1f}%, file={out_path.stat().st_size / 1e6:.1f} MB\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", default="data")
    ap.add_argument("--samples", nargs="*", default=DEFAULT_SAMPLES)
    ap.add_argument("--channel", default="Ex_488_Ch1",
                    help="full-res channel subfolder (default: Ex_488_Ch1)")
    ap.add_argument("--out-dir", default="stitched")
    ap.add_argument("--target-um", type=float, default=25.0,
                    help="isotropic output voxel size in um (default: 25, ~CCF scale)")
    ap.add_argument("--limit-z", type=int, default=None,
                    help="only produce this many output Z-planes (for timing tests)")
    ap.add_argument("--orient", choices=["ccf", "none"], default="ccf",
                    help="'ccf' swaps axes to CCF order (A-P, D-V, L-R); "
                         "'none' keeps raw (Z=D-V, Y=A-P, X=L-R)")
    ap.add_argument("--flip", default="",
                    help="comma-separated output axes to flip after reorienting, "
                         "in CCF order 0=A-P,1=D-V,2=L-R (e.g. '2' to flip L-R)")
    args = ap.parse_args()
    flip_axes = tuple(int(a) for a in args.flip.split(",") if a.strip() != "")

    data_root = Path(args.data_root)
    out_dir = Path(args.out_dir)

    t0 = time.time()
    for name in args.samples:
        sample_dir = data_root / name
        tag = f"{int(round(args.target_um))}um"
        out_path = out_dir / f"{name}_{args.channel}_fullres_{tag}.tif"
        stitch_sample(sample_dir, args.channel, out_path, args.target_um,
                      args.limit_z, args.orient, flip_axes)
    print(f"All samples stitched in {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
