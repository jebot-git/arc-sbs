# OpenCL stereo rendering on Arc A770

Implemented and validated on 2026-10-19. Conversion now defaults to GPU stereo
rendering through `--renderer opencl`; `--renderer cpu` keeps the reference path.
OpenVINO depth inference and Intel VA-API encoding remain unchanged.

```bash
uv sync --locked
uv run arc-sbs convert input.mp4 output-sbs.mp4 --renderer opencl
uv run arc-sbs convert input.mp4 output-reference.mp4 --renderer cpu
uv run arc-sbs benchmark-render --renderer opencl --iterations 30
uv run arc-sbs benchmark-render --renderer cpu --iterations 30
```

## Implementation

`gpu_stereo.py` creates a persistent PyOpenCL context/queue on the Intel A770,
compiles `stereo.cl`, and reuses GPU allocations between frames. It selects by
vendor and model name rather than reusing OpenVINO's device ordinal. On this
machine the platform is `Intel(R) OpenCL Graphics (discrete)`, the device is
`Intel(R) Arc(TM) A770 Graphics`, and the driver is `26.35.39758.11`.

The implementation requires `cl_khr_int64_base_atomics` and
`cl_khr_int64_extended_atomics`; both are available here. Missing devices,
capabilities, or build failures produce an explicit error with instructions to
select CPU rendering. No silent fallback occurs. PyOpenCL is a locked runtime
dependency and the OpenCL source is included in the built wheel.

The GPU runs three kernels per frame:

1. Clear left/right visibility buffers.
2. Forward-splat each source pixel into both eyes. A 64-bit atomic maximum picks
   the nearest depth, using the source index as a deterministic tie-breaker.
   Packing depth and index into one atomic key avoids races between visibility
   and color writes. Signed finite float depths are ordered monotonically;
   positive and negative zero compare equally.
3. Gather visible colors, fill holes from the farther of the nearest occupied
   horizontal neighbors, and pack the eyes. Half SBS averages adjacent pixels
   using nearest-even rounding, matching the CPU's 2:1 INTER_AREA resize.
   Eye swapping is included in this pass.

The output preserves the CPU renderer's integer-pixel geometry and background
fill behavior. NumPy promotes integer image coordinates plus float32 shifts to
float64. A naive float32 GPU addition changed some half-pixel decisions at HD
widths. The kernel instead separates each shift into its integer and fractional
parts and applies nearest-even rounding using integer coordinate parity. This
matches the reference without requiring GPU FP64. Fast relaxed math is disabled.

`render()` is synchronous, owns an in-order queue, and includes host input
uploads and output download. It does not share OpenVINO or VA-API surfaces.
A renderer belongs to one conversion pipeline and is not a concurrent API.
GPU memory scales with frame size; at 1080p its four persistent buffers total
about 60 MB, plus host arrays and runtime overhead.

## Measurements

Final corrected implementation; benchmarks run sequentially on the same system
as [the original feasibility assessment](FEASIBILITY.md). Render-only measurements
use 1920×1080 seeded RGB and synthetic depth planes with a foreground block,
three warmups and 30 measured iterations. Initialization is excluded; validation,
per-call output allocation and GPU transfers are included.

| 1080p half-SBS rendering | Mean time | Throughput |
| --- | ---: | ---: |
| CPU reference | 328.31 ms | 3.05 fps |
| A770 OpenCL | 9.33 ms | 107.23 fps |

This is **35.2× faster** for the measured rendering workload. Earlier kernel
measurements before the HD rounding correction are superseded by these results.

| Complete conversion | Frames | Pipeline time | Throughput |
| --- | ---: | ---: | ---: |
| CPU renderer, 1080p loop → half SBS | 72 | 27.60 s | 2.61 fps |
| GPU renderer, same input/settings | 72 | 4.47 s | 16.09 fps |
| GPU renderer, 1080p → half SBS | 24 | 1.64 s | 14.61 fps |
| GPU renderer, 960×540 natural scene → full SBS | 48 | 1.93 s | 24.86 fps |

The matched 72-frame comparison improves overall throughput **6.17×**. Its source
is a stream-copied loop with reported average rate 23.6219 fps and 3.048-second
output duration; both runs use that same input. The separate 24-frame clip is
24 fps with a one-second output duration. The natural-scene clip repeats a still.
These short clips do not establish sustained movie performance or thermal behavior.

All conversions use the 518×294 Small model, A770 OpenVINO `GPU.1`, and H.264
VA-API encoding with AAC. Pipeline timings include decode, CPU normalization,
rendering, encoding, audio mux and file work. OpenVINO compilation (~0.6 s cached)
and renderer initialization (~0.04 s cached) are excluded and reported separately.
Raw reports are in [measurements/](measurements/).

For the 72-frame GPU run, depth inference plus CPU normalization takes 2.64 seconds
(~59% of pipeline time), rendering including copies takes 0.61 seconds (~14%),
and pipe writes take 0.35 seconds. CPU normalization, host I/O and pipeline
scheduling are now useful profiling targets. At 16.1 fps the observed 1080p path
remains below 24 fps real time.

## Verification

- **39 automated tests pass on this A770**, including 28 GPU cases. They cover
  foreground collisions, holes and valid black pixels, random and signed depth,
  eye order, both layouts, half-pixel ties, HD rounding, reused buffers, and
  changed dimensions. GPU tests skip discovery on hosts without an A770;
  build/execution failures on an available A770 fail the tests.
- A natural 1920×1080 frame with real OpenVINO depth produces **zero unequal
  color components** versus CPU for half/full SBS and both eye orders.
- The decoded CPU/GPU H.264 outputs for the matched 72-frame run have **identical
  frame hashes for every frame**. This validates end-to-end image parity on that
  clip, beyond unencoded-array comparison.
- Three GPU demo files fully decode without FFmpeg errors and contain audio with
  expected dimensions and durations. The CPU audio/output-integrity tests still pass.
- A wheel build was checked for the packaged `arc_sbs/stereo.cl` source.

Reproduce numerical/media checks with `scripts/validate_gpu_renderer.py` after
creating the local demo clips listed in the measurement reports. It requires the
natural image described in the README and the prepared model. When the matched
CPU output is present it additionally compares decoded frame hashes.

## Remaining limits

Acceleration preserves existing visual limitations: nearest-pixel splatting,
heuristic horizontal background fill, unmodeled hidden textures and no
motion-compensated temporal depth. Hidden texture cannot be recovered by faster
rendering. Subpixel stereo quality, headset evaluation and representative moving
film tests remain future work.

Hole filling scans within each row until an occupied neighbor is found. Cost is
content- and disparity-dependent; the supported disparity range is bounded by
the existing strength limit. Pipeline buffers remain host-facing, so this is not
a zero-copy design. Decode and depth normalization still run on CPU, and depth,
rendering and encoding are not asynchronously overlapped.
