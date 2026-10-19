# Arc SBS

An independent Linux command-line prototype for converting SDR 2D video to
side-by-side stereoscopic video using the Intel Arc A770 and OpenVINO.

**Status:** working conversion/streaming prototype, validated on this machine. Depth inference
and stereo rendering run on Arc using OpenVINO and OpenCL; video encoding uses
Intel VA-API. GPU rendering is the default; `--renderer cpu` keeps the reference
path available. See [GPU rendering and measurements](docs/GPU_RENDERING.md) and
the [real-time implementation evaluation](docs/REALTIME_FEASIBILITY.md) before
planning a large conversion. A one-minute local 1080p24 half-SBS streaming test
passed; actual conversion-to-headset display latency remains unmeasured.

VisionDepth3D was the requested starting point, but its current application is
private and its license prohibits derivatives. This repository contains original
code and uses the separately available Depth Anything V2 Small model. It does not
contain or modify VisionDepth3D code, assets, or its custom stereo pipeline.
See [provenance](THIRD_PARTY_NOTICES.md).

## Local installation

Build a wheel, source archive, and user-install bundle, then install the Qt6 GUI
and command-line converter without root access:

```bash
uv lock --check
python3 packaging/build.py
python3 packaging/install.py --model models/depth-small.xml
arc-sbs-gui
```

The application menu also contains **Arc SBS**. The installer uses an isolated,
versioned environment under `~/.local/opt/arc-sbs`, copies the prepared model to
user data storage, and creates launchers in `~/.local/bin`. Add that directory
to PATH if needed. The installed app runs independently of this checkout.
The bundle in `dist/arc-sbs-0.3.0-linux-user.tar.gz` includes locked, hashed
dependency requirements; installation needs network access for Python packages.
Model weights and system GPU drivers are not bundled. See
[installation, upgrades and removal](packaging/INSTALL.md).

## Images in qimgvr and XR

