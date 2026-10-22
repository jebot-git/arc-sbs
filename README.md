> This experimental branch defaults to FLTK 1.4.5. See [FLTK_EXPERIMENT.md](FLTK_EXPERIMENT.md) for build instructions and missing features.

# Arc SBS

The default `arc-sbs-gui` launches the native FLTK 1.4.5 conversion form.
It uses the same isolated Python conversion worker as the optional Qt6 GUI.
Its native menus export conversion/file actions through DBusMenu for DomeD;
DomeD supplies desktop/XR window controls. The retained Qt6 frontend is an
explicit opt-in, not a dependency of the default GUI. See [GUI usage](docs/GUI.md)
for the two frontends' features and limitations.

An independent Linux command-line prototype for converting SDR 2D video to
side-by-side stereoscopic video using Intel Arc/OpenVINO, with experimental
AMD ROCm and NVIDIA CUDA builds.

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

Build the default native frontend separately from the Python package:

```bash
uv sync --python 3.12 --locked
export DE_XR_FLTK_PREFIX=/path/to/patched-fltk-1.4.5
cmake -S fltk -B build-fltk -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$DE_XR_FLTK_PREFIX"
cmake --build build-fltk --parallel 4
uv run arc-sbs-gui
```

`arc-sbs-gui` finds `build-fltk/arc-sbs-fltk` in this checkout, then
`arc-sbs-fltk` on PATH. `ARC_SBS_FLTK_BINARY` overrides its executable;
`DE_XR_FLTK_PREFIX` selects its shared runtime. The wrapper sets
`ARC_SBS_PYTHON` to the active Python interpreter and resolves the model path.
The `gui` extra is now empty; use the `qt6` extra only for the Qt frontend.

The existing `packaging/build.py` generates Python wheels, source archives and
user bundles. Those artifacts do **not** include the native FLTK executable or
its runtime. The legacy `packaging/install.py` still validates PySide6 even
though the default dependency export no longer supplies it. Its complete GUI
installation path therefore remains unfinished for this default FLTK branch;
do not assume the Python bundle is a self-contained desktop installation.
The Intel bundle in `dist/intel/arc-sbs-0.4.1-intel-linux-user.tar.gz` includes locked, hashed
dependency requirements; installation needs network access for Python packages.
Model weights and system GPU drivers are not bundled. See
[installation, upgrades and removal](packaging/INSTALL.md).

## Build-time GPU backend selection

Choose exactly one backend when building; the installed package fixes the
inference runtime, GPU alias, model format and default encoder. A source checkout
and ordinary `uv build` default to Intel. Building another backend uses an isolated
source tree and leaves the checkout and its lockfile unchanged.

```bash
python3 packaging/build.py --backend intel   # default: OpenVINO, Arc A770
python3 packaging/build.py --backend amd     # experimental: PyTorch ROCm 6.4
python3 packaging/build.py --backend nvidia  # experimental: PyTorch CUDA 12.8
```

