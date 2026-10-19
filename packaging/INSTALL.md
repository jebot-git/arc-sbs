# Local Linux installation

Requires `uv` on PATH, FFmpeg/ffprobe, Intel OpenCL and VA-API drivers, and
user access to `/dev/dri`. The installer downloads locked Python dependencies
(including Qt6); it does not install system packages. No root access is needed.
The bundle targets Linux with Python 3.12 (uv can provision Python).

Extract the user bundle, enter its directory, then run:

```bash
python3 install.py --model /path/to/depth-small.xml
~/.local/bin/arc-sbs-gui
~/.local/bin/arc-sbs devices
```

Keep the model XML, BIN and provenance JSON together. Model weights are not
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
`ARC_SBS_MODEL` overrides the default model path; GUI model selections persist
in Qt settings. Runtime conversion works offline after installation.

To uninstall, remove `~/.local/opt/arc-sbs`, `~/.local/bin/arc-sbs`,
`~/.local/bin/arc-sbs-gui`, and the `arc-sbs.desktop` and `arc-sbs.svg` files
under your user applications/icons directories. Models, caches and Qt settings
are retained unless removed separately. System drivers are unaffected.
