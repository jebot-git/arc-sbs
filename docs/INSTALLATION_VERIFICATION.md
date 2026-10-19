# Version 0.2.0 local installation verification

Verified on 2026-10-19 on this Nobara 44 workstation, Python 3.12.15:

- Built wheel, source distribution, and Linux user-install bundle with SHA-256
  sidecars. The bundle contains locked, hashed runtime/Qt dependency requirements
  and installer assets, without model weights or evaluation media.
- Installed under `~/.local/opt/arc-sbs` and copied the pinned FP16 model to
  `~/.local/share/arc-sbs/models`. XML/BIN/JSON remain together. Migrated this
  machine's GUI selection from the same model in the checkout to the local copy.
- Installed CLI and Qt commands report 0.2.0 from `/tmp`; no editable/source
  dependency. Packaged OpenCL shader resource is present. Desktop entry validates.
- Installed CLI conversion from `/tmp`: 24 frames of 640×360 SDR input, full SBS
  1280×360 output, OpenVINO `GPU.1` / Arc A770, OpenCL stereo, H.264 VA-API.
  Default model resolved to user data storage. Local report:
  `artifacts/installed-smoke.json` (ignored).
- Installed Qt window with isolated settings: offscreen conversion through its
  actual QProcess worker completed 12 frames on `GPU.1`; audio stream verified.
  This checks GUI/worker integration, not visual headset playback.
- Extracted user bundle reinstalls from `/tmp` independently of repository assets.
- Complete test suite including optional GUI tests: **56 passed**, one existing
  Python multiprocessing/fork deprecation warning.

This is functional installation verification, not a new performance benchmark.
System FFmpeg and GPU drivers remain external dependencies. Installing Python
dependencies needs network access; subsequent conversion works offline.
