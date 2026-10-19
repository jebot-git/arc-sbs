import io
import base64
import json
from pathlib import Path

import cv2
import numpy as np
import openvino as ov
from openvino import opset13 as ops
import pytest

from arc_sbs.image_worker import ImageConverter, serve
from arc_sbs import image_worker
from arc_sbs.xr import WayVROutput


def test_cpu_stills_resize_atomic_output_and_independent_bounds(tmp_path):
    x = ops.parameter([1, 3, 28, 42], ov.Type.f32)
    model = tmp_path / 'model.xml'
    ov.save_model(ov.Model([ops.reduce_mean(x, ops.constant([1]), False)], [x]), model)
    converter = ImageConverter(model, device='CPU', renderer='cpu', max_edge=80)
    source, output = tmp_path / 'input.png', tmp_path / 'stereo.png'
    frame = np.random.default_rng(31).integers(0, 256, (63, 101, 3), dtype=np.uint8)
    cv2.imwrite(str(source), frame)
    result = converter.convert(source, output)
    first = cv2.imread(str(output))
    assert first.shape == (50, 160, 3)
    assert result['size'] == [160, 50]
    compiled = converter.estimator
    # Reusing the worker after a different image must not retain video EMA bounds.
    cv2.imwrite(str(source), 255 - frame)
    converter.convert(source, output)
    cv2.imwrite(str(source), frame)
    converter.convert(source, output)
    assert converter.estimator is compiled
    np.testing.assert_array_equal(first, cv2.imread(str(output)))
    assert not list(tmp_path.glob('*.part.png'))
    preserved = output.read_bytes()
    with pytest.raises(ValueError):
        converter.convert(tmp_path / 'missing.png', output)
    assert output.read_bytes() == preserved


def test_protocol_auto_disconnected_skips_and_recovers_from_error(monkeypatch):
    monkeypatch.setattr(image_worker, 'headset_connected', lambda: False)
    class Converter:
        def convert(self, *args):
            raise RuntimeError('expected error')
    class XR:
        closed = False
        def close(self): self.closed = True
        def hide(self): pass
    xr = XR()
    requests = [{'id': 1, 'action': 'convert', 'mode': 'auto'},
                {'id': 2, 'action': 'convert', 'input': 'a', 'output': 'b'},
                {'id': 3, 'action': 'probe'}, {'action': 'quit'}]
    rows = []
    serve(Converter(), io.StringIO('\n'.join(map(json.dumps, requests))), rows.append, xr)
    assert [row['status'] for row in rows] == ['skipped', 'error', 'ok']
    assert rows[1]['id'] == 2 and rows[1]['error'] == 'expected error'
    assert xr.closed


def test_xr_overlay_uses_squeezed_stereo_and_terminates_only_owned_process(monkeypatch):
    xr = WayVROutput()
    calls = []
    def command(*args):
        calls.append(args)
        if args[0] == 'window-list':
            return xr.name if any(c[0] == 'process-launch' for c in calls) else 'other app'
        if args[0] == 'process-launch':
            return '{"user":42}'
        return ''
    monkeypatch.setattr(xr, 'command', command)
    xr.present('/viewer with spaces/qimgv', Path('/tmp/image with spaces.png'))
    assert ('window-attrib', xr.name, 'set', 'stereo', 'left_right') in calls
    assert ('window-attrib', xr.name, 'set', 'stereo_full_frame', 'false') in calls
    launch = next(call for call in calls if call[0] == 'process-launch')
    arguments = launch[-1].split()
    assert arguments[0] == '--sbs-present-encoded'
    assert base64.b64decode(arguments[1]).decode() == '/tmp/image with spaces.png'
    assert arguments[3] == xr.name
    xr.close()
    assert calls[-1] == ('process-terminate', '{"user": 42}')


def test_disconnected_compositor_failure_does_not_lose_connection_state(monkeypatch):
    monkeypatch.setattr(image_worker, 'headset_connected', lambda: False)
    class XR:
        def hide(self): raise RuntimeError('compositor stopped')
        def close(self): pass
    rows = []
    serve(None, io.StringIO('{"action":"probe"}\n'), rows.append, XR())
    assert rows == [{'id': None, 'action': 'probe', 'status': 'ok', 'connected': False}]