Artifacts and hashed runtime requirements are separate under `dist/intel`,
`dist/amd` and `dist/nvidia`. Extract the matching `*-linux-user.tar.gz` bundle
and run its `install.py`. GPU builds pin PyTorch 2.8.0 from the official
[ROCm/CUDA wheel indexes](https://pytorch.org/get-started/previous-versions/).
AMD/NVIDIA bundles target Linux x86_64 and require a card and driver supported by
that runtime. AMD also needs access to `/dev/kfd` and `/dev/dri`. Install the
vendor's OpenCL ICD for GPU stereo rendering, with 64-bit atomic support.

All backends use **the same pinned official Depth Anything V2 Small weights**.
Intel exports FP16-weight OpenVINO IR (`.xml` + `.bin`); AMD/NVIDIA export a
fixed-shape FP32 TorchScript model (`.pt`). The preprocessing, depth convention,
and geometry are shared. Models and provenance JSON are prepared separately;
weights are never included in the build. To prepare a model after installing an
AMD/NVIDIA bundle:

```bash
uv pip install --python ~/.local/opt/arc-sbs/current/venv/bin/python 'transformers>=4.48,<6' 'huggingface-hub>=0.28,<3'
arc-sbs prepare-model --output ~/.local/share/arc-sbs/models/depth-small.pt
arc-sbs devices
arc-sbs convert input.mp4 output.mp4
```

Use `--device AMD` or `--device NVIDIA` (the respective build default), `GPU.N`
to choose an inference card, or `CPU` for an explicit CPU path. A mismatched
PyTorch runtime or unavailable GPU fails explicitly. OpenCL selects the first
GPU matching the built vendor; Intel retains A770 selection by name. Inference
and OpenCL use separate device lists and host copies. `--renderer cpu` is
available when the vendor OpenCL driver lacks the required atomics.

AMD/NVIDIA default to software H.264 encoding (`libx264`), avoiding Intel-specific
VA-API paths; hardware video encoding remains an explicit option. The GUI and
still-image worker follow the built backend too. The backend cannot be changed
by a runtime environment variable; install a different build to switch it.

**Experimental validation:** package resolution/builds and CPU execution of the
portable export are tested. AMD/NVIDIA hardware inference, renderer parity and
performance remain unvalidated; no acceleration claims are made for these cards.
Intel performance results elsewhere in this README apply only to the measured
Intel configuration.

## Images in qimgvr and XR

The [qimgvr fork](https://github.com/jebot-git/qimgvr) provides asynchronous
still-image conversion and a dedicated SBS display in the connected WiVRn/WayVR
session, with mpv, OpenCV, Exiv2 and KDE support.
Install it after Arc SBS 0.4.1, then run `qimgvr photo.jpg`. F8 toggles SBS;
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

## Graphical frontends

The FLTK form supports video/model/output selection, compute and encoder choices,
SBS layout, strength, convergence, eye swapping, overwrite confirmation,
conversion progress/logs and cancellation. Advanced options include processing
quality, frame limits and device overrides.

The retained Qt6 GUI is explicitly selected with:

```bash
DE_XR_TOOLKIT=Qt6 uv run --extra qt6 arc-sbs-gui
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

### DomeD and existing SBS images

qimgvr 1.2.0 supports optional DomeD output using the image worker's
`--xr-backend domed` adapter. Enable it using qimgvr's `scripts/install-domed.py`;
it changes only Dome's default image associations. Active Dome XR sessions receive
an owned SBS window on the XR socket, with separate per-eye composition layers.

The image worker recognizes SBS/HSBS filename tokens, JPEG JPS descriptors and PNG
sTER metadata. JPS/PNS extension fallback uses right-first eye order; LR/PARALLEL
markers select left-first. Existing stereo bypasses depth inference, including
when the model is unavailable. qimgvr F9 and `--input-layout` override detection.
Unmarked wide images remain 2D. Video conversion behavior is unchanged.

### Transparent still images

Arc SBS 0.4.1 with qimgvr 1.2.1 preserves BGRA/PNG alpha through existing-SBS
bypass and CPU/A770 still-image warping. Input scaling and half-SBS packing use
premultiplied filtering; PNG storage remains straight alpha. Fully invisible
pixels participate only as the farthest background, so they cannot cover visible
geometry. Depth inference receives RGB composited on black, never hidden RGB or
an extra alpha channel. The presenter uses transparent margins and alpha-capable
Wayland buffers. Input 16-bit PNG samples are quantized to 8-bit SDR. Alpha means
coverage; the model does not simulate refraction or layered glass. The BGR video
pipeline remains unchanged.

## Shared installations

The bundle installer above targets the current user. A shared deployment can
instead keep its isolated environment under `/usr/local/libexec/arc-sbs/venv`
and root-owned launchers in `/usr/local/bin`. Models must be separately prepared,
readable by intended users, and configured in the application; keep per-user
preferences and caches writable in each user's profile. Do not run the per-user
installer with sudo to simulate a system installation. qimgvr can select the
shared interpreter through `QIMGV_SBS_PYTHON`.

## Command manuals and related projects

Basic manuals: [arc-sbs.1](docs/man/arc-sbs.1), [arc-sbs-gui.1](docs/man/arc-sbs-gui.1). After installation, use `man arc-sbs`.
The wheel installs these pages under its environment prefix; to expose them system-wide:

```sh
sudo install -d /usr/local/share/man/man1
sudo install -m 0644 docs/man/*.1 /usr/local/share/man/man1/
```

Part of the [de-xr desktop](https://github.com/topics/de-xr):
[DomeD](https://github.com/jebot-git/DomeD) provides the desktop/session,
[Ghostty XR](https://github.com/jebot-git/ghostty-xr) the terminal,
[mpvr](https://github.com/jebot-git/mpvr) video playback,
[cmuxr](https://github.com/jebot-git/cmuxr) music playback and an audio-reactive XR floor,
[fsvr](https://github.com/jebot-git/fsv-xr) file browsing,
[qimgvr](https://github.com/jebot-git/qimgvr) image viewing,
[Arc SBS](https://github.com/jebot-git/arc-sbs) depth conversion, and
[LightXR](https://github.com/jebot-git/LightXR) the optional login manager.
Each application has its own dependencies and can be used independently where
its selected backend permits.

### DomeD appearance

Inside a DomeD session, the GUI watches `dome/appearance.json` in the user's
configuration directory. Controls and titles follow its palette, opacity and
bitmap font family, style and native pixel size, including live changes and
atomic configuration replacement. The native frontend uses the shared patched
FLTK appearance hook. The optional Qt6 frontend has its own appearance adapter;
its missing bitmap selections fall back to the Qt application font. These
adapters do not change the CLI or its conversion settings.
