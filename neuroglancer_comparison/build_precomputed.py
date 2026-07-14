#!/usr/bin/env python3
"""Convert aligned TIFF volumes to Neuroglancer precomputed image layers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import tifffile


def normalize_uint8(volume: np.ndarray) -> np.ndarray:
    volume = np.asarray(volume, dtype=np.float32)
    values = volume[volume > 0]
    if values.size == 0:
        return np.zeros(volume.shape, dtype=np.uint8)
    low, high = np.percentile(values, (0.5, 99.5))
    if high <= low:
        high = low + 1
    scaled = np.clip((volume - low) / (high - low), 0, 1)
    return np.rint(scaled * 255).astype(np.uint8)


def convert(source: Path, output: Path, chunk_size: int, resolution_nm: int) -> dict:
    print(f"Reading {source}", flush=True)
    volume = normalize_uint8(tifffile.imread(source))
    if volume.ndim != 3:
        raise ValueError(f"Expected 3-D TIFF, got {volume.shape}: {source}")
    z_size, y_size, x_size = volume.shape
    scale_key = f"{resolution_nm}_{resolution_nm}_{resolution_nm}"
    chunk_dir = output / scale_key
    chunk_dir.mkdir(parents=True, exist_ok=True)
    info = {
        "@type": "neuroglancer_multiscale_volume",
        "type": "image",
        "data_type": "uint8",
        "num_channels": 1,
        "scales": [{
            "key": scale_key,
            "size": [x_size, y_size, z_size],
            "voxel_offset": [0, 0, 0],
            "resolution": [resolution_nm, resolution_nm, resolution_nm],
            "chunk_sizes": [[chunk_size, chunk_size, chunk_size]],
            "encoding": "raw",
        }],
    }
    (output / "info").write_text(json.dumps(info, indent=2))
    count = 0
    for z0 in range(0, z_size, chunk_size):
        z1 = min(z0 + chunk_size, z_size)
        for y0 in range(0, y_size, chunk_size):
            y1 = min(y0 + chunk_size, y_size)
            for x0 in range(0, x_size, chunk_size):
                x1 = min(x0 + chunk_size, x_size)
                chunk = np.ascontiguousarray(volume[z0:z1, y0:y1, x0:x1])
                (chunk_dir / f"{x0}-{x1}_{y0}-{y1}_{z0}-{z1}").write_bytes(chunk.tobytes(order="C"))
                count += 1
    report = {"source": str(source), "layer": output.name, "shape_zyx": list(volume.shape), "chunks": count}
    print(json.dumps(report), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--elastix", type=Path, required=True)
    parser.add_argument("--ants", type=Path, required=True)
    parser.add_argument("--lddmm", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=64)
    parser.add_argument("--resolution-nm", type=int, default=25000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    reports = []
    for name in ("original", "elastix", "ants", "lddmm"):
        reports.append(convert(getattr(args, name), args.output / name, args.chunk_size, args.resolution_nm))
    (args.output / "manifest.json").write_text(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
