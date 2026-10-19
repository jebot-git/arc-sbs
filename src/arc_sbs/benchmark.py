"""Reproducible, render-only microbenchmark. Use video conversion for throughput."""
import time
import numpy as np

from .stereo import create_renderer


def benchmark_render(renderer="opencl", width=1920, height=1080, layout="half", iterations=30):
    if width <= 0 or height <= 0 or width % 2 or height % 2 or iterations <= 0:
        raise ValueError("Use positive even dimensions and positive iterations")
    rng = np.random.default_rng(42)
    frame = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    # Deterministic planes with a hard-edged foreground object and disocclusions.
    depth = np.broadcast_to(np.linspace(0, 0.7, width, dtype=np.float32), (height, width)).copy()
    depth[height//4:3*height//4, width//3:2*width//3] = 1
    engine = create_renderer(renderer)
    for _ in range(3):
        engine.render(frame, depth, layout=layout)
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        engine.render(frame, depth, layout=layout)
        times.append(time.perf_counter()-start)
    return {"renderer": engine.info, "input_size": [width, height], "layout": layout,
            "iterations": iterations, "mean_ms": float(np.mean(times)*1000),
            "p50_ms": float(np.median(times)*1000), "p95_ms": float(np.percentile(times,95)*1000),
            "fps": float(1/np.mean(times)),
            "scope": "render only, seeded RGB/planes; validation, allocations and GPU copies included; 3 warmups excluded"}
