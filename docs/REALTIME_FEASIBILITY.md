# Real-time conversion and playback: implementation evaluation

Tested on 2026-10-19 on the existing A770/i7-12700 system. **1080p24 half-SBS is
now demonstrated in a one-minute paced local playback test**, using sampled depth
normalization and an overlapped pipeline. All 1,440 frames were decoded with audio.
WiVRn's headset presentation counters advanced throughout that test. This is a
local decode test alongside XR, not a measurement of conversion-to-headset display
latency or perceptual stereo/lip-sync quality.

The default pipeline now overlaps work while retaining reference full-resolution
depth processing. Sampled depth processing is an explicit faster option. Model-
resolution processing remains experimental because it changes depth edges more.

## Implementations tested

- **Allocation reduction:** full-resolution float32 normalization now uses one
  working array with in-place division/clipping. An exact-array regression test
  verifies the previous arithmetic. All matrix cases include this change.
- **Bounded overlap:** one depth worker reads, infers and normalizes the next
  frame while the main thread renders and writes the current one. At most one
  prepared frame is pending. A single worker preserves scene-cut/EMA order;
  each inference request and renderer has a single owner. Errors propagate to
  the caller, kill FFmpeg, join the worker, and leave no published partial file.
- **Sampled bounds:** calculate 2nd/98th percentiles on a regular grid targeting
  at most 65,536 samples. At 1080p this is every sixth row/column. Normalize and
  bilateral-filter at full resolution. This approximates the global bounds.
- **Model-resolution processing:** normalize and bilateral-filter on the cropped
  model output, then bicubic-upsample and clamp to video resolution. This changes
  spatial filtering and is not equivalent to the reference.
- **VA-API decode:** optional A770 video decode followed by host download and
  BGR conversion. Currently restricted to 8-bit 4:2:0 input. It is not zero-copy.
- **Playback before completion:** one H.264 encode is muxed both to the temporary
  archive and to a loopback UDP MPEG-TS preview using FFmpeg tee. The preview
  includes the first optional audio stream as AAC. B-frames are disabled for the
  preview path and a keyframe is requested every nominal second. The finalized
  archive still receives the original source audio through the existing mux step.

## Throughput matrix

Two sequential sweeps, reverse case order in the second sweep. Each case converts
all 240 frames of the same native 1920×1080, 24 fps, ten-second moving FFmpeg
`testsrc2` clip with sine audio. Depth model: Small, 518×294. Stereo: half SBS,
2.5% disparity span, convergence 0.5. Inference/render/encode use A770; initial
matrix source decode uses CPU. No frames are intentionally dropped.

| Depth processing | Pipeline | Mean pipeline fps | Range across two runs |
| --- | --- | ---: | ---: |
| Full reference | Serial | 18.87 | 16.86–20.87 |
| Full reference | Overlap | **24.78** | **24.43–25.12** |
| Sampled bounds | Serial | 23.37 | 20.92–25.83 |
| Sampled bounds | Overlap | **28.97** | **28.09–29.85** |
| Model resolution | Serial | 24.42 | 23.28–25.55 |
| Model resolution | Overlap | 29.13 | 28.47–29.79 |

Pipeline time includes decode, processing, encode, audio mux and publication, but
excludes OpenVINO compilation and renderer initialization, which are separately
reported. Stage totals overlap in time and must not be added to infer wall time.
The new native-resolution source differs from the older upscaled short demo;
use the matched rows above for comparisons, not ratios to historical results.

Reference overlap averages **1.31×** serial throughput with identical decoded
images. Its lower run is only ~1.8% above 24 fps; this is a narrow margin. Sampled
overlap averages **1.54×** the serial reference and has ~17% headroom over 24 fps
in its slower run. These results do not establish reliable 30 or 60 fps.

Average normalization cost falls from about **12.3 ms/frame** in reference overlap
to **4.5 ms** with sampled bounds and **2.9 ms** at model resolution. Model-
resolution processing provides little overall speed benefit over sampled bounds
on this machine, because other work and GPU scheduling also contribute.

The initial direct NV12-to-BGR hardware decode path changed colors: the first
frame had 0.868 mean absolute color difference and some components differed by
78. Forcing planar YUV420P before BGR conversion fixes this. All 240 final encoded
frames now decode identically to the CPU path. The corrected VA-API decoder is
available as an explicit option; CPU decode remains the default. Decoder timing
checks give **27.81–27.84 fps** with corrected VA-API decode, versus
**26.41–26.61 fps** in two fresh CPU-decode reference runs in the same follow-up
window (roughly a 5% improvement). The CPU recheck is itself faster than the
initial matrix, demonstrating system/load variation. The restricted hardware
path is useful to test explicitly, but this modest gain is insufficient evidence
to make it the portable default. Reports named `vaapi-full-overlap-*` describe
the initial rejected NV12 color path; `vaapi-planar-*` describe the corrected
implementation. `decode-quality.json` records the diagnosis.

## Fidelity and visual tradeoffs

**49 automated tests pass on this A770**, including overlap failure cleanup,
frame limits, live output with/without audio, exact normalization, and hardware
decode color parity. Full-resolution serial and overlap outputs decode to identical frame hashes for
all 240 frames. The same parity check passes for sampled serial/overlap and model
serial/overlap. A separate test protects exact reference normalization arithmetic.

Depth-mode quality evaluation uses six sampled procedural frames and six cropped
translations of a natural still, with real OpenVINO depth. These are comparisons
to the existing renderer, not ground-truth depth or representative film testing.
Reported percentiles below are averages of per-frame percentiles.

