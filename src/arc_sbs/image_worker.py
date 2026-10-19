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

from .paths import model_path
from .xr import WayVROutput, headset_connected


class ImageConverter:
    def __init__(self, model=None, device='ARC', renderer='opencl', max_edge=3840):
        self.model = model or model_path()
        self.device = device
        self.renderer_name = renderer
        self.max_edge = max_edge
        self.estimator = None
        self.renderer = None

    def convert(self, source, output, strength=.025, convergence=.5):
        import cv2
        import numpy as np
        from .depth import DepthEstimator, DepthNormalizer
        started = time.perf_counter()
        frame = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError(f'Cannot decode image: {source}')
        h, w = frame.shape[:2]
        scale = min(1, self.max_edge / max(w, h))
        width, height = max(2, round(w * scale / 2) * 2), max(2, round(h * scale / 2) * 2)
        if (width, height) != (w, h):
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
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
        depth = DepthNormalizer(smoothing=0, sampled=True).apply(self.estimator.infer(frame), frame)
        sbs = self.renderer.render(frame, depth, strength=strength, convergence=convergence, layout='full')
        output = Path(output)
        temporary = output.with_name(output.name + '.part.png')
        try:
            if not cv2.imwrite(str(temporary), np.ascontiguousarray(sbs), [cv2.IMWRITE_PNG_COMPRESSION, 1]):
                raise RuntimeError('Cannot write SBS image')
            temporary.replace(output)
        finally:
            temporary.unlink(missing_ok=True)
        return {'output': str(output), 'size': [sbs.shape[1], sbs.shape[0]],
                'device': self.estimator.device_name, 'model': str(self.model),
                'model_shape': [self.estimator.height, self.estimator.width],
                'renderer': self.renderer.info, 'seconds': time.perf_counter() - started}


def serve(converter, stream, emit, xr=None):
    xr = xr or WayVROutput()
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
                    result['connected'] = headset_connected()
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
                    connected = headset_connected()
                    result['connected'] = connected
                    if connected:
                        target = Path(request['display'])
                        temporary = target.with_name(target.name + '.new')
                        shutil.copyfile(request['output'], temporary)
                        temporary.replace(target)
                        result['overlay'] = xr.present(request['presenter'], target)
                elif action == 'convert':
                    connected = headset_connected()
                    result['connected'] = connected
                    if request.get('mode') == 'auto' and not connected:
                        result['status'] = 'skipped'
                    else:
                        result.update(converter.convert(request['input'], request['output']))
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
    parser.add_argument('--device', default='ARC')
    parser.add_argument('--renderer', choices=['opencl', 'cpu'], default='opencl')
    args = parser.parse_args()
    protocol = sys.stdout
    def emit(value):
        protocol.write(json.dumps(value) + '\n')
        protocol.flush()
    with contextlib.redirect_stdout(sys.stderr):
        serve(ImageConverter(**vars(args)), sys.stdin, emit)


if __name__ == '__main__':
    main()
