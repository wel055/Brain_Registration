import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile

from registration_benchmark import volume_io


class VolumeIoTests(unittest.TestCase):
    def test_nifti_and_vtk_preserve_intensity_values(self):
        array = (np.arange(5 * 7 * 9, dtype=np.uint16).reshape(5, 7, 9) % 101).astype(np.uint16)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.tif"
            nifti = root / "volume.nii.gz"
            vtk = root / "volume.vtk"
            tifffile.imwrite(source, array, photometric="minisblack")

            volume_io.tiff_to_nifti(source, nifti, "intensity", (1.0, 1.0, 1.0))
            volume_io.tiff_to_vtk(source, vtk, "intensity", (1.0, 1.0, 1.0))

            vtk_array = volume_io.read_vtk(vtk)
            np.testing.assert_array_equal(vtk_array, array.astype(np.float32))

    def test_vtk_preserves_large_annotation_ids(self):
        array = np.array([0, 1, 70000, 614454272], dtype=np.uint32).reshape(1, 2, 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "annotation.tif"
            vtk = root / "annotation.vtk"
            tifffile.imwrite(source, array, photometric="minisblack")

            volume_io.tiff_to_vtk(source, vtk, "labels", (1.0, 1.0, 1.0))

            np.testing.assert_array_equal(volume_io.read_vtk(vtk), array)


if __name__ == "__main__":
    unittest.main()
