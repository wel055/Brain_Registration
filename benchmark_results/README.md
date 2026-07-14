# Benchmark results

Curated evaluation outputs from the pipeline (metrics + per-subregion reports +
runtime), one folder per sample and method-set. Large binaries (warped TIFFs,
transforms, intermediates) are intentionally excluded — see the run scripts to
regenerate them.

```
benchmark_results/
  sample1_WT_M3/                 20260115_..._AZ4_DB5_P60_WT_M3_A
    elastix_ants_emlddmm/        3-method run
    ants_fireants/               ANTs vs GPU FireANTs run
  sample2_GS_F1/                 20260220_..._AZ4_DB6_P60_GS_F1_A
    elastix_ants_emlddmm/
    ants_fireants/
```

Each folder contains:

| File | What it is |
| --- | --- |
| `evaluation_metrics.csv` / `.json` | whole-brain metrics per method (NCC, edge NCC, MI, NMI, RMSE, MAE, weighted subregion scores) |
| `subregion_metrics.csv` / `.json` | per-region metrics (each warped atlas label as an ROI) |
| `subregion_report.csv` / `.html` | named per-region × per-method comparison tables (Allen CCF region names) |
| `timings.csv` / `.json` | per-method registration wall-time (seconds) |
| `RUN_NOTES.txt` | run configuration / provenance |

Metrics are **intensity-agreement proxies**, not overlap-vs-ground-truth — see
the top-level `README.md` (Evaluation section) for how each is computed and its
caveats.
