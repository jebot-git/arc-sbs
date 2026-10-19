# qimgv SBS / active XR verification

Implemented and tested on 2026-10-19. qimgv source was fetched from
`https://github.com/easymodo/qimgv.git`, revision
`3127a2d211b124ad4fcf853d01e6df9323bdfdc3`, into `/home/blux/qimgv`.
The local branch is `feature/sbs-xr`; the reproducible patch and install helper
are in [integrations/qimgv](../integrations/qimgv/README.md).

## Implemented behavior

- Default automatic output when WiVRn reports a connected, running headset
  session. Explicit `--sbs` also supports a local squeezed SBS window;
  `--no-sbs` disables the integration. F8 and the context menu toggle output.
- qimgv provides its already oriented/edited image pixels to a persistent Arc SBS
  worker. Inference/rendering run outside the UI process. Each photograph gets
  independent normalization bounds; the compiled model is reused.
- Pending navigation is coalesced. A separate presentation request prevents
  stale image results appearing in XR. File replacement updates one presenter
  surface without opening a new XR application for every photograph.
- A dedicated per-eye fitted canvas avoids splitting the main viewer's controls
  and margins across the eye boundary. Internal full SBS is transported on a
  2560×1440 squeezed surface: WayVR `left_right`, `stereo_full_frame=false`.
- Only the integration's owned overlay is changed/closed. Existing XR apps,
  WiVRn and WayVR remain running. Disconnect detection is polled while idle.

## Functional checks

1. Full converter/Qt test suite: **60 passed**. New checks cover CPU still-image
   conversion with a real tiny OpenVINO IR, independent per-photo normalization,
   model reuse, atomic output preservation, protocol recovery, disconnection,
   and correct WayVR arguments including paths with spaces.
2. Native qimgv Qt Test target: three tests (plus setup/cleanup) pass. They cover
   portrait centering/eye separation, atomic-file reload and owner cleanup, and
   rapid navigation/disabled-result suppression with a real asynchronous process.
3. The patch applies cleanly to a fresh worktree at the pinned upstream revision.
   The integration install helper successfully builds/tests/installs the patched
   local source tree. The system `/usr/bin/qimgv` is retained.
4. Live session: Meta Quest Pro, WiVRn `HeadsetConnected=true` and
   `SessionRunning=true`. Opening the natural evaluation JPEG in the installed
   `qimgv-sbs` in default automatic mode generated a 4096×1362 full-SBS PNG.
   The live WayVR overlay reported `left_right` and `stereo_full_frame=false`.
   Mean absolute left/right pixel difference was 19.47 on the 0–255 scale;
   the eyes are rendered distinctly. Local numerical evidence is in ignored
   `artifacts/qimgv-xr/auto-verification.json`.
5. Both a normal visible main-window run and a deterministic offscreen
   main-window run reached a real Wayland XR presenter. Offscreen mode applied
   only to the automated main window. Owned overlays closed after the test;
   existing headset applications stayed active.

The full-SBS image was visually inspected on the host. Actual through-headset
perception, comfort and photon latency were not verified. The depth-based
prototype can show disocclusion/edge artifacts. Images are SDR, composited on
black and capped at 3840 pixels on the longest edge. Animations/videos are not
converted by this still-image integration. Optional mpv/OpenCV native qimgv
features are disabled in this image-only build; Exiv2 support remains enabled.

## Launch

Use **qimgv SBS** in the application menu or:

```bash
~/.local/bin/qimgv-sbs /path/to/photo.jpg
```

First-image compilation, image snapshots and PNG transfer introduce processing
delay. This implementation is intended for individual photographs, with normal
qimgv navigation controlling which image is sent to the active session.
