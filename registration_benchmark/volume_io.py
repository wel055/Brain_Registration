#!/usr/bin/env python
"""Lossless volume conversion and validation for registration pipelines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import SimpleITK as sitk
import tifffile


VTK_DTYPES = {
    np.dtype("uint8"): "unsigned_char",
    np.dtype("uint16"): "unsigned_short",
    np.dtype("uint32"): "unsigned_int",
    np.dtype("int8"): "char",
    np.dtype("int16"): "short",
    np.dtype("int32"): "int",
    np.dtype("float32"): "float",
    np.dtype("float64"): "double",
}
VTK_DTYPES_REVERSE = {value: key for key, value in VTK_DTYPES.items()}


def volume_stats(array: np.ndarray) -> dict[str, object]:
    array = np.asarray(array)
    finite = np.isfinite(array)
    finite_values = array[finite]
    if finite_values.size == 0:
        minimum = maximum = mean = standard_deviation = float("nan")
    else:
        minimum = float(finite_values.min())
        maximum = float(finite_values.max())
        mean = float(finite_values.mean(dtype=np.float64))
        standard_deviation = float(finite_values.std(dtype=np.float64))
    return {
        "shape_zyx": list(array.shape),
        "dtype": str(array.dtype),
        "finite_fraction": float(finite.mean()),
        "minimum": minimum,
        "maximum": maximum,
        "mean": mean,
        "standard_deviation": standard_deviation,
        "nonzero_voxels": int(np.count_nonzero(array)),
        "nonzero_fraction": float(np.count_nonzero(array) / array.size),
    }


def print_report(operation: str, source: Path | None, output: Path, array: np.ndarray) -> None:
    report = {
        "operation": operation,
        "source": str(source) if source else None,
        "output": str(output),
        **volume_stats(array),
    }
    print(json.dumps(report, indent=2))


def prepare_tiff(path: Path, kind: str) -> np.ndarray:
    array = np.asarray(tifffile.imread(path))
    if array.ndim != 3:
        raise ValueError(f"Expected a 3D TIFF at {path}, found shape {array.shape}")
    if not np.isfinite(array).all():
        raise ValueError(f"Input contains NaN or infinity: {path}")
    if kind == "intensity":
        return array.astype(np.float32, copy=False)
    if not np.issubdtype(array.dtype, np.integer):
        array = np.rint(array)
    maximum = int(array.max())
    if maximum <= np.iinfo(np.uint16).max:
        return array.astype(np.uint16, copy=False)
    return array.astype(np.uint32, copy=False)


def assert_same_values(source: np.ndarray, converted: np.ndarray, label: str) -> None:
    if source.shape != converted.shape:
        raise ValueError(f"{label}: shape changed from {source.shape} to {converted.shape}")
    if not np.isfinite(converted).all():
        raise ValueError(f"{label}: converted volume contains NaN or infinity")
    for z_index in range(source.shape[0]):
        if not np.array_equal(source[z_index], converted[z_index]):
            maximum_error = float(
                np.max(np.abs(source[z_index].astype(np.float64) - converted[z_index].astype(np.float64)))
            )
            raise ValueError(f"{label}: voxel values changed at z={z_index}; max error={maximum_error}")


def tiff_to_nifti(source_path: Path, output_path: Path, kind: str, spacing: tuple[float, ...]) -> None:
    source = prepare_tiff(source_path, kind)
    image = sitk.GetImageFromArray(source)
    image.SetSpacing(tuple(spacing))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sitk.WriteImage(image, str(output_path), useCompression=True)
    converted = sitk.GetArrayFromImage(sitk.ReadImage(str(output_path)))
    assert_same_values(source, converted, "TIFF-to-NIfTI validation")
    print_report("tiff-to-nifti", source_path, output_path, converted)


def write_vtk(path: Path, array: np.ndarray, title: str, spacing: tuple[float, ...]) -> None:
    dtype = np.dtype(array.dtype)
    if dtype not in VTK_DTYPES:
        raise ValueError(f"Unsupported VTK dtype: {dtype}")
    z_size, y_size, x_size = array.shape
    z_spacing, y_spacing, x_spacing = spacing
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# vtk DataFile Version 3.0\n"
        f"{title}\n"
        "BINARY\n"
        "DATASET STRUCTURED_POINTS\n"
        f"DIMENSIONS {x_size} {y_size} {z_size}\n"
        "ORIGIN 0.0 0.0 0.0\n"
        f"SPACING {x_spacing} {y_spacing} {z_spacing}\n"
        f"POINT_DATA {array.size}\n"
        f"SCALARS data_000(b) {VTK_DTYPES[dtype]} 1\n"
        "LOOKUP_TABLE default\n"
    )
    with path.open("wb") as stream:
        stream.write(header.encode("ascii"))
        array.astype(dtype.newbyteorder(">"), copy=False).tofile(stream)
        stream.write(b"\n")


def read_vtk(path: Path) -> np.ndarray:
    with path.open("rb") as stream:
        first_line = stream.readline().decode("ascii").strip()
        if "vtk datafile" not in first_line.lower():
            raise ValueError(f"Not a legacy VTK file: {path}")
        stream.readline()
        if stream.readline().decode("ascii").strip().upper() != "BINARY":
            raise ValueError(f"Only binary VTK is supported: {path}")
        if stream.readline().decode("ascii").strip().upper() != "DATASET STRUCTURED_POINTS":
            raise ValueError(f"Only VTK STRUCTURED_POINTS is supported: {path}")
        dimensions = tuple(map(int, stream.readline().decode("ascii").split()[1:]))
        stream.readline()
        stream.readline()
        point_count = int(stream.readline().decode("ascii").split()[-1])
        scalar_parts = stream.readline().decode("ascii").split()
        if not scalar_parts or scalar_parts[0].upper() != "SCALARS":
            raise ValueError(f"Expected scalar VTK data: {path}")
        dtype = VTK_DTYPES_REVERSE[scalar_parts[2]].newbyteorder(">")
        if not stream.readline().decode("ascii").startswith("LOOKUP_TABLE"):
            raise ValueError(f"Missing VTK lookup table: {path}")
        offset = stream.tell()
    if point_count != int(np.prod(dimensions)):
        raise ValueError(f"VTK point count does not match dimensions: {path}")
    data = np.memmap(path, mode="r", dtype=dtype, offset=offset, shape=(point_count,))
    return np.asarray(data).reshape(dimensions[::-1])


def tiff_to_vtk(source_path: Path, output_path: Path, kind: str, spacing: tuple[float, ...]) -> None:
    source = prepare_tiff(source_path, kind)
    title = "emlddmm annotation atlas volume" if kind == "labels" else "emlddmm intensity volume"
    write_vtk(output_path, source, title, spacing)
    converted = read_vtk(output_path)
    assert_same_values(source, converted, "TIFF-to-VTK validation")
    print_report("tiff-to-vtk", source_path, output_path, converted)


def write_tiff(output_path: Path, array: np.ndarray) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        output_path,
        array,
        bigtiff=array.nbytes >= 2**32,
        compression="zlib",
        photometric="minisblack",
        metadata={"axes": "ZYX"},
    )


def nifti_to_tiff(source_path: Path, output_path: Path, kind: str) -> None:
    array = sitk.GetArrayFromImage(sitk.ReadImage(str(source_path)))
    if array.ndim != 3:
        raise ValueError(f"Expected scalar 3D NIfTI at {source_path}, found shape {array.shape}")
    if kind == "labels":
        array = np.rint(array)
        maximum = int(array.max())
        dtype = np.uint16 if maximum <= np.iinfo(np.uint16).max else np.uint32
        array = array.astype(dtype)
    else:
        array = array.astype(np.float32, copy=False)
    if not np.isfinite(array).all():
        raise ValueError(f"NIfTI contains NaN or infinity: {source_path}")
    write_tiff(output_path, array)
    print_report("nifti-to-tiff", source_path, output_path, array)


def vtk_to_tiff(source_path: Path, output_path: Path, kind: str) -> None:
    array = read_vtk(source_path)
    if kind == "labels":
        array = np.rint(array)
        maximum = int(array.max())
        dtype = np.uint16 if maximum <= np.iinfo(np.uint16).max else np.uint32
        array = array.astype(dtype)
    else:
        array = array.astype(np.float32)
    if not np.isfinite(array).all():
        raise ValueError(f"VTK contains NaN or infinity: {source_path}")
    write_tiff(output_path, array)
    print_report("vtk-to-tiff", source_path, output_path, array)


def inspect_volume(path: Path, require_nonzero: bool) -> None:
    lower_name = path.name.lower()
    if lower_name.endswith((".tif", ".tiff")):
        array = np.asarray(tifffile.imread(path))
    elif lower_name.endswith((".nii", ".nii.gz")):
        array = sitk.GetArrayFromImage(sitk.ReadImage(str(path)))
    elif lower_name.endswith(".vtk"):
        array = read_vtk(path)
    else:
        raise ValueError(f"Unsupported volume format: {path}")
    stats = volume_stats(array)
    if stats["finite_fraction"] != 1.0:
        raise ValueError(f"Volume contains NaN or infinity: {path}")
    if require_nonzero and stats["nonzero_voxels"] == 0:
        raise ValueError(f"Volume is entirely zero: {path}")
    print_report("inspect", None, path, array)


def validate_matrix(path: Path) -> None:
    matrix = np.loadtxt(path, delimiter=",")
    if matrix.shape != (4, 4):
        raise ValueError(f"Expected a 4x4 matrix at {path}, found {matrix.shape}")
    if not np.isfinite(matrix).all():
        raise ValueError(f"Transform matrix contains NaN or infinity: {path}")
    print(json.dumps({"operation": "validate-matrix", "path": str(path), "matrix": matrix.tolist()}, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("tiff-to-nifti", "tiff-to-vtk"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("source", type=Path)
        subparser.add_argument("output", type=Path)
        subparser.add_argument("--kind", choices=("intensity", "labels"), required=True)
        subparser.add_argument("--spacing", nargs=3, type=float, default=(1.0, 1.0, 1.0), metavar=("Z", "Y", "X"))

    for command in ("nifti-to-tiff", "vtk-to-tiff"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("source", type=Path)
        subparser.add_argument("output", type=Path)
        subparser.add_argument("--kind", choices=("intensity", "labels"), required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("path", type=Path)
    inspect_parser.add_argument("--require-nonzero", action="store_true")

    matrix_parser = subparsers.add_parser("validate-matrix")
    matrix_parser.add_argument("path", type=Path)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "tiff-to-nifti":
        tiff_to_nifti(args.source, args.output, args.kind, tuple(args.spacing))
    elif args.command == "tiff-to-vtk":
        tiff_to_vtk(args.source, args.output, args.kind, tuple(args.spacing))
    elif args.command == "nifti-to-tiff":
        nifti_to_tiff(args.source, args.output, args.kind)
    elif args.command == "vtk-to-tiff":
        vtk_to_tiff(args.source, args.output, args.kind)
    elif args.command == "inspect":
        inspect_volume(args.path, args.require_nonzero)
    elif args.command == "validate-matrix":
        validate_matrix(args.path)


if __name__ == "__main__":
    main()
