# Local Linux installation

Requires `uv` on PATH, FFmpeg/ffprobe, and drivers for the selected build.
Intel uses OpenVINO, Intel OpenCL and VA-API. Experimental AMD uses PyTorch
ROCm 6.4; experimental NVIDIA uses PyTorch CUDA 12.8. Both GPU builds target
Linux x86_64, use vendor OpenCL for stereo rendering, and default to software
H.264 encoding. AMD requires access to `/dev/kfd` and `/dev/dri`. The installer downloads locked Python dependencies
but the default `gui` dependency extra is empty; Qt6 is now an opt-in `qt6`
extra. It does not install system packages. No root access is needed.
The bundle targets Linux with Python 3.12 (uv can provision Python).

## Current FLTK packaging limitation

The Python wheel and user bundle do not include `arc-sbs-fltk` or patched FLTK.
The legacy installer below still imports PySide6 during validation, while its
current dependency export no longer includes it. A fresh default bundle can
therefore fail validation before activation. It is not yet a complete standalone
FLTK GUI installer. Build the native frontend and launch from source as described
in [the main README](../README.md#local-installation); keep the matching Python
worker environment and native runtime available.

The remaining installation paths describe the legacy versioned Python installer,
not a verified FLTK bundle. For an already compatible bundle, its commands are:

```bash
python3 install.py --model /path/to/depth-small.xml
~/.local/bin/arc-sbs-gui
~/.local/bin/arc-sbs devices
```

Choose the bundle matching your GPU (`intel`, `amd` or `nvidia`); its backend
is fixed during build. Intel models use XML, BIN and provenance JSON.
AMD/NVIDIA models use a `.pt` TorchScript file and provenance JSON; pass
`--model /path/to/depth-small.pt` instead. Keep those files together. Model weights are not
included. Existing prepared models may also be selected in the GUI. For model
preparation from source, see the repository README and its `export` extra.

The installer creates a versioned environment under `~/.local/opt/arc-sbs`,
launchers in `~/.local/bin`, and an Arc SBS application-menu entry. Reinstalling
switches the `current` symlink after package validation; older releases remain
available for rollback. This is a per-user Python installation, not an RPM.
The package has no dependency on the source checkout.

For still images with automatic output to an active WiVRn/WayVR session, the
bundle includes an optional qimgv integration. After installing Arc SBS, run
`python3 qimgv/install.py`, then `~/.local/bin/qimgv-sbs photo.jpg`. That helper
downloads pinned upstream qimgv sources and needs compiler/CMake, Qt6 development
packages (base/Svg/tools/Test), and Exiv2 development files. See `qimgv/README.md`.

Models go in `$XDG_DATA_HOME/arc-sbs/models` (default `~/.local/share`), and
OpenVINO caches in `$XDG_CACHE_HOME/arc-sbs/openvino` (default `~/.cache`).
`ARC_SBS_MODEL` overrides the default model path. FLTK preferences use
`$XDG_CONFIG_HOME/arc-sbs/fltk.json`; the optional Qt6 frontend uses Qt settings. Runtime conversion works offline after installation.

To uninstall, remove `~/.local/opt/arc-sbs`, `~/.local/bin/arc-sbs`,
`~/.local/bin/arc-sbs-gui`, and the `arc-sbs.desktop` and `arc-sbs.svg` files
under your user applications/icons directories. Models, caches, native FLTK preferences and Qt settings
are retained unless removed separately. System drivers are unaffected.

Build commands: `python3 packaging/build.py --backend intel|amd|nvidia`
(select one value). Artifacts are written under `dist/<backend>/`. For preparing
AMD/NVIDIA models in the installed environment, see the README build-time
backend section. GPU hardware execution on those vendors remains experimental.
