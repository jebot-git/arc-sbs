# Arc SBS graphical frontends

## Default FLTK frontend

`arc-sbs-gui` selects FLTK unless `DE_XR_TOOLKIT=Qt6` is set. Build the native
application against the shared patched FLTK 1.4.5 first:

```sh
export DE_XR_FLTK_PREFIX=/path/to/patched-fltk-1.4.5
cmake -S fltk -B build-fltk -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$DE_XR_FLTK_PREFIX"
cmake --build build-fltk --parallel 4
uv run arc-sbs-gui
```

The launcher finds the checkout's `build-fltk/arc-sbs-fltk` or that executable
on PATH. Override it with `ARC_SBS_FLTK_BINARY`. Launch through the Python
wrapper so the worker uses the same interpreter and model defaults as the CLI.
A prepared model and the selected backend's GPU/CPU dependencies are required.

Use the native Browse buttons for input, output and model files. Select Compute,
Layout, Encoder, Depth strength, Convergence, Swap eyes and Replace existing
output, then Convert. Progress and the worker log remain visible. Cancel sends
a cooperative termination request to the isolated worker. More options exposes
frame limit, processing quality, media-device path and inference-device override.
Settings persist in `$XDG_CONFIG_HOME/arc-sbs/fltk.json` (normally
`~/.config/arc-sbs/fltk.json`). The form follows DomeD's live bitmap appearance
and exports native conversion/file menu commands through DBusMenu.

The FLTK application does not currently implement the Qt frontend's first-file
argument prefill or drag-and-drop input handler. Use Browse or edit the fields.
It supports a graphical `--smoke-test FILE` screenshot diagnostic; it does not
provide the converter's CLI options or a separate GUI `--help` parser. Conversion
arguments belong to `arc-sbs convert --help`.

The Python wheel/user bundle does not contain this native executable. See
[installation limitations](../packaging/INSTALL.md) before distributing it.

## Qt6 frontend

The following usage and verification notes apply to the retained Qt6 frontend,
selected explicitly with the `qt6` extra and environment switch; they do not
establish equivalent FLTK interaction coverage.

For an installation that includes the optional Qt6 dependencies, run
`DE_XR_TOOLKIT=Qt6 ~/.local/bin/arc-sbs-gui`. Installed models default to
`~/.local/share/arc-sbs/models/depth-small.xml` (respects `XDG_DATA_HOME`).
The optional first argument prefills the input video.
Drop one local video onto the input field or the window to select it. File URLs
are decoded correctly, including spaces, Unicode and `#`. Output suggestions
update while preserving a manually selected destination. Drops never move the
source file. Remote URLs, folders, multiple files and drops during conversion
are rejected.

For development, launch from the project directory:

```bash
DE_XR_TOOLKIT=Qt6 uv run --extra qt6 arc-sbs-gui
# Optionally prefill the input:
DE_XR_TOOLKIT=Qt6 uv run --extra qt6 arc-sbs-gui /path/to/input.mp4
```

The optional `qt6` extra installs PySide6-Essentials (Qt6 Widgets); `gui` is empty. The command-line
converter works without Qt. This GUI targets the existing Linux implementation.

Choose an input video, an MP4/MKV output, and the prepared OpenVINO model. On this
checkout the existing `models/depth-small.xml` is selected automatically. The
matching `.bin` file must remain alongside it. A fresh checkout needs the one-time
model preparation described in the README; model downloading/export stays in the
CLI.

Select half/full SBS, Reference/Fast/Experimental depth processing, and Arc/CPU
acceleration. More options contains encoder, disparity strength, convergence, eye
order, frame limit, and the Intel media-device path. Arc defaults to OpenVINO and
OpenCL on A770 with hardware H.264 encoding. CPU selects software inference,
rendering and H.264. Overlapped scheduling remains enabled.

The output name is suggested from the input. Existing files require the explicit
**Replace existing output** checkbox. Progress shows estimated frames and speed;
it remains below 100% while encoding and audio muxing finish. A completion summary
and **Open output folder** button appear after successful publication. **Show
details** provides the worker log and full error messages.

Conversion runs in a separate QProcess, so the UI remains responsive during GPU
setup and processing. Cancel requests cooperative shutdown; the converter stops
FFmpeg, joins the depth worker and removes its temporary output. Closing the
window during a conversion also cancels it before closing. If a worker fails to
stop within ten seconds, the GUI stops its isolated process group and cleans the
scratch directory recorded for that job. Existing output is preserved when a job
is cancelled before publication.

The GUI converts video files. Live UDP preview, hardware-decode experiments and
other detailed CLI options remain available through `arc-sbs convert`.

## Verification

- Qt offscreen integration tests cover input/output validation, safe naming,
  CPU settings, a real conversion with audio, worker errors, responsive event
  processing, and cancellation while preserving an existing output.
- A real A770 conversion was launched through the Qt window: 24 frames of the
  procedural demo, full SBS, sampled processing, OpenVINO `GPU.1`, OpenCL rendering,
  and H.264 VA-API. Output dimensions were 1280×360. Numerical evidence is in
  [gui-smoke.json](measurements/gui-smoke.json).
- Default, expanded-options and completed screenshots were rendered and visually
  inspected. Local screenshots are under ignored `artifacts/gui-*.png`.
- Backend progress/cancellation regression coverage is included in the normal
  test suite. GUI tests skip when the optional Qt dependency is absent.

```bash
QT_QPA_PLATFORM=offscreen uv run --extra qt6 --extra dev pytest -q
```