| Natural pan approximation | Sampled bounds | Model-resolution processing |
| --- | ---: | ---: |
| Mean binocular disparity difference | **0.026 px** | 0.058 px |
| 95th-percentile disparity difference | **0.078 px** | 0.333 px |
| 99th-percentile disparity difference | **0.087 px** | 0.806 px |
| Mean RGB difference, 0–255 | 0.098 | 0.338 |
| Changed half-SBS pixels | 1.75% | 3.74% |

The error heatmaps were visually inspected. Model-resolution differences
concentrate around edges and thin structures; individual edge disparity errors
can be much larger than the means. Sampled bounds preserve the spatial filter
and stay closer in these tests, but a sampling grid can alias different content.
It remains opt-in. The local heatmap is `artifacts/feasibility/depth-quality.jpg`
(top: source/reference depth; bottom: sampled/model error amplified 20×).

## Local streaming and paced playback

A headless FFmpeg client starts first, decodes local UDP H.264/AAC, and reports
progress every 50 ms. It uses a small probe budget. First-frame times below are
approximate upper bounds from decoded progress, including converter setup and
cached model initialization. They are not steady-state per-frame latency.

| Test | Frames decoded | First decoded report | Active submission rate |
| --- | ---: | ---: | ---: |
| Reference overlap, six seconds unpaced | 144/144 | 1.27 s | 26.32 fps |
| Sampled overlap, six seconds paced | 144/144 | 1.38 s | 24.04 fps |
| Sampled overlap, sixty seconds paced | **1,440/1,440** | **1.32 s** | **24.09 fps** |

Every client decoded output before archive publication, detected AAC audio and
reported no corrupt-packet/decode errors. Archive durations are 6/6/60 seconds.
The one-minute converter call completed in 62.62 seconds including setup and
archive finalization; paced input intentionally caps throughput. The active
frame-submission rate shows that the path kept up with the 24 fps source.

WiVRn was configured for 90 Hz. Main eye-stream presented counters each increased
by roughly 5,805 during the soak observation interval. This establishes that
headset streaming was active alongside conversion; it does not establish its
visual quality, dropped-frame behavior, or added photon latency. The new SBS
preview was decoded to a null output, not viewed in the headset during this test.

UDP has no EOF. The test client waits for a four-second idle timeout to drain
its last decoder frames. This drain is excluded from converter wall time and is
not a per-frame playback delay. A production finite-stream player needs explicit
session/end handling. Live viewing also needs player buffering, A/V sync, seek,
cancellation and reconnect validation; the sine soundtrack is not a lip-sync test.

## Memory-copy assessment

A profiling-enabled OpenCL queue measured 20 iterations after three warmups on
1080p half SBS with persistent buffers:

| Device operation | Mean |
| --- | ---: |
| RGB upload | 0.31 ms |
| Depth upload | 0.40 ms |
| Clear + visibility splat + pack | 1.12 ms |
| SBS download | 1.00 ms |
| Host wall time for the profiled sequence | 5.04 ms |

Device event times exclude Python, driver enqueue and host overhead. The current
copies total about 1.70 ms of device time in this diagnostic; removing them alone
would not account for the entire earlier real-time gap. This is profiling of the
existing copy path, not an implemented zero-copy comparison. Sharing OpenVINO,
OpenCL and VA-API surfaces would require a new interop path and removal of current
CPU preprocessing/rawvideo-pipe requirements.

## Commands and reproduction

Reference depth with overlap is now the default:

```bash
uv run arc-sbs convert input.mp4 output.mp4
uv run arc-sbs convert input.mp4 output-fast.mp4 --depth-processing sampled
uv run arc-sbs convert input.mp4 output-serial.mp4 --pipeline serial
uv run arc-sbs convert input.mp4 output-vaapi.mp4 --decode-device vaapi
```

Start a player before the converter for a local preview:

```bash
mpv --profile=low-latency --cache=no \
  --demuxer-lavf-probesize=32768 --demuxer-lavf-analyzeduration=0 \
  'udp://127.0.0.1:18370?fifo_size=16384&overrun_nonfatal=1'
uv run arc-sbs convert input.mp4 output.mp4 \
  --depth-processing sampled --preview-port 18370 --pace
```

Run these in separate terminals. In WayVR, configure the player overlay as
`left_right`; for default half SBS set `stereo_full_frame=false`. Full SBS requires
`stereo_full_frame=true`. The preview is loopback-only and does not itself create
an XR window. MP4 output remains atomic; streaming is the separate UDP branch.

Evaluation scripts are `evaluate_realtime.py` (two-sweep matrix),
`evaluate_quality.py`, `evaluate_transfers.py`, `evaluate_stream.py`, and
`validate_realtime_outputs.py` under `scripts/`. They write to ignored local
`artifacts/feasibility/`. Source media, models, logs and images are not committed.
Raw numerical reports are in [measurements/realtime/](measurements/realtime/).

The benchmark source was created with FFmpeg lavfi testsrc2 at 1920×1080/24 fps
and a 48 kHz sine, encoded with H.264 VA-API/AAC for ten seconds. The soak source
loops that clip, re-encodes at explicit 24 fps and ends at sixty seconds. Natural
quality input is the separately fetched image described in the README.

## Assessment

Keep overlap enabled and retain full processing as the reference default. Use
sampled processing when additional 24 fps headroom is needed, with its approximation
made explicit. Model-resolution processing is not the preferred tradeoff in these
tests, and VA-API decode shows insufficient benefit to make it the default.

The prototype now demonstrates local real-time 1080p24 half-SBS conversion and
playback plumbing. It still needs representative moving-film and headset viewing
validation, long-form thermal/synchronization testing, actual source-to-headset
latency instrumentation, and production stream/session handling. 1080p full-SBS,
30/60 fps, HDR, VFR preservation and zero-copy remain unproven in this evaluation.
