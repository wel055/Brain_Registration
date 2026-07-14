#!/usr/bin/env python3
"""FireANTs GPU registration driver (runs in the isolated `fireants` conda env).

Registers the CCFv3 template (moving) onto the experimental sample (fixed) with
FireANTs' GPU affine + greedy diffeomorphic registration, then warps:
  * the CCF template intensity  -> registered template  (bilinear)
  * the CCF annotation labels    -> warped annotation    (nearest-neighbour)

Label handling note: this build of FireANTs has no fused ops, so its native
segmentation path would force one-hot encoding — infeasible for ~600 Allen
labels with IDs up to ~6e8. Instead we compact-remap the labels to 0..N (exact
in float32), warp with plain nearest-neighbour, and map back to the original IDs.

Inputs/outputs are NIfTI (converted to/from TIFF by run_fireants.sh via volume_io).
Prints an explicit registration wall-time so it can be logged like other methods.
"""

import argparse
import time

import numpy as np
import torch
import SimpleITK as sitk

from fireants.io import Image, BatchedImages
from fireants.registration.affine import AffineRegistration
from fireants.registration.greedy import GreedyRegistration


def parse_ints(s):
    return [int(x) for x in str(s).split(",") if x.strip() != ""]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixed", required=True, help="fixed sample NIfTI")
    ap.add_argument("--template", required=True, help="moving CCF template NIfTI")
    ap.add_argument("--annotation", required=True, help="moving CCF annotation (labels) NIfTI")
    ap.add_argument("--out-template", required=True, help="output warped template NIfTI")
    ap.add_argument("--out-annotation", required=True, help="output warped annotation NIfTI")
    ap.add_argument("--scales", default="4,2,1", help="multi-resolution scales")
    ap.add_argument("--affine-iters", default="200,100,50")
    ap.add_argument("--greedy-iters", default="200,100,50")
    ap.add_argument("--affine-lr", type=float, default=3e-3)
    ap.add_argument("--greedy-lr", type=float, default=0.5)
    ap.add_argument("--cc-kernel", type=int, default=5)
    args = ap.parse_args()

    scales = parse_ints(args.scales)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[fireants] device={device}", flush=True)

    fixed = Image.load_file(args.fixed)
    template = Image.load_file(args.template)
    fb = BatchedImages([fixed])
    tb = BatchedImages([template])

    t0 = time.time()
    affine = AffineRegistration(scales, parse_ints(args.affine_iters), fb, tb,
                                optimizer="Adam", optimizer_lr=args.affine_lr,
                                cc_kernel_size=args.cc_kernel)
    affine.optimize()
    greedy = GreedyRegistration(scales=scales, iterations=parse_ints(args.greedy_iters),
                                fixed_images=fb, moving_images=tb,
                                cc_kernel_size=args.cc_kernel,
                                deformation_type="compositive", smooth_grad_sigma=1,
                                optimizer="adam", optimizer_lr=args.greedy_lr,
                                init_affine=affine.get_affine_matrix().detach())
    greedy.optimize()
    if device == "cuda":
        torch.cuda.synchronize()
    reg_time = time.time() - t0
    print(f"[fireants] registration_seconds={reg_time:.1f}", flush=True)

    # --- warped template (intensity, bilinear) ---
    moved_template = greedy.evaluate(fb, tb)
    greedy.save_moved_images(moved_template, args.out_template)
    print(f"[fireants] wrote {args.out_template}", flush=True)

    # --- warped annotation (labels, nearest via compact remap) ---
    annot_itk = sitk.ReadImage(args.annotation)
    annot = sitk.GetArrayFromImage(annot_itk)
    uniq = np.unique(annot)                        # sorted unique label ids
    compact = np.searchsorted(uniq, annot).astype(np.float32)   # 0..N-1, exact in f32
    compact_itk = sitk.GetImageFromArray(compact)
    compact_itk.CopyInformation(annot_itk)

    annot_img = Image(compact_itk)                 # non-seg -> float array
    annot_img.interpolation_mode = "nearest"       # force NN warp (no one-hot / fused ops)
    ab = BatchedImages([annot_img])
    moved_compact = greedy.evaluate(fb, ab)
    warped_compact = np.rint(moved_compact.squeeze().detach().cpu().numpy()).astype(np.int64)
    warped_compact = np.clip(warped_compact, 0, len(uniq) - 1)
    labels_back = uniq[warped_compact].astype(annot.dtype)

    # geometry: same as the warped template (fixed space). Round-trip the template
    # output to copy its ITK header onto the label volume.
    ref = sitk.ReadImage(args.out_template)
    out_itk = sitk.GetImageFromArray(labels_back)
    out_itk.CopyInformation(ref)
    sitk.WriteImage(out_itk, args.out_annotation, True)
    print(f"[fireants] wrote {args.out_annotation} "
          f"({len(np.unique(labels_back))} labels present)", flush=True)


if __name__ == "__main__":
    main()
