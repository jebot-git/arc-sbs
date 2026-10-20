"""Build-selected depth inference, shared RGB preprocessing and stabilization."""
from pathlib import Path
import time

import cv2
import numpy as np

from .paths import cache_path
from .backend import BACKEND, DEFAULT_DEVICE

MODEL_ID = "depth-anything/Depth-Anything-V2-Small-hf"
MODEL_REVISION = "5426e4f0f36572d16453bbda7a8389317b1bef99"
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def devices(core=None):
    if core is None and BACKEND != "intel":
        from .torch_depth import torch_devices
        return torch_devices()
    import openvino as ov
    core = core or ov.Core()
    return [{"id": d, "name": core.get_property(d, "FULL_DEVICE_NAME")}
            for d in core.available_devices]


def select_device(core, requested):
    available = devices(core)
    if requested.upper() == "ARC":
        matches = [d for d in available if "A770" in d["name"]]
        if not matches:
            raise ValueError("Arc A770 unavailable; run arc-sbs devices or explicitly use --device CPU")
        return matches[0]["id"]
    requested = requested.upper()
    if requested not in [d["id"] for d in available]:
        raise ValueError(f"Device {requested!r} unavailable; run arc-sbs devices")
    return requested


def preprocess(frame, height, width):
    """Letterbox rather than distort the source; return crop in model coordinates."""
    h, w = frame.shape[:2]
    scale = min(width / w, height / h)
    rw, rh = max(1, round(w * scale)), max(1, round(h * scale))
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    rgb = cv2.resize(rgb, (rw, rh), interpolation=cv2.INTER_CUBIC)
    x, y = (width - rw) // 2, (height - rh) // 2
    rgb = cv2.copyMakeBorder(rgb, y, height-rh-y, x, width-rw-x, cv2.BORDER_REPLICATE)
    tensor = (rgb.astype(np.float32) / 255.0 - MEAN) / STD
    return np.ascontiguousarray(tensor.transpose(2, 0, 1)[None]), (x, y, rw, rh)


class DepthEstimator:
    def __new__(cls, model, device=DEFAULT_DEVICE, cache_dir=None):
        if BACKEND != "intel":
            from .torch_depth import TorchDepthEstimator
            return TorchDepthEstimator(model, device, cache_dir)
        return super().__new__(cls)

    def __init__(self, model, device=DEFAULT_DEVICE, cache_dir=None):
        import openvino as ov
        cache_dir = cache_path() if cache_dir is None else cache_dir
        core = ov.Core()
        self.device = select_device(core, device)
        self.device_name = core.get_property(self.device, "FULL_DEVICE_NAME")
        model = core.read_model(str(model))
        shape = model.input(0).partial_shape
        if not shape.is_static or len(shape) != 4 or list(shape.to_shape())[:2] != [1, 3]:
            raise ValueError("Expected a static [1,3,H,W] RGB depth model; use prepare-model")
        _, _, self.height, self.width = model.input(0).shape
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        start = time.perf_counter()
        self.compiled = core.compile_model(model, self.device, {
            "PERFORMANCE_HINT": "LATENCY", "CACHE_DIR": str(cache_dir),
        })
        self.compile_seconds = time.perf_counter() - start
        self.execution_devices = list(self.compiled.get_property("EXECUTION_DEVICES"))
        self.request = self.compiled.create_infer_request()
        self.output = self.compiled.output(0)

    def infer_native(self, frame):
        tensor, (x, y, w, h) = preprocess(frame, self.height, self.width)
        depth = np.asarray(self.request.infer({0: tensor})[self.output]).squeeze().copy()
        if depth.ndim != 2 or not np.isfinite(depth).all():
            raise ValueError("Model returned invalid depth")
        depth = cv2.resize(depth, (self.width, self.height), interpolation=cv2.INTER_CUBIC)
        return depth[y:y+h, x:x+w]

    def infer(self, frame):
        return cv2.resize(self.infer_native(frame), (frame.shape[1], frame.shape[0]),
                          interpolation=cv2.INTER_CUBIC)


class DepthNormalizer:
    """Smooth percentile bounds, not moving pixels; reset at detected hard cuts.

    Per-pixel EMA without motion compensation creates trailing silhouettes. This
    prototype only smooths the normalization range and spatially filters depth.
    Larger values from the supported model mean nearer surfaces.
    """
    def __init__(self, smoothing=0.85, cut_threshold=0.28, sampled=False):
        self.sampled = sampled
        self.smoothing = smoothing
        self.cut_threshold = cut_threshold
        self.bounds = None
        self.previous = None
        self.cuts = 0

    def apply(self, depth, frame):
        preview = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36)).astype(np.float32) / 255
        cut = self.previous is not None and np.mean(np.abs(preview-self.previous)) > self.cut_threshold
        self.previous = preview
        stride = max(1, int(np.ceil(np.sqrt(depth.size / 65536)))) if self.sampled else 1
        lo, hi = np.percentile(depth[::stride, ::stride], [2, 98])
        current = np.array([lo, hi], dtype=np.float32)
        if self.bounds is None or cut:
            self.bounds = current
            self.cuts += int(cut)
        else:
            self.bounds = self.smoothing*self.bounds + (1-self.smoothing)*current
        lo, hi = self.bounds
        if hi-lo < 1e-6:
            return np.full_like(depth, 0.5, dtype=np.float32)
        normalized = np.subtract(depth, lo, dtype=np.float32)
        np.divide(normalized, hi-lo, out=normalized)
        np.clip(normalized, 0, 1, out=normalized)
        return cv2.bilateralFilter(normalized, 5, 0.08, 3)