The [qimgvr fork](https://github.com/jebot-git/qimgvr) provides asynchronous
still-image conversion and a dedicated SBS display in the connected WiVRn/WayVR
session, with mpv, OpenCV, Exiv2 and KDE support.
Install it after Arc SBS 0.3.0, then run `qimgvr photo.jpg`. F8 toggles SBS;
`--sbs` also enables local SBS display while disconnected. The model stays loaded
between images and qimgv remains responsive during conversion.

## Development setup

Requires Linux, Python 3.11–3.13, `uv`, FFmpeg/ffprobe with `libx264`, Intel
OpenCL compute runtime for inference/rendering, and Intel media driver for
VA-API encoding. Access to `/dev/dri` must be available to your user.

```bash
cd /home/blux/arc-sbs
uv sync --python 3.12 --extra export --extra dev --locked
uv run arc-sbs devices
uv run --extra export arc-sbs prepare-model
```

Model preparation downloads the pinned official Small model once, then exports
OpenVINO IR with FP16 weights. The default input is 518×294 (multiples of 14).
A prepared model already exists at `models/depth-small.xml` on this machine;
skip preparation here. Runtime conversion needs no PyTorch and works offline
after setup. Keep the `.xml`, `.bin`, and provenance `.json` files together.

## Qt6 GUI

```bash
uv run --extra gui arc-sbs-gui
```

Choose input/output video, layout, depth processing and Arc/CPU acceleration.
The window shows progress and supports cancellation; conversion runs in an
isolated worker. See [GUI usage](docs/GUI.md). A prepared OpenVINO model is required.
The GUI dependency is optional and the CLI works without it.

For the follow-up on interactive images, see [measured image feasibility](docs/IMAGE_FEASIBILITY.md).

## Convert

```bash
uv run arc-sbs convert input.mp4 output-half-sbs.mp4
uv run arc-sbs convert input.mp4 output-full-sbs.mkv \
  --layout full --frames 240 --strength 0.025 --convergence 0.5 \
  --report artifacts/my-conversion.json
```

- `--pipeline overlap` is the default and prepares one frame ahead while the
  current frame renders/encodes. `--pipeline serial` is the reference schedule.
- `--depth-processing full` retains reference processing. `sampled` approximates
  percentile bounds with a grid for more speed; `model` filters before upsampling
  and changes edges more. See the measured tradeoffs in the real-time report.
- `--preview-port PORT` also sends H.264/AAC SBS to a loopback UDP player before
  conversion finishes. `--pace` reads the input at nominal playback speed.
- `--decode-device vaapi` optionally uses hardware decode with host download;
  current support is limited to 8-bit 4:2:0. CPU decode remains the default.
- `--renderer opencl` is the default and selects the Intel A770 by name. It
  requires 64-bit integer OpenCL atomics. Initialization failures are explicit;
  use `--renderer cpu` for the NumPy/OpenCV reference implementation.
- `--device ARC` is the default and selects A770 by name, currently `GPU.1`.
  Selection fails explicitly if A770 is absent. For an entirely CPU pipeline,
  use `--device CPU --renderer cpu --encoder libx264`.
- `--encoder h264_vaapi` uses Intel encoding by default. The configured render
  node is `/dev/dri/by-path/pci-0000:03:00.0-render`, the A770 on this machine.
  Override `--vaapi-device` on a different host. `libx264`, `hevc_vaapi`, and
  `av1_vaapi` are also selectable; there is no silent encoder fallback.
- Half SBS preserves the input frame size, compressing each eye horizontally.
  Full SBS doubles the width. Eye order defaults to left then right; use
  `--swap-eyes` if your player expects the reverse. Select SBS explicitly in
  the headset/player; MP4 players may ignore stereo metadata.
- `--strength` controls the disparity span as a fraction of source width.
  `--convergence` sets the screen plane in normalized depth, 0=far and 1=near.
- Existing outputs are protected unless `--overwrite` is supplied. Conversion
  uses a temporary directory beside the destination and publishes on success.
- First optional audio stream is re-encoded to AAC, limited to output duration.
  Subtitles, chapters, extra audio tracks, and HDR metadata are not preserved.

For more detail: `uv run arc-sbs convert --help`.

## Demo and verification

```bash
uv run arc-sbs make-demo artifacts/source.mp4
uv run arc-sbs convert artifacts/source.mp4 artifacts/demo.mp4
uv run arc-sbs benchmark --device ARC --iterations 30
uv run arc-sbs benchmark --device CPU --iterations 15
uv run arc-sbs benchmark-render --renderer opencl --iterations 30
uv run arc-sbs benchmark-render --renderer cpu --iterations 30
uv run --extra dev pytest -q
```

Existing local GPU artifacts include `artifacts/gpu-1080p-half-sbs.mp4`,
`artifacts/gpu-1080p-3s-sbs.mp4`, `artifacts/gpu-natural-full-sbs.mp4`, and
`artifacts/gpu-stereo-preview.jpg`. Media, models, and caches are excluded from Git.
The procedural demo tests plumbing; the natural sample tests a static scene.
Neither is a representative moving-film quality benchmark. The backend tests pass
on this A770; GPU tests skip on hosts without it. Full-resolution natural-scene
parity and demo decoding can be checked with
`uv run python scripts/validate_gpu_renderer.py` after creating the local media.

To check the exported model against its original FP32 PyTorch implementation:

```bash
curl -fL https://raw.githubusercontent.com/DepthAnything/Depth-Anything-V2/main/assets/examples/demo01.jpg \
  -o artifacts/natural.jpg
uv run --extra export python scripts/validate_model.py artifacts/natural.jpg
```

## Limits

The prototype accepts even-sized, square-pixel SDR video without rotation
metadata. It decodes the first non-cover-art video stream on CPU and converts
to constant frame rate using the source average frame rate. VFR cadence is not
preserved; unusual start times and long-form audio synchronization need further
validation. HDR, anamorphic video, and rotation must be normalized beforehand.

A small relative-depth model cannot recover ground-truth geometry or unseen
background. Nearest-pixel forward splatting and horizontal background filling
can create stair steps, halos, stretched textures, and stereo window violations.
Only normalization bounds are temporally smoothed; there is no motion-compensated
temporal depth model. The fixed landscape input letterboxes portrait content and
loses detail. This is an engineering baseline, not production-quality conversion.

For higher depth detail, export a separate larger shape (and select it with
`--model`); expect more inference cost:

```bash
uv run --extra export arc-sbs prepare-model \
  --height 518 --width 924 --output models/depth-small-hq.xml
```

## Development

`src/arc_sbs/depth.py` handles GPU selection, preprocessing, inference, and depth
normalization. `gpu_stereo.py` and `stereo.cl` implement the OpenCL renderer;
`stereo.py` retains the CPU reference and shared validation.
`video.py` manages FFmpeg and output integrity. `model.py` handles one-time
export. `cli.py` provides the commands. Dependencies are pinned in `uv.lock`;
PyTorch resolves from its CPU wheel index for the export tool.

Original project code: MIT. Model weights and external tools retain their own
licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
