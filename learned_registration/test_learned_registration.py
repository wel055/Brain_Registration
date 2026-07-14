import unittest

import torch

from learned_registration.learned_registration import TransMorphLite, VoxelMorphNet, warp


class LearnedRegistrationTests(unittest.TestCase):
    def test_zero_flow_is_identity(self):
        image = torch.rand(1, 1, 8, 10, 12)
        flow = torch.zeros(1, 3, 8, 10, 12)
        self.assertTrue(torch.allclose(warp(image, flow), image, atol=1e-6))

    def test_models_return_dense_three_axis_flow(self):
        moving = torch.rand(1, 1, 16, 16, 16)
        fixed = torch.rand(1, 1, 16, 16, 16)
        for model in (VoxelMorphNet(base=4), TransMorphLite(base=4)):
            flow = model(moving, fixed)
            self.assertEqual(flow.shape, (1, 3, 16, 16, 16))
            self.assertTrue(torch.isfinite(flow).all())


if __name__ == "__main__":
    unittest.main()
