# Brain Registration Project: Speaker Notes

Target length: 7-9 minutes, roughly 35-50 seconds per slide.

## Slide 1 - Project framing
I am building a reproducible pipeline that maps 3D LSFM brain volumes into a common Allen CCFv3 anatomical framework. The current comparison includes Elastix, ANTs, and emlddmm/LDDMM. The goal is not only to produce a visually plausible registration, but to standardize conversion, transformation, validation, and evaluation.

## Slide 2 - Why registration matters
The experimental 488 stack and the CCF template have different shapes and coordinate systems. The annotation atlas supplies anatomical region IDs. By warping the template and labels into sample space, experimental signal can be summarized consistently by brain region while preserving the original sample volume.

## Slide 3 - Shared pipeline
All methods receive exactly the same fixed sample, template, and atlas. Validated conversion is critical: an earlier c3d conversion reduced the sample from 35.6 million nonzero voxels to only 182. The new pipeline verifies shape, values, finiteness, and nonzero content. Heavy I/O is performed on the external SSD, then validated results are copied once to the NAS.

## Slide 4 - Three methods
Elastix provides an affine plus B-spline baseline and is highly configurable. ANTs combines rigid, affine, and SyN diffeomorphic registration and currently performs best. emlddmm implements a large-deformation velocity-field model and provides explicit deformation products, but its current intensity agreement is lower.

## Slide 5 - Main method
ANTs is the main method at this stage. It first corrects pose, then estimates affine differences, and finally computes a smooth nonlinear SyN deformation. The same composite transform is applied to the annotation atlas with nearest-neighbor interpolation so region IDs are not blended.

## Slide 6 - Benchmark
The benchmark uses the same downsampling stride for all methods. NCC, edge NCC, mutual information, and NMI are higher-is-better. RMSE and MAE are lower-is-better. Per-subregion metrics evaluate each warped label independently. ANTs leads global NCC at 0.8558 and weighted regional NCC at 0.3765.

## Slide 7 - Visual QC
These overlays show the sample in green/cyan and the warped CCF template in magenta. Pale or white structures indicate overlap. The same slice and scaling are used across methods. Quantitative ranking is useful, but boundaries and the full z-stack still require expert visual inspection.

## Slide 8 - Interactive 3D result
Click the brain image to open the browser-based WebGL viewer. Drag to rotate, scroll to zoom, and shift-drag to pan. Before presenting locally, run `python -m http.server 8000` from the project folder. Google Slides cannot contain a live 3D canvas, so the HTML must be hosted at a public URL when presenting from another computer.

## Slide 9 - Implementation and speed
The wrappers use multiresolution pyramids rather than optimizing every parameter at full resolution from the beginning. ANTs uses shrink factors 12, 8, 4, and 2 for rigid/affine stages and 10, 6, 4, 2, and 1 for SyN. emlddmm uses 16-fold then 8-fold downsampling. The experimental sample remains the fixed reference and is never resampled in the forward result; the smaller CCF template and labels move into sample space. The current wrappers do not explicitly split and restitch chunks. A tiled implementation would require overlap halos and blended stitching to prevent seams.

## Slide 10 - Conclusion
The project now has a validated, reproducible three-method benchmark and a storage-safe execution pattern. ANTs is the current quantitative lead. The next scientific step is to add manual landmarks or expert reference segmentations, inspect low-scoring regions, and test whether a structural or autofluorescence channel improves registration.
