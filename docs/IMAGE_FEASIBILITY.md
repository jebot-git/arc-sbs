# Still-image conversion and display feasibility

**Interactive still-image conversion is more feasible than continuous video.**
The current A770 backend converts a 1080p image and creates an owned Qt image
buffer in roughly 50–70 ms when warm. A still-image viewer can retain that result
and keep displaying it without running depth inference on each headset refresh.
A changing camera or desktop-image stream still requires fresh inference for
every changed frame and has the same core throughput constraints as video.

Measured on 2026-10-19 using the existing 518×294 Small model and one natural
image, center-cropped/resized to 1080p and 4K. Each case uses three warmups followed
by twenty sequential conversions. Scene-normalization state is reset for every
image. Results include preprocessing/inference, full-size depth processing,
OpenCL stereo rendering with transfers, and copying the SBS pixels into a Qt
QImage. Input pixels are already loaded in memory.

| Input | Layout | Depth processing | Mean latency | 95th percentile |
| --- | --- | --- | ---: | ---: |
| 1920×1080 | Half SBS | Reference | 65.9 ms | 81.0 ms |
| 1920×1080 | Half SBS | Sampled | **50.6 ms** | 66.5 ms |
| 1920×1080 | Full SBS | Reference | 70.5 ms | 84.9 ms |
| 1920×1080 | Full SBS | Sampled | **58.0 ms** | 74.2 ms |
| 3840×2160 | Half SBS | Reference | 111.4 ms | 124.9 ms |
| 3840×2160 | Half SBS | Sampled | **76.4 ms** | 94.3 ms |
| 3840×2160 | Full SBS | Reference | 121.9 ms | 142.7 ms |
| 3840×2160 | Full SBS | Sampled | **99.2 ms** | 120.5 ms |

Model/renderer setup took about **0.81 seconds**, excluding Python module import.
Loading the source JPEG took about 11 ms in this run. Actual application launch,
image scaling/loading, window/compositor presentation and headset delivery add
time. These measurements stop at a Qt image buffer; they are not photon-latency
measurements. No video encoder, file write, audio or network transport is included.

For a viewer that converts only when a picture changes, the measured latency is
suitable for interactive selection and modest update rates. Once depth is cached,
changing strength/convergence could skip inference: the measured full-SBS stereo
and Qt-copy stages total roughly 17–20 ms at 1080p. That is an implementation
estimate, not a tested slider-to-display path.

The sampled full-SBS path corresponds to about **17 images/second at 1080p** or
**10 images/second at 4K** if run serially. It does not demonstrate 30/60/90 fresh
conversions per second. A persistent viewer could overlap stages, reuse depth for
unchanged images and avoid video decoding/encoding, but those optimizations need
a dedicated image pipeline and display test.

The previous video tests use overlapping stages and different image content/run
conditions. Compare their throughput separately from these single-image serial
latencies. Neither benchmark establishes a universal GPU rate under changing XR
load. The fixed model resolution also means 4K output does not imply 4K inferred
geometry.

The Qt6 application currently provides video conversion. A still-image viewer
with cached depth and direct SBS texture presentation is a reasonable next feature;
its native-headset display latency remains unmeasured.

Reproduce with the separately fetched natural image described in the README:

```bash
QT_QPA_PLATFORM=offscreen uv run --extra gui python scripts/evaluate_images.py
```

Raw timings and measured scope: [conversion.json](measurements/images/conversion.json).
