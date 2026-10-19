import numpy as np
import pytest

from arc_sbs.stereo import render_sbs, warp_eye


def image():
    frame = np.full((16, 80, 3), 35, dtype=np.uint8)
    frame[:, 40] = [30, 180, 240]
    return frame


def test_zero_disparity_identity_and_eye_dimensions():
    frame = image()
    d = np.full(frame.shape[:2], 0.5, dtype=np.float32)
    sbs = render_sbs(frame, d, layout="full")
    assert sbs.shape == (16, 160, 3)
    np.testing.assert_array_equal(sbs[:, :80], frame)
    np.testing.assert_array_equal(sbs[:, 80:], frame)
    assert render_sbs(frame, d, layout="half").shape == frame.shape


def test_near_disparity_sign_and_swap():
    frame = image()
    d = np.ones(frame.shape[:2], dtype=np.float32)
    sbs = render_sbs(frame, d, strength=0.1, convergence=0, layout="full")
    assert sbs[0, :80, 1].argmax() == 44
    assert sbs[0, 80:, 1].argmax() == 36
    swapped = render_sbs(frame, d, strength=0.1, convergence=0, layout="full", swap=True)
    np.testing.assert_array_equal(swapped[:, :80], sbs[:, 80:])


def test_nearest_surface_wins_collision():
    frame = np.zeros((2, 6, 3), dtype=np.uint8)
    frame[:, 1] = [0, 255, 0]
    frame[:, 2] = [0, 0, 255]
    depth = np.zeros((2, 6), dtype=np.float32)
    depth[:, 1] = 0.2
    depth[:, 2] = 0.9
    shift = np.zeros((2, 6), dtype=np.float32)
    shift[:, 1] = 1
    result = warp_eye(frame, depth, shift)
    np.testing.assert_array_equal(result[:, 2], [[0,0,255]]*2)


def test_disocclusion_uses_background_and_preserves_valid_black():
    frame = np.full((2, 8, 3), 40, dtype=np.uint8)
    frame[:, 3:5] = 200
    frame[:, 0] = 0
    depth = np.zeros((2, 8), dtype=np.float32)
    depth[:, 3:5] = 1
    shift = np.zeros((2, 8), dtype=np.float32)
    shift[:, 3:5] = 2
    result = warp_eye(frame, depth, shift)
    assert (result[:, 3:5] == 40).all()
    assert (result[:, 5:7] == 200).all()
    assert (result[:, 0] == 0).all()


def test_invalid_depth_and_parameters_rejected():
    frame = image()
    with pytest.raises(ValueError):
        render_sbs(frame, np.full((16,80), np.nan))
    with pytest.raises(ValueError):
        render_sbs(frame, np.ones((16,80)), strength=-0.1)
