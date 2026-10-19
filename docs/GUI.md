# Qt6 conversion GUI

Launch from the project directory:

```bash
uv run --extra gui arc-sbs-gui
# Optionally prefill the input:
uv run --extra gui arc-sbs-gui /path/to/input.mp4
```

The optional `gui` extra installs PySide6-Essentials (Qt6 Widgets). The command-line
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
QT_QPA_PLATFORM=offscreen uv run --extra gui --extra dev pytest -q
```
