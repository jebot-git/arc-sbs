"""Persistent JSON-lines still-image converter for native image viewers.

Stdout is protocol-only. One request at a time; qimgv coalesces navigation while
inference runs. A separate present command prevents obsolete results reaching XR.
"""
import argparse
import contextlib
import json
from pathlib import Path
import shutil
import sys
import time

from .backend import BACKEND, DEFAULT_DEVICE, DEFAULT_ENCODER
from .paths import model_path
from .image_layout import detect_layout, file_metadata
from .xr import WayVROutput, headset_connected
from .alpha import resize_image


class ImageConverter:
    def __init__(self, model=None, device=DEFAULT_DEVICE, renderer='opencl', max_edge=3840):
        self.model = model or model_path()
        self.device = device
        self.renderer_name = renderer
        self.max_edge = max_edge
        self.estimator = None
        self.renderer = None

    def convert(self, source, output, strength=.025, convergence=.5,
                source_name='', metadata=None, input_layout='auto'):
        import cv2
        import numpy as np
        from .depth import DepthEstimator, DepthNormalizer
        started = time.perf_counter()
        frame = cv2.imread(str(source), cv2.IMREAD_UNCHANGED)
        if frame is None:
            raise ValueError(f'Cannot decode image: {source}')
        if frame.dtype == np.uint16:
            frame = np.rint(frame.astype(np.float32) / 257).astype(np.uint8)
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] not in (3, 4):
            raise ValueError('Expected an 8/16-bit RGB or RGBA still image')
        metadata = {**file_metadata(source_name or str(source)), **(metadata or {})}
        layout, swap, reason = detect_layout(source_name or str(source), metadata, input_layout)
        if layout != '2d':
            if input_layout == 'auto' and metadata.get('stereo-eye-width'):
                eye = max(1, round(frame.shape[1] * int(metadata['stereo-eye-width']) / int(metadata['stereo-image-width'])))
                left, right = frame[:, :eye], frame[:, -eye:]
            elif frame.shape[1] % 2:
                raise ValueError('An SBS image must have an even width; select 2D if this is not stereo')
            else:
                left, right = np.split(frame, 2, axis=1)
            if swap:
                left, right = right, left
            if layout == 'half-sbs':
                left = resize_image(left, (left.shape[1] * 2, left.shape[0]), cv2.INTER_CUBIC)
                right = resize_image(right, (right.shape[1] * 2, right.shape[0]), cv2.INTER_CUBIC)
            sbs = np.concatenate((left, right), axis=1)
            self.write(output, sbs)
            return {'output': str(output), 'size': [sbs.shape[1], sbs.shape[0]],
                    'input_layout': layout, 'recognition': reason, 'depth_bypassed': True,
                    'device': 'pre-existing stereo image', 'seconds': time.perf_counter() - started}
        h, w = frame.shape[:2]
        scale = min(1, self.max_edge / max(w, h))
        width, height = max(2, round(w * scale / 2) * 2), max(2, round(h * scale / 2) * 2)
        if (width, height) != (w, h):
            frame = resize_image(frame, (width, height), cv2.INTER_AREA)
        if self.estimator is None:
            self.estimator = DepthEstimator(self.model, self.device)
        if self.renderer is None:
            if self.renderer_name == 'opencl':
                from .gpu_stereo import OpenCLRenderer
                self.renderer = OpenCLRenderer()
            else:
                from .stereo import CPURenderer
                self.renderer = CPURenderer()
        # Each photograph gets independent bounds, including same-scene images.
        # The model consumes RGB only; alpha is coverage, not an extra feature.
        # Hidden RGB under fully transparent pixels must not influence depth.
        rgb = frame[:, :, :3]
        if frame.shape[2] == 4:
            rgb = np.rint(rgb.astype(np.float32) * (frame[:, :, 3:4] / 255)).astype(np.uint8)
        depth = DepthNormalizer(smoothing=0, sampled=True).apply(self.estimator.infer(rgb), rgb)
        sbs = self.renderer.render(frame, depth, strength=strength, convergence=convergence, layout='full')
        self.write(output, sbs)
        return {'output': str(output), 'size': [sbs.shape[1], sbs.shape[0]],
                'input_layout': '2d', 'recognition': reason, 'depth_bypassed': False,
                'device': self.estimator.device_name, 'model': str(self.model),
                'model_shape': [self.estimator.height, self.estimator.width],
                'renderer': self.renderer.info, 'seconds': time.perf_counter() - started}

    @staticmethod
    def write(output, sbs):
        import cv2
        import numpy as np
        output = Path(output)
        temporary = output.with_name(output.name + '.part.png')
        try:
            if not cv2.imwrite(str(temporary), np.ascontiguousarray(sbs), [cv2.IMWRITE_PNG_COMPRESSION, 1]):
                raise RuntimeError('Cannot write SBS image')
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)


def serve(converter, stream, emit, xr=None, connected=None):
    xr = xr or WayVROutput()
    is_connected = connected or headset_connected
    try:
        for line in stream:
            request = {}
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError('Request must be a JSON object')
                request = value
                action = request.get('action', 'convert')
                result = {'id': request.get('id'), 'action': action, 'status': 'ok'}
                if action == 'quit':
                    break
                if action == 'probe':
                    result['connected'] = is_connected()
                    if not result['connected']:
                        # The compositor can disappear as part of disconnection.
                        # Still report disconnected so reconnect triggers a new image.
                        try:
                            xr.hide()
                        except (OSError, RuntimeError):
                            pass
                elif action == 'hide':
                    xr.hide()
                elif action == 'present':
                    active = is_connected()
                    result['connected'] = active
                    if active:
                        target = Path(request['display'])
                        temporary = target.with_name(target.name + '.new')
                        shutil.copyfile(request['output'], temporary)
                        temporary.replace(target)
                        result['overlay'] = xr.present(request['presenter'], target)
                elif action == 'convert':
                    active = is_connected()
                    result['connected'] = active
                    if request.get('mode') == 'auto' and not active:
                        result['status'] = 'skipped'
                    else:
                        options = {key: request[key] for key in ('source_name', 'metadata', 'input_layout') if key in request}
                        result.update(converter.convert(request['input'], request['output'], **options))
                else:
                    raise ValueError(f'Unknown action: {action}')
                emit(result)
            except Exception as exc:
                emit({'id': request.get('id'), 'action': request.get('action'),
                      'status': 'error', 'error': str(exc)})
    finally:
        xr.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=model_path())
    parser.add_argument('--device', default=DEFAULT_DEVICE)
    parser.add_argument('--renderer', choices=['opencl', 'cpu'], default='opencl')
    parser.add_argument('--xr-backend', choices=['auto', 'wayvr', 'domed'], default='auto')
    args = parser.parse_args()
    protocol = sys.stdout
    def emit(value):
        protocol.write(json.dumps(value) + '\n')
        protocol.flush()
    options = vars(args)
    from .domed import output_backend
    backend = output_backend(options.pop('xr_backend'))
    xr = None
    if backend == 'domed':
        from .domed import DomeDOutput
        xr = DomeDOutput()
    with contextlib.redirect_stdout(sys.stderr):
        serve(ImageConverter(**options), sys.stdin, emit, xr, xr.connected if xr else None)


if __name__ == '__main__':
    main()
