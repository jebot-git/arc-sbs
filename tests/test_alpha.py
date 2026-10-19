import cv2
import numpy as np
import pytest
from arc_sbs.alpha import resize_image, half_width
from arc_sbs.stereo import render_sbs
from arc_sbs.image_worker import ImageConverter


def cutout():
    frame = np.zeros((16, 80, 4), np.uint8)
    frame[:, :, :3] = [255, 0, 255]  # arbitrary hidden RGB must not darken edges
    frame[4:12, 20:60] = [0, 0, 255, 128]
    frame[6:10, 30:50] = [0, 255, 0, 255]
    return frame


def test_zero_disparity_preserves_both_alpha_views():
    frame = cutout()
    result = render_sbs(frame, np.full((16, 80), .5, np.float32), strength=0, layout='full')
    np.testing.assert_array_equal(result[:, :80], frame)
    np.testing.assert_array_equal(result[:, 80:], frame)


def test_invisible_near_pixels_cannot_occlude_shifted_visible_subject():
    frame = np.zeros((2, 80, 4), np.uint8)
    frame[:, 20] = [0, 0, 255, 128]
    depth = np.ones((2, 80), np.float32)  # invisible background falsely predicted near
    depth[:, 20] = .5
    result = render_sbs(frame, depth, strength=.1, convergence=0, layout='full')
    np.testing.assert_array_equal(result[:, 22], [[0, 0, 255, 128]] * 2)
    np.testing.assert_array_equal(result[:, 80+18], [[0, 0, 255, 128]] * 2)
    assert (result[:, :10, 3] == 0).all()
    assert (result[:, 70:80, 3] == 0).all()


def test_half_sbs_and_resizing_filter_premultiplied_colors():
    frame = np.array([[[0, 0, 255, 255], [255, 255, 0, 0]]]*2, np.uint8)
    half = half_width(frame)
    np.testing.assert_array_equal(half, np.array([[[0, 0, 255, 128]]]*2, np.uint8))
    expanded = resize_image(frame, (8, 2), cv2.INTER_CUBIC)
    visible = expanded[:, :, 3] > 0
    assert (expanded[:, :, :3][visible] == [0, 0, 255]).all()


@pytest.mark.parametrize('layout', ['full-sbs', 'half-sbs'])
@pytest.mark.parametrize('swap', [False, True])
def test_existing_stereo_preserves_alpha_without_model(tmp_path, layout, swap):
    frame = cutout()
    source = tmp_path / 'image.png'; output = tmp_path / 'out.png'
    pair = np.concatenate((frame, frame[:, ::-1]), axis=1)
    cv2.imwrite(str(source), pair)
    metadata = {'stereo-layout': layout, 'stereo-order': 'right_left' if swap else 'left_right'}
    converter = ImageConverter(tmp_path / 'missing.xml')
    report = converter.convert(source, output, metadata=metadata)
    assert report['depth_bypassed'] and converter.estimator is None
    actual = cv2.imread(str(output), cv2.IMREAD_UNCHANGED)
    left, right = (frame[:, ::-1], frame) if swap else (frame, frame[:, ::-1])
    if layout == 'half-sbs':
        left = resize_image(left, (160, 16), cv2.INTER_CUBIC)
        right = resize_image(right, (160, 16), cv2.INTER_CUBIC)
    np.testing.assert_array_equal(actual, np.concatenate((left, right), axis=1))


@pytest.mark.parametrize('renderer', ['cpu', 'opencl'])
def test_depth_conversion_keeps_alpha_and_sends_only_composited_rgb_to_model(tmp_path, renderer):
    class Estimator:
        device_name = 'test RGB estimator'
        height, width = 16, 80
        def infer(self, rgb):
            assert rgb.shape == (16, 80, 3)
            assert (rgb[:4] == 0).all()
            return np.full((16, 80), .5, np.float32)
    source = tmp_path / 'image.png'; output = tmp_path / 'out.png'
    frame = cutout(); cv2.imwrite(str(source), frame)
    converter = ImageConverter(renderer=renderer)
    converter.estimator = Estimator()
    report = converter.convert(source, output, strength=0)
    assert not report['depth_bypassed']
    result = cv2.imread(str(output), cv2.IMREAD_UNCHANGED)
    np.testing.assert_array_equal(result[:, :80], frame)
    np.testing.assert_array_equal(result[:, 80:], frame)


def test_fully_transparent_image_stays_invisible_after_disparity():
    frame = np.full((16, 80, 4), 255, np.uint8)
    frame[:, :, 3] = 0
    for layout in ('half', 'full'):
        result = render_sbs(frame, np.ones((16, 80), np.float32), strength=.1, layout=layout)
        assert not result[:, :, 3].any()


def test_existing_16bit_rgba_keeps_coverage_in_8bit_output(tmp_path):
    image = np.full((16, 80, 4), 65535, np.uint16)
    image[:, :20, 3] = 0
    image[:, 20:60, 3] = 128 * 257
    source, output = tmp_path / 'pair_SBS.png', tmp_path / 'out.png'
    cv2.imwrite(str(source), image)
    ImageConverter(tmp_path / 'missing.xml').convert(source, output)
    result = cv2.imread(str(output), cv2.IMREAD_UNCHANGED)
    assert result.dtype == np.uint8
    np.testing.assert_array_equal(result[:, :, 3], (image[:, :, 3] // 257).astype(np.uint8))
