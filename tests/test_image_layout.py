import struct
import zlib

import cv2
import numpy as np
import pytest

from arc_sbs.image_layout import detect_layout, file_metadata
from arc_sbs.image_worker import ImageConverter


@pytest.mark.parametrize('name,layout,swap', [
    ('pair_SBS.jpg', 'full-sbs', False), ('pair.FSBS.png', 'full-sbs', False),
    ('pair-half-sbs.webp', 'half-sbs', False), ('pair_HSBS_RL.png', 'half-sbs', True),
    ('pair.jps', 'full-sbs', True), ('pair_LR.pns', 'full-sbs', False),
    ('panorama.jpg', '2d', False), ('newsbs.png', '2d', False)])
def test_explicit_stereo_markers(name, layout, swap):
    assert detect_layout(name)[:2] == (layout, swap)


def test_metadata_and_manual_override_take_precedence():
    assert detect_layout('pair_hsbs.jpg', {'StereoMode': 'right_left'})[:2] == ('full-sbs', True)
    assert detect_layout('pair_sbs.jpg', {'Stereo': 'mono'})[0] == '2d'
    assert detect_layout('pair_sbs.jpg', requested='2d')[0] == '2d'


def pair():
    image = np.zeros((16, 32, 3), np.uint8)
    image[:, :16] = [0, 0, 255]
    image[:, 16:] = [255, 0, 0]
    return image


def test_existing_sbs_bypasses_missing_model_and_preserves_views(tmp_path):
    source, output = tmp_path / 'pair_SBS.png', tmp_path / 'result.png'
    cv2.imwrite(str(source), pair())
    converter = ImageConverter(tmp_path / 'nonexistent-model.xml')
    report = converter.convert(source, output)
    assert report['depth_bypassed'] is True
    assert converter.estimator is None and converter.renderer is None
    np.testing.assert_array_equal(cv2.imread(str(output)), pair())
    report = converter.convert(source, output, input_layout='half-sbs')
    image = cv2.imread(str(output))
    assert image.shape == (16, 64, 3)
    np.testing.assert_array_equal(image[:, 4], pair()[:, 4])
    np.testing.assert_array_equal(image[:, 40], pair()[:, 20])


def test_jps_marker_and_cross_eyed_extension(tmp_path):
    _, jpeg = cv2.imencode('.jpg', pair())
    # APP3 payload: identifier, descriptor length, separation/flags/layout/type.
    payload = b'_JPSJPS_' + b'\x00\x04' + bytes([0, 4, 2, 1])
    path = tmp_path / 'plain.jpg'
    path.write_bytes(jpeg[:2].tobytes() + b'\xff\xe3' + struct.pack('>H', len(payload) + 2) + payload + jpeg[2:].tobytes())
    assert file_metadata(path)['stereo-order'] == 'left_right'
    output = tmp_path / 'out.png'
    converter = ImageConverter(tmp_path / 'missing.xml')
    assert converter.convert(path, output)['depth_bypassed']
    jps = tmp_path / 'plain.jps'
    jps.write_bytes(jpeg.tobytes())
    converter.convert(jps, output)
    assert cv2.imread(str(output))[8, 4, 0] > 200  # right-first file -> left-first output


def test_png_ster_descriptor_removes_alignment_padding(tmp_path):
    # Two 9-pixel views with the mandated 7-pixel alignment gap.
    image = np.zeros((16, 25, 3), np.uint8)
    image[:, :9] = [0, 0, 255]
    image[:, -9:] = [255, 0, 0]
    _, encoded = cv2.imencode('.png', image)
    data = encoded.tobytes()
    payload = b'sTER' + b'\x01'
    chunk = struct.pack('>I', 1) + payload + struct.pack('>I', zlib.crc32(payload) & 0xffffffff)
    path = tmp_path / 'pair.png'
    path.write_bytes(data[:33] + chunk + data[33:])
    assert file_metadata(path)['stereo-eye-width'] == 9
    output = tmp_path / 'result.png'
    report = ImageConverter(tmp_path / 'missing.xml').convert(path, output)
    assert report['size'] == [18, 16]
    result = cv2.imread(str(output))
    assert (result[:, :9] == [0, 0, 255]).all()
    assert (result[:, 9:] == [255, 0, 0]).all()
