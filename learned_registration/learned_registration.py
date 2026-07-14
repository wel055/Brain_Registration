#!/usr/bin/env python3
"""Train and apply amortized 3-D atlas registration models.

The implementation is intentionally self-contained: it provides a compact
VoxelMorph-style U-Net and a compact TransMorph-style transformer/conv model.
Both predict a dense displacement field and use the same spatial transformer,
losses, preprocessing, and output code so their benchmark is comparable.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import random
import sys
import time
from pathlib import Path

import numpy as np
import tifffile
import torch
import torch.nn as nn
import torch.nn.functional as F


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def read_volume(path: Path) -> np.ndarray:
    arr = np.asarray(tifffile.imread(path))
    if arr.ndim != 3:
        raise ValueError(f"Expected a 3-D TIFF at {path}, found {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError(f"Non-finite voxels in {path}")
    return arr


def robust_normalize(arr: np.ndarray) -> np.ndarray:
    arr = arr.astype(np.float32, copy=False)
    foreground = arr[arr > 0]
    values = foreground if foreground.size >= 100 else arr.reshape(-1)
    lo, hi = np.percentile(values, (1.0, 99.5))
    if hi <= lo:
        hi = lo + 1.0
    return np.clip((arr - lo) / (hi - lo), 0, 1).astype(np.float32)


def tensor_volume(arr: np.ndarray, shape: tuple[int, int, int], mode: str = "trilinear") -> torch.Tensor:
    x = torch.from_numpy(np.ascontiguousarray(arr))[None, None].float()
    return F.interpolate(x, size=shape, mode=mode, align_corners=True if mode != "nearest" else None)


def make_grid(shape: tuple[int, int, int], device: torch.device) -> torch.Tensor:
    z = torch.linspace(-1, 1, shape[0], device=device)
    y = torch.linspace(-1, 1, shape[1], device=device)
    x = torch.linspace(-1, 1, shape[2], device=device)
    zz, yy, xx = torch.meshgrid(z, y, x, indexing="ij")
    return torch.stack((xx, yy, zz), dim=-1)[None]


def warp(src: torch.Tensor, flow: torch.Tensor, mode: str = "bilinear") -> torch.Tensor:
    """Pull src through voxel-unit flow ordered (dz, dy, dx)."""
    d, h, w = src.shape[2:]
    scale = torch.tensor((2 / max(w - 1, 1), 2 / max(h - 1, 1), 2 / max(d - 1, 1)), device=src.device)
    offset = torch.stack((flow[:, 2], flow[:, 1], flow[:, 0]), dim=-1) * scale
    return F.grid_sample(src, make_grid((d, h, w), src.device) + offset, mode=mode,
                         padding_mode="border", align_corners=True)


class ConvBlock(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, stride: int = 1):
        super().__init__()
        self.block = nn.Sequential(
            nn.Conv3d(in_ch, out_ch, 3, stride=stride, padding=1),
            nn.InstanceNorm3d(out_ch), nn.LeakyReLU(0.2, inplace=True),
            nn.Conv3d(out_ch, out_ch, 3, padding=1),
            nn.InstanceNorm3d(out_ch), nn.LeakyReLU(0.2, inplace=True),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.block(x)


class VoxelMorphNet(nn.Module):
    """Small 3-D VoxelMorph-style encoder-decoder."""
    def __init__(self, base: int = 8, max_disp: float = 12.0):
        super().__init__()
        self.max_disp = max_disp
        self.e1 = ConvBlock(2, base)
        self.e2 = ConvBlock(base, base * 2, 2)
        self.e3 = ConvBlock(base * 2, base * 4, 2)
        self.b = ConvBlock(base * 4, base * 4, 2)
        self.d3 = ConvBlock(base * 8, base * 4)
        self.d2 = ConvBlock(base * 6, base * 2)
        self.d1 = ConvBlock(base * 3, base)
        self.flow = nn.Conv3d(base, 3, 3, padding=1)
        nn.init.normal_(self.flow.weight, 0, 1e-5)
        nn.init.zeros_(self.flow.bias)

    def forward(self, moving: torch.Tensor, fixed: torch.Tensor) -> torch.Tensor:
        a = self.e1(torch.cat((moving, fixed), 1))
        b = self.e2(a)
        c = self.e3(b)
        x = self.b(c)
        x = self.d3(torch.cat((F.interpolate(x, size=c.shape[2:], mode="trilinear", align_corners=True), c), 1))
        x = self.d2(torch.cat((F.interpolate(x, size=b.shape[2:], mode="trilinear", align_corners=True), b), 1))
        x = self.d1(torch.cat((F.interpolate(x, size=a.shape[2:], mode="trilinear", align_corners=True), a), 1))
        return torch.tanh(self.flow(x)) * self.max_disp


class TransMorphLite(nn.Module):
    """Memory-conscious TransMorph-style transformer at the U-Net bottleneck."""
    def __init__(self, base: int = 8, max_disp: float = 12.0):
        super().__init__()
        self.max_disp = max_disp
        self.e1 = ConvBlock(2, base, 2)
        self.e2 = ConvBlock(base, base * 2, 2)
        self.e3 = ConvBlock(base * 2, base * 4, 2)
        layer = nn.TransformerEncoderLayer(d_model=base * 4, nhead=4, dim_feedforward=base * 8,
                                           dropout=0.0, batch_first=True, norm_first=True)
        self.transformer = nn.TransformerEncoder(layer, num_layers=2)
        self.d2 = ConvBlock(base * 6, base * 2)
        self.d1 = ConvBlock(base * 3, base)
        self.out = ConvBlock(base + 2, base)
        self.flow = nn.Conv3d(base, 3, 3, padding=1)
        nn.init.normal_(self.flow.weight, 0, 1e-5)
        nn.init.zeros_(self.flow.bias)

    def forward(self, moving: torch.Tensor, fixed: torch.Tensor) -> torch.Tensor:
        inputs = torch.cat((moving, fixed), 1)
        a = self.e1(inputs)
        b = self.e2(a)
        c = self.e3(b)
        shape = c.shape
        tokens = c.flatten(2).transpose(1, 2)
        x = self.transformer(tokens).transpose(1, 2).reshape(shape)
        x = self.d2(torch.cat((F.interpolate(x, size=b.shape[2:], mode="trilinear", align_corners=True), b), 1))
        x = self.d1(torch.cat((F.interpolate(x, size=a.shape[2:], mode="trilinear", align_corners=True), a), 1))
        x = self.out(torch.cat((F.interpolate(x, size=inputs.shape[2:], mode="trilinear", align_corners=True), inputs), 1))
        return torch.tanh(self.flow(x)) * self.max_disp


def build_model(name: str, base: int, max_disp: float) -> nn.Module:
    if name == "voxelmorph":
        return VoxelMorphNet(base, max_disp)
    if name == "transmorph":
        return TransMorphLite(base, max_disp)
    raise ValueError(name)


def correlation_loss(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    av = a - a.mean()
    bv = b - b.mean()
    return 1 - (av * bv).mean() / (av.square().mean().sqrt() * bv.square().mean().sqrt() + 1e-6)


def gradient_image(x: torch.Tensor) -> torch.Tensor:
    dz = F.pad(x[:, :, 1:] - x[:, :, :-1], (0, 0, 0, 0, 0, 1))
    dy = F.pad(x[:, :, :, 1:] - x[:, :, :, :-1], (0, 0, 0, 1))
    dx = F.pad(x[:, :, :, :, 1:] - x[:, :, :, :, :-1], (0, 1))
    return torch.sqrt(dx.square() + dy.square() + dz.square() + 1e-6)


def smoothness(flow: torch.Tensor) -> torch.Tensor:
    return ((flow[:, :, 1:] - flow[:, :, :-1]).square().mean() +
            (flow[:, :, :, 1:] - flow[:, :, :, :-1]).square().mean() +
            (flow[:, :, :, :, 1:] - flow[:, :, :, :, :-1]).square().mean()) / 3


def augment_intensity(x: torch.Tensor) -> torch.Tensor:
    gamma = 0.8 + 0.4 * random.random()
    gain = 0.85 + 0.3 * random.random()
    noise = torch.randn_like(x) * (0.015 * random.random())
    return torch.clamp(gain * x.clamp_min(0).pow(gamma) + noise, 0, 1)


def choose_device(requested: str) -> torch.device:
    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    return torch.device(requested)


def prepare_pair(template_path: Path, fixed_path: Path, shape: tuple[int, int, int]) -> tuple[torch.Tensor, torch.Tensor]:
    moving = tensor_volume(robust_normalize(read_volume(template_path)), shape)
    fixed = tensor_volume(robust_normalize(read_volume(fixed_path)), shape)
    return moving, fixed


def train(args: argparse.Namespace) -> None:
    seed_everything(args.seed)
    device = choose_device(args.device)
    shape = tuple(args.shape)
    moving, fixed = prepare_pair(args.template, args.fixed, shape)
    moving, fixed = moving.to(device), fixed.to(device)
    model = build_model(args.model, args.base_channels, args.max_disp).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.learning_rate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    history = []
    start = time.perf_counter()
    model.train()
    for step in range(1, args.steps + 1):
        optimizer.zero_grad(set_to_none=True)
        fixed_aug = augment_intensity(fixed)
        flow = model(moving, fixed_aug)
        moved = warp(moving, flow)
        intensity = correlation_loss(moved, fixed_aug)
        edges = correlation_loss(gradient_image(moved), gradient_image(fixed_aug))
        regularity = smoothness(flow)
        loss = intensity + args.edge_weight * edges + args.smooth_weight * regularity
        loss.backward()
        optimizer.step()
        row = {"step": step, "loss": loss.detach().item(), "ncc_loss": intensity.detach().item(),
               "edge_loss": edges.detach().item(), "smoothness": regularity.detach().item()}
        history.append(row)
        if step == 1 or step % args.log_every == 0 or step == args.steps:
            print(json.dumps(row), flush=True)
    elapsed = time.perf_counter() - start
    checkpoint = {
        "state_dict": model.state_dict(), "model": args.model, "shape": shape,
        "base_channels": args.base_channels, "max_disp": args.max_disp,
        "template": str(args.template.resolve()), "training_fixed": str(args.fixed.resolve()),
        "steps": args.steps, "seed": args.seed, "training_seconds": elapsed,
        "device": str(device), "torch_version": torch.__version__,
    }
    torch.save(checkpoint, args.output)
    with args.output.with_suffix(".training.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=history[0].keys())
        writer.writeheader(); writer.writerows(history)
    print(json.dumps({"checkpoint": str(args.output), "training_seconds": elapsed, "device": str(device)}, indent=2))


def write_tiff(path: Path, array: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, array, bigtiff=array.nbytes >= 2**32, compression="zlib",
                     photometric="minisblack", metadata={"axes": "ZYX"})


def infer(args: argparse.Namespace) -> None:
    end_to_end_start = time.perf_counter()
    device = choose_device(args.device)
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    shape = tuple(checkpoint["shape"])
    model = build_model(checkpoint["model"], checkpoint["base_channels"], checkpoint["max_disp"])
    model.load_state_dict(checkpoint["state_dict"])
    model.to(device).eval()
    original_fixed = read_volume(args.fixed)
    moving, fixed = prepare_pair(args.template, args.fixed, shape)
    labels_np = read_volume(args.atlas)
    labels = tensor_volume(labels_np.astype(np.float32), shape, "nearest")
    moving, fixed, labels = moving.to(device), fixed.to(device), labels.to(device)
    times = []
    with torch.inference_mode():
        for _ in range(args.warmup):
            _ = model(moving, fixed)
        for _ in range(args.repeats):
            start = time.perf_counter()
            flow = model(moving, fixed)
            moved = warp(moving, flow)
            moved_labels = warp(labels, flow, mode="nearest")
            if device.type == "cuda": torch.cuda.synchronize()
            times.append(time.perf_counter() - start)
    full_shape = tuple(original_fixed.shape)
    moved_full = F.interpolate(moved.cpu(), size=full_shape, mode="trilinear", align_corners=True)[0, 0].numpy()
    label_full = F.interpolate(moved_labels.cpu(), size=full_shape, mode="nearest")[0, 0].numpy()
    template_raw = read_volume(args.template)
    scale = float(np.percentile(template_raw[template_raw > 0], 99.5)) if np.any(template_raw > 0) else 1.0
    moved_u16 = np.clip(moved_full * scale, 0, np.iinfo(np.uint16).max).astype(np.uint16)
    max_label = int(np.rint(label_full).max())
    label_dtype = np.uint16 if max_label <= np.iinfo(np.uint16).max else np.uint32
    labels_out = np.rint(label_full).astype(label_dtype)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.fixed.name
    for suffix in (".tiff", ".tif"):
        if stem.lower().endswith(suffix): stem = stem[:-len(suffix)]
    registered = args.output_dir / f"{stem}_registered_CCFv3_template.tif"
    annotation = args.output_dir / f"{stem}_annotation.tif"
    write_tiff(registered, moved_u16); write_tiff(annotation, labels_out)
    end_to_end_seconds = time.perf_counter() - end_to_end_start
    report = {
        "model": checkpoint["model"], "checkpoint": str(args.checkpoint), "device": str(device),
        "network_shape_zyx": shape, "output_shape_zyx": full_shape,
        "inference_seconds": times, "median_inference_seconds": float(np.median(times)),
        "end_to_end_seconds_including_io": end_to_end_seconds,
        "registered_template": str(registered), "annotation": str(annotation),
        "parameter_count": sum(p.numel() for p in model.parameters()),
        "host": platform.node(), "python": sys.version, "torch": torch.__version__,
    }
    (args.output_dir / "inference_benchmark.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--template", type=Path, required=True)
    common.add_argument("--fixed", type=Path, required=True)
    common.add_argument("--device", default="auto", choices=("auto", "cpu", "mps", "cuda"))
    t = sub.add_parser("train", parents=[common])
    t.add_argument("--model", choices=("voxelmorph", "transmorph"), required=True)
    t.add_argument("--output", type=Path, required=True)
    t.add_argument("--shape", type=int, nargs=3, default=(96, 48, 80), metavar=("Z", "Y", "X"))
    t.add_argument("--steps", type=int, default=100)
    t.add_argument("--base-channels", type=int, default=8)
    t.add_argument("--max-disp", type=float, default=12.0)
    t.add_argument("--learning-rate", type=float, default=1e-3)
    t.add_argument("--edge-weight", type=float, default=0.25)
    t.add_argument("--smooth-weight", type=float, default=0.02)
    t.add_argument("--seed", type=int, default=17)
    t.add_argument("--log-every", type=int, default=10)
    i = sub.add_parser("infer", parents=[common])
    i.add_argument("--atlas", type=Path, required=True)
    i.add_argument("--checkpoint", type=Path, required=True)
    i.add_argument("--output-dir", type=Path, required=True)
    i.add_argument("--warmup", type=int, default=1)
    i.add_argument("--repeats", type=int, default=5)
    return p


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    parsed = parser().parse_args()
    train(parsed) if parsed.command == "train" else infer(parsed)
