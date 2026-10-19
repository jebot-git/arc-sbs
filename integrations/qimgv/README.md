# qimgv SBS / XR integration

The active viewer is now maintained in the separate private
[qimgvr repository](https://github.com/jebot-git/qimgvr). Its local installer
builds all optional features and replaces qimgv launchers. Source on this machine:
`/home/blux/qimgvr`; installed commands: `qimgvr`, `qimgv`, and `qimgv-sbs`.
The system qimgv RPM has been uninstalled. The pinned image-only integration below
is retained as the original 0.3.0 implementation; use qimgvr for current builds.

This directory contains a GPLv3 patch for
[easymodo/qimgv](https://github.com/easymodo/qimgv), pinned to
`3127a2d211b124ad4fcf853d01e6df9323bdfdc3`. qimgv remains a separate C++/Qt
application; it communicates with the MIT Arc SBS converter through JSON lines
over a child process. The qimgv patch is covered by the accompanying GPLv3
LICENSE. The model remains the separately licensed, pinned official Small model.

Install Arc SBS 0.3.0 and its model as described in the main README, then:

```bash
python3 integrations/qimgv/install.py
qimgv-sbs /path/to/photo.jpg
```

The installer clones a new source tree at `~/qimgv-sbs-source`, applies the patch,
builds with Qt6, runs native tests, and installs in `~/.local/opt/qimgv-sbs`.
It creates `~/.local/bin/qimgv-sbs` and a **qimgv SBS** application-menu entry;
the system qimgv and default file associations are retained. Pass `--source`
to use another clean pinned or already patched tree. The current workstation's
source tree is `/home/blux/qimgv`, branch `feature/sbs-xr`.

Build prerequisites: compiler, Git, CMake, Qt6 base/Svg/tools development packages
(including Qt Test and LinguistTools), and Exiv2 development files. The helper
does not install system packages. The local image-only build disables optional
mpv playback and OpenCV scaling. The separately installed Arc SBS Python worker
does use OpenCV for image processing.

With WiVRn **HeadsetConnected** and **SessionRunning**, static images automatically
convert on A770 and appear in a dedicated WayVR SBS overlay. Navigate in the
regular qimgv window; the overlay updates after processing. Idle connection checks
run every three seconds. F8 or **Toggle SBS / XR output** toggles output.
`--sbs` also enables a local squeezed SBS window if disconnected; `--no-sbs`
disables automatic XR output. Conversion uses qimgv's oriented/edited snapshot,
composites transparency on black, and limits the longest edge to 3840 pixels.
It handles static SDR images; videos and animations hide this image overlay.

The worker retains the compiled model, resets normalization per photograph,
and runs inference/stereo outside the UI process. Only the latest pending image
is converted after rapid navigation. Presentation is a separate command so stale
results cannot replace the requested image. The generated full-SBS PNG feeds a
2560×1440 squeezed canvas configured as `left_right`, `stereo_full_frame=false`.
Per-eye fitting keeps portrait/landscape margins symmetrical. Other XR overlays
and applications are left running. Closing qimgv closes its presenter.

The current adapter supports this machine's **WiVRn + WayVR** session, not every
OpenXR compositor. Set `ARC_SBS_WAYVRCTL` for another compatible WayVR CLI;
set `QIMGV_SBS_PYTHON` to override the installed converter interpreter.
First-image loading/compilation and PNG transport add latency; conversion delay
is intentional. This is a still-image viewer prototype, not camera/video playback.

The patch also adds `SBS_XR.md` to qimgv with build, controls and presentation
details. Uninstall the separate local qimgv prefix, launcher and desktop entry to
remove this integration. Converter/model installation is managed separately.
