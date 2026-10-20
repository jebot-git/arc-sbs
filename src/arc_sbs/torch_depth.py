"""Experimental CUDA / ROCm inference using the pinned model's fixed-shape export."""
import json
from pathlib import Path
import time

import cv2
import numpy as np

from .backend import BACKEND, DEFAULT_DEVICE
from .depth import MODEL_ID, MODEL_REVISION, preprocess


def torch_runtime():
    import torch
    expected = "hip" if BACKEND == "amd" else "cuda"
    if not getattr(torch.version, expected, None) or (BACKEND == "nvidia" and torch.version.hip):
        raise RuntimeError(f"This {BACKEND} build requires PyTorch {'ROCm' if expected == 'hip' else 'CUDA'}; install its matching bundle")
    return torch


def torch_devices():
    torch = torch_runtime()
    return [{"id": "CPU", "name": "CPU"}] + [
        {"id": f"GPU.{i}", "name": torch.cuda.get_device_name(i)}
        for i in range(torch.cuda.device_count())]


def select_torch_device(torch, requested):
    requested = requested.upper()
    if requested == "CPU":
        return "cpu"
    if requested == DEFAULT_DEVICE:
        index = 0
    elif requested.startswith("GPU.") and requested[4:].isdigit():
        index = int(requested[4:])
    else:
        raise ValueError(f"Use {DEFAULT_DEVICE}, GPU.N or CPU with this {BACKEND} build")
    if not torch.cuda.is_available() or index >= torch.cuda.device_count():
        raise ValueError(f"{BACKEND} GPU {index} unavailable; run arc-sbs devices or explicitly use CPU")
    return f"cuda:{index}"  # ROCm intentionally uses the same PyTorch device API.


class TorchDepthEstimator:
    def __init__(self, model, device=DEFAULT_DEVICE, cache_dir=None):
        self.torch = torch_runtime()
        self.device = select_torch_device(self.torch, device)
        self.device_name = "CPU" if self.device == "cpu" else self.torch.cuda.get_device_name(int(self.device.split(":")[1]))
        self.execution_devices = [self.device]
        path = Path(model)
        if path.suffix != ".pt":
            raise ValueError("This build requires a .pt model; run prepare-model from its installed environment")
        extra = {"arc_sbs.json": ""}
        start = time.perf_counter()
        self.model = self.torch.jit.load(str(path), map_location=self.device, _extra_files=extra).eval()
        metadata = json.loads(extra["arc_sbs.json"])
        shape = metadata.get("shape", [])
        if (metadata.get("model_id") != MODEL_ID or metadata.get("revision") != MODEL_REVISION
                or metadata.get("format") != "torchscript" or metadata.get("near_is_large") is not True):
            raise ValueError("Expected an Arc SBS export of the pinned Depth Anything V2 Small model")
        if (len(shape) != 4 or shape[:2] != [1, 3]
                or any(type(n) is not int or n < 56 or n % 14 for n in shape[2:])):
            raise ValueError("Expected a static [1,3,H,W] model with patch-aligned dimensions")
        self.height, self.width = shape[2:]
        self.compile_seconds = time.perf_counter() - start

    def infer_native(self, frame):
        tensor, (x, y, w, h) = preprocess(frame, self.height, self.width)
        # Enter inference mode in the caller thread (the video pipeline can overlap).
        with self.torch.inference_mode():
            result = self.model(self.torch.from_numpy(tensor).to(self.device))
            depth = result.float().cpu().numpy().squeeze().copy()
        if depth.ndim != 2 or not np.isfinite(depth).all():
            raise ValueError("Model returned invalid depth")
        depth = cv2.resize(depth, (self.width, self.height), interpolation=cv2.INTER_CUBIC)
        return depth[y:y+h, x:x+w]

    def infer(self, frame):
        return cv2.resize(self.infer_native(frame), (frame.shape[1], frame.shape[0]),
                          interpolation=cv2.INTER_CUBIC)
