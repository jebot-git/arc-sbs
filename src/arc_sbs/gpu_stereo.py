"""Persistent A770 OpenCL renderer with deterministic depth/source visibility.

OpenVINO handles neural inference. OpenCL handles generic splatting, z-buffering,
disocclusion fill and SBS packing, including host/device transfers in render().
The renderer is synchronous and intended to be owned by a single pipeline.
"""
from pathlib import Path
import time

import numpy as np
import pyopencl as cl

from .stereo import validate_inputs
from .alpha import visibility_depth


def gpu_devices():
    """Enumerate GPU devices only; device ordinals differ from OpenVINO's."""
    return [d for p in cl.get_platforms() for d in p.get_devices(device_type=cl.device_type.GPU)]


class OpenCLRenderer:
    def __init__(self):
        start = time.perf_counter()
        try:
            matches = [d for d in gpu_devices() if "A770" in d.name and "Intel" in d.vendor]
            if not matches:
                raise RuntimeError("Intel Arc A770 OpenCL device unavailable")
            self.device = matches[0]
            required = {"cl_khr_int64_base_atomics", "cl_khr_int64_extended_atomics"}
            if not required.issubset(self.device.extensions.split()):
                raise RuntimeError("GPU requires 64-bit OpenCL atomics")
            self.context = cl.Context([self.device])
            self.queue = cl.CommandQueue(self.context)
            source = Path(__file__).with_name("stereo.cl").read_text()
            self.program = cl.Program(self.context, source).build(options=["-cl-std=CL1.2"])
            self.clear = cl.Kernel(self.program, "clear_keys")
            self.splat = cl.Kernel(self.program, "splat")
            self.pack = cl.Kernel(self.program, "pack")
        except (cl.Error, RuntimeError) as exc:
            raise RuntimeError(f"OpenCL renderer initialization failed: {exc}; use --renderer cpu") from exc
        self.shape = None
        self.init_seconds = time.perf_counter()-start

    @property
    def info(self):
        return {"backend": "opencl", "device": self.device.name.strip(),
                "platform": self.device.platform.name, "driver": self.device.driver_version,
                "init_seconds": self.init_seconds,
                "scope": "synchronous, includes host/device copies; no zero-copy sharing"}

    def render(self, frame, depth, strength=0.025, convergence=0.5, layout="half", swap=False):
        validate_inputs(frame, depth, strength, convergence, layout)
        h, w = frame.shape[:2]
        n = h*w
        if n > (2**31-1)//(2*frame.shape[2]):
            raise ValueError("Image is too large for the renderer's signed indices")
        frame = np.ascontiguousarray(frame)
        depth = np.ascontiguousarray(visibility_depth(frame, depth), dtype=np.float32)
        channels = frame.shape[2]
        if self.shape != (h, w, channels):
            flags = cl.mem_flags
            self.frame = cl.Buffer(self.context, flags.READ_ONLY, channels*n)
            self.depth = cl.Buffer(self.context, flags.READ_ONLY, 4*n)
            self.keys = cl.Buffer(self.context, flags.READ_WRITE, 16*n)
            self.output = cl.Buffer(self.context, flags.WRITE_ONLY, 2*channels*n)
            self.shape = (h, w, channels)
        output = np.empty((h, w if layout == "half" else 2*w, channels), dtype=np.uint8)
        # In-order queue guarantees uploads -> clear -> splat -> pack -> read.
        # Keep input arrays alive through the blocking output copy below.
        cl.enqueue_copy(self.queue, self.frame, frame, is_blocking=False)
        cl.enqueue_copy(self.queue, self.depth, depth, is_blocking=False)
        self.clear(self.queue, (2*n,), None, self.keys, np.int32(n))
        self.splat(self.queue, (n, 2), None, self.depth, self.keys, np.int32(w), np.int32(n),
                   np.float32(strength*w), np.float32(convergence))
        self.pack(self.queue, (output.shape[0]*output.shape[1],), None,
                  self.frame, self.depth, self.keys, self.output, np.int32(w), np.int32(h),
                  np.int32(layout == "half"), np.int32(bool(swap)), np.int32(channels))
        cl.enqueue_copy(self.queue, output, self.output, is_blocking=True)
        return output
