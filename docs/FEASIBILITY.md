# Feasibility assessment

Measured on 2026-10-19. **An offline 2D-to-SBS converter is feasible on this
system.** OpenVINO successfully runs depth estimation on the A770, and FFmpeg
successfully encodes stereo video through that GPU's VA-API media engine.
The implemented CPU stereo renderer prevents real-time HD conversion.

## VisionDepth3D starting point

The current [upstream repository](https://github.com/VisionDepth/VisionDepth3D)
was inspected at `995b8180a21fefc855dd0f83be3b49b5dcb8b5e0`.
It now distributes documentation and release information, not application source;
its [license](https://github.com/VisionDepth/VisionDepth3D/blob/995b8180a21fefc855dd0f83be3b49b5dcb8b5e0/LICENSE.txt)
prohibits derivatives. A direct fork or OpenVINO backend port therefore is not
an available implementation route without the owner's permission and source
access. This project is an independent implementation of the same broad product
goal using generic depth-image-based rendering. No older snapshot was assumed
to have a permissive license, and no protected application was reverse engineered.

Intel documents [Arc GPU support](https://docs.openvino.ai/2024/notebooks/gpu-device-with-output.html)
and [Depth Anything V2 conversion](https://docs.openvino.ai/2024/notebooks/depth-anything-v2-with-output.html).
The prototype verifies both locally, rather than relying only on those documents.

## System verified

| Component | Observed |
| --- | --- |
| OS | Nobara Linux 44, kernel 7.2.11-200.nobara.fc44.x86_64 |
| CPU | Intel Core i7-12700 |
| Integrated GPU | UHD 770, OpenVINO `GPU.0`, `/dev/dri/renderD128` |
| Discrete GPU | Arc A770, OpenVINO `GPU.1`, `/dev/dri/renderD129` |
| Stable Arc media path | `/dev/dri/by-path/pci-0000:03:00.0-render` |
| Media driver | Intel iHD 26.1.5; H.264, HEVC, AV1 encode capabilities advertised |
| Runtime | Python 3.12.15, OpenVINO 2026.4.1, FFmpeg 8.1.3 |
| Libraries | NumPy 2.5.4, OpenCV 5.0.0.93 |
| Export only | CPU PyTorch 2.14.1, Transformers 5.20.2 |

Nobara is not assumed to be an officially supported OpenVINO distribution;
compatibility here was established experimentally. No system packages or GPU
drivers were replaced. Python packages live in the project virtual environment.

## Prototype architecture

1. FFprobe validates the first video stream; FFmpeg decodes to BGR on CPU.
2. Replicate-letterbox into 518×294 RGB, scale /255, ImageNet normalization.
3. OpenVINO runs the pinned official Depth Anything V2 Small on A770. IR weights
   are FP16; the plugin chooses operation precision. This is not a claim that
   every operation executes in FP16. GPU selection is explicit, without AUTO
   CPU fallback, and the compiled model is cached.
4. Smooth the 2nd/98th-percentile normalization bounds, reset at simple scene
   cuts, and apply a small bilateral depth filter. Larger depth values are nearer.
5. Produce both eyes with forward horizontal splatting, a nearest-surface
   z-buffer, and horizontal background filling for disocclusions. This stage is
   NumPy/OpenCV on CPU, using integer pixel positions.
6. Pack half or full SBS, encode H.264 on Arc using VA-API, then attach the first
   optional audio stream as AAC and bound it to the converted duration.
7. Publish output atomically after successful muxing; record timings separately.

The pipeline streams frames and does not retain the movie in RAM. The rendering
working set scales with frame resolution, while FFmpeg performs normal buffering.
OpenVINO acceleration applies to depth inference; it does not accelerate the
NumPy renderer. Video encode acceleration uses VA-API, independently of OpenVINO.

## Measurements

Depth-only tests use the same seeded 640×360 image, three warmups, and synchronous
inference. They include preprocessing and output resizing. Compilation is excluded.

| Depth device | Iterations | Mean | Throughput |
| --- | ---: | ---: | ---: |
| A770 | 30 | 15.19 ms | 65.84 fps |
| i7-12700 CPU | 15 | 159.47 ms | 6.27 fps |

Observed depth acceleration is approximately **10.5×** for this workload.
These are short warm runs, not sustained thermal or film-quality benchmarks.

| End-to-end source → output | Frames | Pipeline time | Throughput |
| --- | ---: | ---: | ---: |
| 640×360 → 640×360 half SBS | 72 | 4.16 s | 17.31 fps |
| 960×540 → 1920×540 full SBS | 48 | 5.14 s | 9.33 fps |
| 1920×1080 → 1920×1080 half SBS | 24 | 9.27 s | 2.59 fps |

Final full-SBS and 1080p measurements were repeated sequentially to avoid
concurrent benchmark contention. They use H.264 VA-API and source audio.
The natural scene is a repeated still; other clips are procedural test patterns.
Pipeline timings include decoding, normalization, rendering, encoding, audio
mux, and filesystem work, but exclude model compilation (~0.6 seconds cached).
The 1080p run spends **8.05/9.27 seconds (~87%)** in CPU stereo rendering.
Depth plus normalization takes 0.71 seconds. At that observed speed, one hour of
24 fps 1080p video would take roughly 9.3 hours; that extrapolation is unvalidated
for long material and different content.

Raw measurements and model checksums are in [measurements/](measurements/).

## Validation and quality

- Eleven automated tests pass. Coverage includes RGB normalization, letterbox,
  device selection, neutral depth, scene-cut reset, eye order/disparity sign,
  foreground collision handling, background filling, partial pipe reads, real
  CPU/FFmpeg conversion with AAC, frame limits, existing-file protection, and
  failed-encoder cleanup. CPU integration uses a tiny deterministic OpenVINO
  graph, so tests do not require model downloads or a GPU.
- The real Small model was separately exercised on A770, with H.264 encoding
  and AAC, in three end-to-end clips. All output files fully decode with no
  FFmpeg errors and have the expected dimensions and duration. This establishes
  stream presence/duration, not perceptual long-form lip-sync quality.
- On one natural image, exported GPU inference versus pinned FP32 PyTorch gives
  Pearson correlation **0.99999957**, and RMSE / reference depth range
  **0.0002088** (~0.0209%). This checks export fidelity, not depth accuracy.
- The source/depth/eye contact sheet was visually inspected. Large scene geometry
  is plausible; thin structures and object boundaries remain approximate.
  Physical headset viewing and moving-film stereo quality were not evaluated.
- HEVC and AV1 encode modes are selectable and advertised by the driver; only
  H.264 and the software fallback are end-to-end validated in this session.

## Work required beyond the prototype

The first performance milestone is a GPU stereo renderer: subpixel splatting,
z-buffer visibility, and directional fill in OpenCL, SYCL, or another appropriate
GPU compute backend. Benchmark it independently, then overlap depth and video
stages and reduce host/device copies. CPU rendering is currently the main target;
a faster depth model alone cannot deliver real-time HD conversion.

Quality work needs motion-compensated temporal depth, edge-aware upsampling,
better disocclusion reconstruction, and stereo-window controls, tested on moving
people, hair, fences, rapid cuts, subtitles, and low-light scenes. Increase model
resolution only after measuring quality versus latency. Current input letterboxing
uses a fixed landscape model canvas and is especially limiting for portrait video.

Media work needs explicit HDR processing, VFR timestamp preservation, rotation
and pixel-aspect handling, multiple streams, long-run sync verification, progress
and cancellation controls, and hardware decode. The current simple grayscale
scene-cut detector is heuristic and cannot guarantee cut detection.

A GUI, batch scheduler, frame-exact editor, VisionDepth3D compatibility layer,
and production distribution are outside this initial prototype.
