"""Generic forward depth-image-based rendering with a nearest-surface z-buffer."""
import cv2
import numpy as np


def warp_eye(frame, depth, shift):
    h, w = depth.shape
    x = np.arange(w, dtype=np.int32)[None, :]
    y = np.arange(h, dtype=np.int32)[:, None]
    dest_x = np.rint(x + shift).astype(np.int32)
    valid = (dest_x >= 0) & (dest_x < w)
    dest = (y*w + np.clip(dest_x, 0, w-1))[valid]
    zbuffer = np.full(h*w, -np.inf, dtype=np.float32)
    np.maximum.at(zbuffer, dest, depth[valid])
    visible = depth[valid] >= zbuffer[dest]
    out = np.zeros_like(frame).reshape(-1, 3)
    out[dest[visible]] = frame[valid][visible]
    out = out.reshape(h, w, 3)
    zbuffer = zbuffer.reshape(h, w)
    occupied = np.isfinite(zbuffer)
    # Fill newly exposed horizontal strips from the farther valid surface.
    # This cannot reconstruct hidden texture; it is a bounded-quality prototype.
    left = np.maximum.accumulate(np.where(occupied, x, -1), axis=1)
    right = np.minimum.accumulate(np.where(occupied, x, w)[:, ::-1], axis=1)[:, ::-1]
    lc, rc = np.clip(left, 0, w-1), np.clip(right, 0, w-1)
    use_left = (left >= 0) & ((right == w) | (zbuffer[y, lc] <= zbuffer[y, rc]))
    source = np.where(use_left, lc, rc)
    filled = out[y, source]
    out[~occupied] = filled[~occupied]
    return out


def render_sbs(frame, depth, strength=0.025, convergence=0.5, layout="half", swap=False):
    """strength is maximum binocular disparity as a fraction of source width.

    Positive near disparity places the left-eye feature to the right of the
    right-eye feature (crossed disparity / in front of the screen).
    """
    validate_inputs(frame, depth, strength, convergence, layout)
    h, w = frame.shape[:2]
    disparity = (np.clip(depth, 0, 1)-convergence) * (strength*w)
    left = warp_eye(frame, depth, disparity*0.5)
    right = warp_eye(frame, depth, -disparity*0.5)
    if swap:
        left, right = right, left
    if layout == "half":
        left = cv2.resize(left, (w//2, h), interpolation=cv2.INTER_AREA)
        right = cv2.resize(right, (w//2, h), interpolation=cv2.INTER_AREA)
    return np.concatenate((left, right), axis=1)


def validate_inputs(frame, depth, strength, convergence, layout):
    if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
        raise ValueError("Frame must be an HxWx3 uint8 BGR array")
    h, w = frame.shape[:2]
    if h <= 0 or w <= 0:
        raise ValueError("Frame dimensions must be positive")
    if depth.shape != (h, w) or not np.isfinite(depth).all():
        raise ValueError("Depth must be finite and match frame dimensions")
    if not 0 <= strength <= 0.1 or not 0 <= convergence <= 1:
        raise ValueError("strength must be 0..0.1 and convergence 0..1")
    if layout not in ("half", "full"):
        raise ValueError("layout must be half or full")
    if w % 2 or h % 2:
        raise ValueError("Even source dimensions are required for YUV420 SBS")


class CPURenderer:
    info = {"backend": "cpu", "device": "NumPy/OpenCV CPU", "init_seconds": 0.0}
    render = staticmethod(render_sbs)


def create_renderer(backend):
    if backend == "cpu":
        return CPURenderer()
    if backend == "opencl":
        from .gpu_stereo import OpenCLRenderer
        return OpenCLRenderer()
    raise ValueError("renderer must be opencl or cpu")
