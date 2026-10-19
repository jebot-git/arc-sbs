"""Validate natural-scene CPU/OpenCL parity and GPU-encoded media.

Run from the repository root after producing the GPU demo clips:
  uv run python scripts/validate_gpu_renderer.py
Local media is excluded from Git; numerical validation goes to artifacts/.
"""
import json
from pathlib import Path
import subprocess

import cv2
import numpy as np

from arc_sbs.depth import DepthEstimator, DepthNormalizer
from arc_sbs.gpu_stereo import OpenCLRenderer
from arc_sbs.stereo import render_sbs
from arc_sbs.video import probe

frame = cv2.imread("artifacts/natural.jpg")
if frame is None:
    raise SystemExit("Fetch artifacts/natural.jpg as described in README.md first")
frame = cv2.resize(frame, (1920,1080), interpolation=cv2.INTER_AREA)
estimator = DepthEstimator("models/depth-small.xml")
depth = DepthNormalizer().apply(estimator.infer(frame), frame)
renderer = OpenCLRenderer()
parity = []
for layout in ("half", "full"):
    for swap in (False,True):
        expected = render_sbs(frame,depth,layout=layout,swap=swap)
        actual = renderer.render(frame,depth,layout=layout,swap=swap)
        difference = np.abs(actual.astype(np.int16)-expected.astype(np.int16))
        parity.append({"layout":layout, "swap":swap,
                       "unequal_components":int(np.count_nonzero(difference)),
                       "max_error":int(difference.max())})
        if layout == "full" and not swap:
            cv2.imwrite("artifacts/gpu-stereo-preview.jpg",actual)
media = []
for name in ("gpu-1080p-half-sbs.mp4", "gpu-natural-full-sbs.mp4", "gpu-1080p-3s-sbs.mp4"):
    path = Path("artifacts")/name
    info = probe(path)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    check = subprocess.run(["ffmpeg", "-v", "error", "-i",str(path),"-f","null","-"],capture_output=True)
    media.append({"file":name,"size":[video["width"],video["height"]],
                  "duration":float(info["format"]["duration"]),
                  "audio_present":any(s["codec_type"]=="audio" for s in info["streams"]),
                  "full_decode_passed":check.returncode==0 and not check.stderr})
passed = all(p["max_error"]==0 for p in parity) and all(m["full_decode_passed"] and m["audio_present"] for m in media)
decoded_frame_parity = None
cpu_path = Path("artifacts/cpu-1080p-3s-sbs.mp4")
if cpu_path.exists():
    hashes = []
    for path in (cpu_path, Path("artifacts/gpu-1080p-3s-sbs.mp4")):
        text = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0",
                               "-f", "framemd5", "-"], check=True, capture_output=True, text=True).stdout
        hashes.append([line.split(",")[-1].strip() for line in text.splitlines() if not line.startswith("#")])
    matched = bool(hashes[0]) and hashes[0] == hashes[1]
    decoded_frame_parity = {"frames":len(hashes[0]),"identical":matched}
    passed = passed and matched
result = {"renderer":renderer.info,"natural_frame_size":[1920,1080], "parity":parity,
          "media":media,"decoded_cpu_gpu_frame_parity":decoded_frame_parity,"passed":passed}
Path("artifacts/gpu-renderer-validation.json").write_text(json.dumps(result,indent=2)+"\n")
print(json.dumps(result,indent=2))
raise SystemExit(0 if passed else 1)
