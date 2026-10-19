import numpy as np
import pytest

from arc_sbs.depth import DepthNormalizer, preprocess, select_device


def test_preprocessing_rgb_normalization_and_letterbox():
    image = np.zeros((20, 40, 3), np.uint8)
    image[:] = [0, 0, 255]
    tensor, crop = preprocess(image, 56, 56)
    assert tensor.shape == (1,3,56,56)
    assert crop == (0,14,56,28)
    assert tensor.flags.c_contiguous
    assert tensor[0,0,0,0] == pytest.approx((1-0.485)/0.229)
    assert tensor[0,2,0,0] == pytest.approx(-0.406/0.225)


def test_constant_depth_neutral_and_scene_cut_resets_bounds():
    normalizer = DepthNormalizer()
    frame = np.zeros((20,40,3), np.uint8)
    depth = np.ones((20,40), np.float32)
    assert (normalizer.apply(depth, frame) == 0.5).all()
    depth = np.linspace(0, 100, 800, dtype=np.float32).reshape(20,40)
    normalizer.apply(depth, frame+255)
    assert normalizer.cuts == 1
    np.testing.assert_allclose(normalizer.bounds, [2,98], atol=0.01)


def test_arc_selection_does_not_assume_device_zero():
    class Core:
        available_devices = ["CPU", "GPU.0", "GPU.1"]
        def get_property(self, device, prop):
            return {"CPU":"CPU", "GPU.0":"UHD 770", "GPU.1":"Intel Arc A770"}[device]
    assert select_device(Core(), "ARC") == "GPU.1"
    with pytest.raises(ValueError):
        select_device(Core(), "GPU.2")
