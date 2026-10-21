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


def test_inplace_full_normalization_preserves_reference_float32_arithmetic():
    import cv2
    rng = np.random.default_rng(41)
    depth = rng.random((48,80),dtype=np.float32)*20
    frame = np.zeros((48,80,3),np.uint8)
    lo,hi = np.percentile(depth,[2,98]).astype(np.float32)
    reference = cv2.bilateralFilter(np.clip((depth-lo)/(hi-lo),0,1).astype(np.float32),5,.08,3)
    np.testing.assert_array_equal(DepthNormalizer().apply(depth,frame),reference)


def test_sampled_bounds_are_finite_and_reset_on_scene_cut():
    depth = np.broadcast_to(np.linspace(0,1,1024,dtype=np.float32),(512,1024)).copy()
    frame = np.zeros((512,1024,3),np.uint8)
    normalizer = DepthNormalizer(sampled=True)
    out = normalizer.apply(depth,frame)
    assert np.isfinite(out).all() and out.min() >= 0 and out.max() <= 1
    normalizer.apply(depth+100,frame+255)
    assert normalizer.cuts == 1 and normalizer.bounds[0] > 100


def test_explicit_cpu_does_not_initialize_unrelated_gpu_plugins():
    class Core:
        @property
        def available_devices(self):
            raise AssertionError("Explicit CPU work enumerated GPU plugins")
        def get_property(self, device, property):
            assert (device, property) == ("CPU", "FULL_DEVICE_NAME")
            return "CPU fixture"
    assert select_device(Core(), "cpu") == "CPU"
