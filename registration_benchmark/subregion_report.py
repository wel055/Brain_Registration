#!/usr/bin/env python3
"""Per-subregion, per-method registration report (Klein-2009-style table).

For every anatomical region (named via the Allen CCFv3 ontology, optionally
aggregated to a coarser level and split into Left/Right hemispheres) and every
registration method (elastix / ANTs / emlddmm), compute intensity-similarity
scores between the warped CCF template and the fixed sample inside that region:

    NCC, Edge NCC, Mutual Information, NMI, RMSE, MAE

Output: a tidy long CSV (region x hemisphere x method) plus an HTML report with
one table per metric (region rows, method columns) laid out like the reference
paper's regional overlap table.

IMPORTANT: these are intensity-agreement PROXIES, not label-overlap accuracy.
Unlike Klein 2009 (Jaccard vs manual ground truth), there is no manual
segmentation of the sample; the numbers say how well template intensity matches
sample intensity inside each warped region, not how well the boundaries match a
gold standard.

Reuses the exact metric functions from evaluate_registration.py.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from evaluate_registration import (  # noqa: E402
    corrcoef, gradient_magnitude, mutual_information, read_image, read_labels,
    robust_normalize, sample_stem,
)

METHODS = ["elastix", "ants", "fireants", "emlddmm"]
METRICS = [
    ("ncc", "NCC", "higher"),
    ("edge_ncc", "Edge NCC", "higher"),
    ("mi", "Mutual Info", "higher"),
    ("nmi", "NMI", "higher"),
    ("rmse", "RMSE", "lower"),
    ("mae", "MAE", "lower"),
]


# --------------------------------------------------------------------------- #
# Allen CCFv3 ontology: id -> name/acronym, and aggregation via structure path
# --------------------------------------------------------------------------- #
def load_ontology(path: Path) -> dict[int, dict]:
    onto = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                sid = int(row["id"])
            except (ValueError, KeyError):
                continue
            path_ids = [int(x) for x in row["structure_id_path"].strip("/").split("/") if x]
            onto[sid] = {
                "acronym": row.get("acronym", str(sid)),
                "name": row.get("safe_name") or row.get("name", str(sid)),
                "path": path_ids,
            }
    return onto


GREY_MATTER_ID = 8  # Allen "Basic cell groups and regions" (grey matter)


def aggregate_id(leaf_id: int, onto: dict[int, dict], level: int) -> int:
    """Map a leaf label to its ancestor at the given ontology depth (0=root)."""
    info = onto.get(int(leaf_id))
    if not info:
        return int(leaf_id)
    path = info["path"]
    return path[level] if level < len(path) else path[-1]


def region_bucket(leaf_id: int, onto: dict[int, dict], level: int, grey_only: bool) -> int:
    """Aggregated region id for a leaf, or 0 to exclude it.

    With grey_only, drops fiber tracts, ventricles, and labels absent from the
    ontology (keeps only descendants of the grey-matter root, id 8).
    """
    info = onto.get(int(leaf_id))
    if grey_only and (not info or GREY_MATTER_ID not in info["path"]):
        return 0
    return aggregate_id(leaf_id, onto, level)


# --------------------------------------------------------------------------- #
# Per-region metrics
# --------------------------------------------------------------------------- #
def region_scores(f_norm, m_norm, f_edges, m_edges, roi) -> dict:
    if roi.sum() < 2:
        return {k: float("nan") for k, _, _ in METRICS}
    diff = f_norm[roi] - m_norm[roi]
    mi, nmi = mutual_information(f_norm, m_norm, roi)
    return {
        "ncc": corrcoef(f_norm, m_norm, roi),
        "edge_ncc": corrcoef(f_edges, m_edges, roi),
        "mi": mi,
        "nmi": nmi,
        "rmse": float(np.sqrt(np.mean(diff * diff))),
        "mae": float(np.mean(np.abs(diff))),
    }


def build_report(args):
    onto = load_ontology(args.ontology)
    stem = sample_stem(args.fixed)
    output_root = args.output_root or (args.run_dir / "registration_runs")

    print(f"Loading fixed sample (stride {args.stride}) ...")
    fixed = read_image(args.fixed, args.stride)
    fixed_mask = fixed > np.percentile(fixed, 5)

    # Left/Right split along the L-R axis (axis 2 after CCF reorientation).
    lr_axis = 2
    mid = fixed.shape[lr_axis] // 2
    coords = np.arange(fixed.shape[lr_axis])
    low_side = coords < mid  # boolean per index along L-R axis
    # nominal naming; hemisphere identity inherits the unresolved L-R chirality
    hemi_masks = {}
    shape = [1, 1, 1]
    shape[lr_axis] = fixed.shape[lr_axis]
    low = low_side.reshape(shape)
    hemi_masks[args.low_side_name] = np.broadcast_to(low, fixed.shape)
    hemi_masks[args.high_side_name] = np.broadcast_to(~low, fixed.shape)

    rows = []  # tidy long records
    for method in METHODS:
        reg = output_root / method / f"{stem}_registered_CCFv3_template.tif"
        ann = output_root / method / f"{stem}_annotation.tif"
        if not (reg.exists() and ann.exists()):
            print(f"  [skip] {method}: outputs not found")
            continue
        print(f"Evaluating {method} ...")
        moving = read_image(reg, args.stride)
        labels = read_labels(ann, args.stride)
        if moving.shape != fixed.shape or labels.shape != fixed.shape:
            print(f"  [skip] {method}: shape mismatch "
                  f"(moving={moving.shape}, labels={labels.shape}, fixed={fixed.shape})")
            continue
        moving_mask = moving > np.percentile(moving, 5)
        mask = fixed_mask | moving_mask
        f_norm = robust_normalize(fixed, mask)
        m_norm = robust_normalize(moving, mask)
        f_edges = gradient_magnitude(f_norm)
        m_edges = gradient_magnitude(m_norm)

        # aggregate leaf labels -> region ids at the chosen level.
        # Allen structure IDs are huge (~1e8), so map via unique-inverse rather
        # than a value-indexed array.
        uniq, inv = np.unique(labels, return_inverse=True)
        agg_uniq = np.array([0 if u == 0 else
                             region_bucket(u, onto, args.level, args.grey_only)
                             for u in uniq], dtype=np.int64)
        agg = agg_uniq[inv].reshape(labels.shape)

        for rid in np.unique(agg):
            if rid == 0:
                continue
            region_mask = agg == rid
            info = onto.get(int(rid), {})
            acr = info.get("acronym", str(rid))
            name = info.get("name", str(rid))
            for hemi, hmask in hemi_masks.items():
                roi = region_mask & hmask
                nvox = int(roi.sum())
                if nvox < args.min_voxels:
                    continue
                sc = region_scores(f_norm, m_norm, f_edges, m_edges, roi)
                rows.append({
                    "region_id": int(rid), "acronym": acr, "name": name,
                    "hemisphere": hemi, "region": f"{hemi}_{acr}",
                    "method": method, "n_voxels": nvox, **sc,
                })

    return rows


# --------------------------------------------------------------------------- #
# Output: tidy CSV + per-metric HTML tables
# --------------------------------------------------------------------------- #
def write_csv(rows, path: Path):
    if not rows:
        raise SystemExit("No regions evaluated; check inputs / --min-voxels.")
    cols = ["region", "region_id", "acronym", "name", "hemisphere", "method",
            "n_voxels"] + [k for k, _, _ in METRICS]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) for c in cols})
    print(f"Wrote {path}")


def write_html(rows, path: Path, level: int):
    # index regions (row) x method (col) per metric
    regions = sorted({r["region"] for r in rows},
                     key=lambda s: (s.split("_", 1)[1], s))
    methods = [m for m in METHODS if any(r["method"] == m for r in rows)]
    lut = {(r["region"], r["method"]): r for r in rows}
    full_name = {r["region"]: r["name"] for r in rows}

    arrow = {"higher": "↑", "lower": "↓"}
    html = ["<h1>Per-subregion registration comparison</h1>",
            f"<p class='note'>One table per registration method; columns are the "
            f"metrics (↑ higher is better, ↓ lower is better). Metrics are "
            f"intensity-agreement <b>proxies</b> (not Jaccard overlap vs ground "
            f"truth). Ontology aggregation level = {level}.</p>"]
    for method in methods:
        html.append(f"<h2>Method: {method}</h2>")
        head = "".join(f"<th>{label} {arrow[better]}</th>" for _, label, better in METRICS)
        html.append("<div class='wrap'><table><thead><tr><th>Region</th>"
                    "<th>Full name</th>" + head + "</tr></thead><tbody>")
        for region in regions:
            r = lut.get((region, method))
            tds = [f"<th class='reg'>{region}</th>",
                   f"<td class='fullname'>{full_name.get(region, '')}</td>"]
            for key, _, _ in METRICS:
                v = r.get(key) if r else None
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    tds.append("<td class='na'>–</td>")
                else:
                    tds.append(f"<td>{v:.4f}</td>")
            html.append("<tr>" + "".join(tds) + "</tr>")
        html.append("</tbody></table></div>")

    style = """
    <style>
      body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1a1a1a}
      h1{font-size:20px} h2{font-size:15px;margin-top:28px}
      .note{color:#555;font-size:13px;max-width:70ch}
      .wrap{overflow-x:auto}
      table{border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}
      th,td{border:1px solid #ddd;padding:3px 8px;text-align:right}
      thead th{background:#f2f2f2;text-align:center}
      th.reg{text-align:left;background:#fafafa;font-weight:600}
      td.fullname{text-align:left;color:#444;white-space:nowrap}
      td.best{background:#d6f5d6;font-weight:700}
      td.na{color:#bbb}
      @media(prefers-color-scheme:dark){
        body{background:#1a1a1a;color:#eee} th,td{border-color:#444}
        thead th{background:#2a2a2a} th.reg{background:#222}
        td.best{background:#1f4d2a} .note{color:#aaa} td.fullname{color:#bbb}}
    </style>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(style + "\n".join(html))
    print(f"Wrote {path}  ({len(regions)} regions x {len(methods)} methods)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="run directory containing registration_runs/")
    ap.add_argument("--fixed", type=Path, required=True, help="fixed sample TIFF")
    ap.add_argument("--output-root", type=Path, default=None,
                    help="override registration_runs location")
    ap.add_argument("--ontology", type=Path, default=HERE / "allen_ccf_structures.csv")
    ap.add_argument("--stride", type=int, default=4)
    ap.add_argument("--level", type=int, default=5,
                    help="Allen ontology depth to aggregate to (0=root; higher=finer; "
                         "5 ~= Allen summary structures)")
    ap.add_argument("--grey-only", dest="grey_only", action="store_true", default=True,
                    help="keep only grey-matter regions (default; drops fiber tracts/ventricles)")
    ap.add_argument("--include-non-grey", dest="grey_only", action="store_false",
                    help="also include fiber tracts, ventricles, and unmapped labels")
    ap.add_argument("--min-voxels", type=int, default=50)
    ap.add_argument("--low-side-name", default="L",
                    help="hemisphere name for the low-index side of the L-R axis")
    ap.add_argument("--high-side-name", default="R")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="where to write the report (default: run-dir)")
    args = ap.parse_args()

    out_dir = args.out_dir or args.run_dir
    rows = build_report(args)
    write_csv(rows, out_dir / "subregion_report.csv")
    write_html(rows, out_dir / "subregion_report.html", args.level)


if __name__ == "__main__":
    main()
