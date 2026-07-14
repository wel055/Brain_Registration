#!/usr/bin/env python
"""Evaluate registration outputs against the fixed experimental sample.

Metrics are intentionally image-level and method-agnostic. They compare the
warped CCF/template image to the fixed sample and summarize warped label output.
When annotation labels are available, the script also writes per-subregion
metrics using each warped atlas label as a region of interest.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import tifffile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXED = PROJECT_ROOT / "CB2_KP2_A1a_A.Ex_488.231.mip4.zlib.tif"
SAMPLE_STEM = "CB2_KP2_A1a_A.Ex_488.231.mip4.zlib"


def read_image(path: Path, stride: int) -> np.ndarray:
    arr = tifffile.imread(str(path))
    arr = np.asarray(arr)
    if stride > 1:
        slicer = tuple(slice(None, None, stride) for _ in range(arr.ndim))
        arr = arr[slicer]
    return arr.astype(np.float32, copy=False)


def read_labels(path: Path, stride: int) -> np.ndarray:
    arr = tifffile.imread(str(path))
    arr = np.asarray(arr)
    if stride > 1:
        slicer = tuple(slice(None, None, stride) for _ in range(arr.ndim))
        arr = arr[slicer]
    return np.rint(arr).astype(np.int64, copy=False)


def robust_normalize(arr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    values = arr[mask]
    if values.size == 0:
        values = arr.reshape(-1)
    lo, hi = np.percentile(values, [1.0, 99.5])
    if hi <= lo:
        hi = lo + 1.0
    out = np.clip(arr, lo, hi)
    out = (out - lo) / (hi - lo)
    return out.astype(np.float32, copy=False)


def corrcoef(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    av = a[mask].astype(np.float64, copy=False)
    bv = b[mask].astype(np.float64, copy=False)
    if av.size < 2:
        return float("nan")
    av = av - av.mean()
    bv = bv - bv.mean()
    denom = np.sqrt(np.sum(av * av) * np.sum(bv * bv))
    if denom == 0:
        return float("nan")
    return float(np.sum(av * bv) / denom)


def mutual_information(a: np.ndarray, b: np.ndarray, mask: np.ndarray, bins: int = 64) -> tuple[float, float]:
    av = a[mask].ravel()
    bv = b[mask].ravel()
    if av.size < 2:
        return float("nan"), float("nan")
    hist, _, _ = np.histogram2d(av, bv, bins=bins, range=[[0, 1], [0, 1]])
    pxy = hist / np.maximum(hist.sum(), 1)
    px = pxy.sum(axis=1)
    py = pxy.sum(axis=0)
    nz = pxy > 0
    mi = np.sum(pxy[nz] * np.log(pxy[nz] / (px[:, None] * py[None, :])[nz]))
    hx = -np.sum(px[px > 0] * np.log(px[px > 0]))
    hy = -np.sum(py[py > 0] * np.log(py[py > 0]))
    nmi = 2.0 * mi / (hx + hy) if (hx + hy) > 0 else float("nan")
    return float(mi), float(nmi)


def gradient_magnitude(arr: np.ndarray) -> np.ndarray:
    grads = np.gradient(arr)
    mag = np.zeros_like(arr, dtype=np.float32)
    for grad in grads:
        mag += grad.astype(np.float32, copy=False) ** 2
    return np.sqrt(mag)


def shifted(mask: np.ndarray, axis: int, offset: int) -> np.ndarray:
    out = np.zeros_like(mask, dtype=bool)
    src = [slice(None)] * mask.ndim
    dst = [slice(None)] * mask.ndim
    if offset > 0:
        src[axis] = slice(None, -offset)
        dst[axis] = slice(offset, None)
    else:
        src[axis] = slice(-offset, None)
        dst[axis] = slice(None, offset)
    out[tuple(dst)] = mask[tuple(src)]
    return out


def dilate_6(mask: np.ndarray) -> np.ndarray:
    out = mask.copy()
    for axis in range(mask.ndim):
        out |= shifted(mask, axis, 1)
        out |= shifted(mask, axis, -1)
    return out


def region_boundary(mask: np.ndarray) -> np.ndarray:
    boundary = np.zeros_like(mask, dtype=bool)
    for axis in range(mask.ndim):
        boundary |= mask & ~shifted(mask, axis, 1)
        boundary |= mask & ~shifted(mask, axis, -1)
    return boundary


def safe_mean(arr: np.ndarray, mask: np.ndarray) -> float:
    if not np.any(mask):
        return float("nan")
    return float(np.mean(arr[mask]))


def evaluate_subregions(
    method: str,
    labels: np.ndarray,
    fixed_mask: np.ndarray,
    moving_mask: np.ndarray,
    f_norm: np.ndarray,
    m_norm: np.ndarray,
    f_edges: np.ndarray,
    m_edges: np.ndarray,
    stride: int,
    min_label_voxels: int,
) -> list[dict]:
    rows = []
    label_ids, counts = np.unique(labels[labels != 0], return_counts=True)
    for label_id, voxel_count in zip(label_ids, counts):
        voxel_count = int(voxel_count)
        if voxel_count < min_label_voxels:
            continue

        region = labels == label_id
        boundary = region_boundary(region)
        boundary_neighborhood = dilate_6(boundary) & ~boundary
        diff = f_norm[region] - m_norm[region]
        mi, nmi = mutual_information(f_norm, m_norm, region)

        boundary_sample_edge_mean = safe_mean(f_edges, boundary)
        local_sample_edge_mean = safe_mean(f_edges, boundary_neighborhood)
        boundary_template_edge_mean = safe_mean(m_edges, boundary)
        local_template_edge_mean = safe_mean(m_edges, boundary_neighborhood)

        eps = 1e-8
        rows.append(
            {
                "method": method,
                "label_id": int(label_id),
                "stride": stride,
                "region_voxels": voxel_count,
                "region_fraction_of_volume": float(voxel_count / labels.size),
                "region_sample_foreground_fraction": float((region & fixed_mask).sum() / voxel_count),
                "region_template_foreground_fraction": float((region & moving_mask).sum() / voxel_count),
                "region_ncc_higher_better": corrcoef(f_norm, m_norm, region),
                "region_mutual_information_higher_better": mi,
                "region_nmi_higher_better": nmi,
                "region_rmse_lower_better": float(np.sqrt(np.mean(diff * diff))),
                "region_mae_lower_better": float(np.mean(np.abs(diff))),
                "region_sample_mean_intensity_norm": safe_mean(f_norm, region),
                "region_template_mean_intensity_norm": safe_mean(m_norm, region),
                "boundary_voxels": int(boundary.sum()),
                "boundary_sample_edge_mean": boundary_sample_edge_mean,
                "boundary_template_edge_mean": boundary_template_edge_mean,
                "boundary_edge_ncc_higher_better": corrcoef(f_edges, m_edges, boundary),
                "boundary_sample_edge_enrichment_higher_better": float(
                    boundary_sample_edge_mean / (local_sample_edge_mean + eps)
                ),
                "boundary_template_edge_enrichment_higher_better": float(
                    boundary_template_edge_mean / (local_template_edge_mean + eps)
                ),
            }
        )
    return rows


def weighted_mean(rows: list[dict], key: str) -> float:
    values = []
    weights = []
    for row in rows:
        value = row.get(key)
        if value is None or not np.isfinite(value):
            continue
        values.append(float(value))
        weights.append(float(row["region_voxels"]))
    if not values:
        return float("nan")
    return float(np.average(values, weights=weights))


def median_metric(rows: list[dict], key: str) -> float:
    values = [float(row[key]) for row in rows if key in row and np.isfinite(row[key])]
    if not values:
        return float("nan")
    return float(np.median(values))


def evaluate_method(
    method: str,
    fixed_path: Path,
    registered_template: Path,
    annotation: Path | None,
    stride: int,
    min_label_voxels: int,
) -> tuple[dict, list[dict]]:
    fixed = read_image(fixed_path, stride)
    moving = read_image(registered_template, stride)
    if fixed.shape != moving.shape:
        raise ValueError(f"{method}: shape mismatch fixed={fixed.shape} registered_template={moving.shape}")

    fixed_mask = fixed > np.percentile(fixed, 5)
    moving_mask = moving > np.percentile(moving, 5)
    mask = fixed_mask | moving_mask

    f_norm = robust_normalize(fixed, mask)
    m_norm = robust_normalize(moving, mask)
    f_edges = gradient_magnitude(f_norm)
    m_edges = gradient_magnitude(m_norm)

    diff = f_norm[mask] - m_norm[mask]
    mi, nmi = mutual_information(f_norm, m_norm, mask)
    edge_ncc = corrcoef(f_edges, m_edges, mask)

    row = {
        "method": method,
        "fixed": str(fixed_path),
        "registered_template": str(registered_template),
        "annotation": str(annotation) if annotation else "",
        "stride": stride,
        "shape": "x".join(map(str, fixed.shape)),
        "n_voxels_evaluated": int(mask.sum()),
        "template_ncc_higher_better": corrcoef(f_norm, m_norm, mask),
        "template_edge_ncc_higher_better": edge_ncc,
        "template_mutual_information_higher_better": mi,
        "template_nmi_higher_better": nmi,
        "template_rmse_lower_better": float(np.sqrt(np.mean(diff * diff))),
        "template_mae_lower_better": float(np.mean(np.abs(diff))),
    }

    subregion_rows: list[dict] = []
    if annotation and annotation.exists():
        labels = read_labels(annotation, stride)
        if labels.shape == fixed.shape:
            nonzero = labels != 0
            row["annotation_nonzero_fraction"] = float(nonzero.mean())
            row["annotation_sample_mask_coverage"] = float((nonzero & fixed_mask).sum() / max(fixed_mask.sum(), 1))
            unique = np.unique(labels[nonzero])
            row["annotation_unique_labels_downsampled"] = int(unique.size)
            subregion_rows = evaluate_subregions(
                method=method,
                labels=labels,
                fixed_mask=fixed_mask,
                moving_mask=moving_mask,
                f_norm=f_norm,
                m_norm=m_norm,
                f_edges=f_edges,
                m_edges=m_edges,
                stride=stride,
                min_label_voxels=min_label_voxels,
            )
            row["subregions_evaluated"] = len(subregion_rows)
            row["subregion_weighted_mean_ncc_higher_better"] = weighted_mean(
                subregion_rows, "region_ncc_higher_better"
            )
            row["subregion_weighted_mean_rmse_lower_better"] = weighted_mean(
                subregion_rows, "region_rmse_lower_better"
            )
            row["subregion_median_boundary_edge_enrichment_higher_better"] = median_metric(
                subregion_rows, "boundary_sample_edge_enrichment_higher_better"
            )
        else:
            row["annotation_shape_warning"] = f"annotation={labels.shape}, fixed={fixed.shape}"

    return row, subregion_rows


def sample_stem(fixed_path: Path) -> str:
    """Derive the output filename stem from the fixed image (matches config.sh)."""
    stem = fixed_path.name
    for ext in (".tif", ".tiff"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem


def default_methods(output_root: Path, stem: str) -> list[tuple[str, Path, Path]]:
    candidates = []
    for method in ["elastix", "ants", "fireants", "emlddmm", "mbrainaligner", "voxelmorph", "transmorph"]:
        base = output_root / method
        reg = base / f"{stem}_registered_CCFv3_template.tif"
        ann = base / f"{stem}_annotation.tif"
        if reg.exists():
            candidates.append((method, reg, ann))
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixed", type=Path, default=DEFAULT_FIXED)
    parser.add_argument("--method", help="Method name for a single explicit evaluation")
    parser.add_argument("--registered-template", type=Path)
    parser.add_argument("--annotation", type=Path)
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / "registration_runs")
    parser.add_argument("--stride", type=int, default=2, help="Downsampling stride for faster evaluation")
    parser.add_argument("--min-label-voxels", type=int, default=50)
    parser.add_argument("--out-csv", type=Path, default=PROJECT_ROOT / "registration_runs" / "evaluation_metrics.csv")
    parser.add_argument("--out-json", type=Path, default=PROJECT_ROOT / "registration_runs" / "evaluation_metrics.json")
    parser.add_argument(
        "--out-subregion-csv",
        type=Path,
        default=PROJECT_ROOT / "registration_runs" / "subregion_alignment_metrics.csv",
    )
    parser.add_argument(
        "--out-subregion-json",
        type=Path,
        default=PROJECT_ROOT / "registration_runs" / "subregion_alignment_metrics.json",
    )
    args = parser.parse_args()

    if args.registered_template:
        if not args.method:
            raise SystemExit("--method is required with --registered-template")
        methods = [(args.method, args.registered_template, args.annotation)]
    else:
        methods = default_methods(args.output_root, sample_stem(args.fixed))

    if not methods:
        raise SystemExit("No registered template outputs found. Run a registration wrapper first.")

    rows = []
    subregion_rows = []
    for method, reg, ann in methods:
        try:
            row, regions = evaluate_method(
                method,
                args.fixed,
                reg,
                ann if ann and ann.exists() else None,
                args.stride,
                args.min_label_voxels,
            )
        except ValueError as exc:
            print(f"Skipping {method}: {exc}")
            continue
        rows.append(row)
        subregion_rows.extend(regions)

    if not rows:
        raise SystemExit("No complete registration outputs could be evaluated.")

    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row.keys()})
    with args.out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    args.out_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    if subregion_rows:
        args.out_subregion_csv.parent.mkdir(parents=True, exist_ok=True)
        subregion_fieldnames = sorted({key for row in subregion_rows for key in row.keys()})
        with args.out_subregion_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=subregion_fieldnames)
            writer.writeheader()
            writer.writerows(subregion_rows)
        args.out_subregion_json.write_text(json.dumps(subregion_rows, indent=2), encoding="utf-8")
    for row in rows:
        print(json.dumps(row, indent=2))
    print(f"\nWrote: {args.out_csv}")
    print(f"Wrote: {args.out_json}")
    if subregion_rows:
        print(f"Wrote: {args.out_subregion_csv}")
        print(f"Wrote: {args.out_subregion_json}")


if __name__ == "__main__":
    main()
