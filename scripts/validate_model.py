"""Compare exported GPU inference with the pinned PyTorch source and save preview.

Run from the repository root after installing the export extra:
  uv run --extra export python scripts/validate_model.py artifacts/natural.jpg
"""
import json
from pathlib import Path
import sys

import cv2
import numpy as np
import torch
from transformers import AutoModelForDepthEstimation

from arc_sbs.depth import DepthEstimator, DepthNormalizer, MODEL_ID, MODEL_REVISION, preprocess
from arc_sbs.stereo import render_sbs

image_path = Path(sys.argv[1])
frame = cv2.imread(str(image_path))
if frame is None:
    raise SystemExit(f"Cannot read {image_path}")
frame = cv2.resize(frame, (960, 638), interpolation=cv2.INTER_AREA)
estimator = DepthEstimator("models/depth-small.xml", "ARC")
tensor, _ = preprocess(frame, estimator.height, estimator.width)
torch.set_num_threads(8)
model = AutoModelForDepthEstimation.from_pretrained(MODEL_ID, revision=MODEL_REVISION).eval()
with torch.inference_mode():
    reference = model(pixel_values=torch.from_numpy(tensor)).predicted_depth.numpy().squeeze()
actual = np.asarray(estimator.request.infer({0:tensor})[estimator.output]).squeeze().copy()
error = np.sqrt(np.mean((actual-reference)**2))
relative_error = float(error / max(np.ptp(reference), 1e-6))
correlation = float(np.corrcoef(reference.ravel(), actual.ravel())[0,1])
result = {"device":estimator.device_name, "model_revision": MODEL_REVISION,
          "rmse_over_reference_range":relative_error, "pearson_correlation":correlation,
          "passed": relative_error < 0.01 and correlation > 0.99,
          "scope":"Pinned FP32 PyTorch vs FP16-weight OpenVINO on the same preprocessed image"}
Path("artifacts/model-validation.json").write_text(json.dumps(result,indent=2)+"\n")
depth = DepthNormalizer().apply(estimator.infer(frame), frame)
cv2.imwrite("artifacts/depth-preview.png", np.rint(depth*255).astype(np.uint8))
sbs = render_sbs(frame, depth, layout="full")
cv2.imwrite("artifacts/stereo-preview.jpg", sbs)
color_depth = cv2.applyColorMap(np.rint(depth*255).astype(np.uint8), cv2.COLORMAP_INFERNO)
preview = np.concatenate((frame, color_depth), axis=1)
preview = np.concatenate((preview, sbs), axis=0)
cv2.imwrite("artifacts/contact-sheet.jpg", preview)
print(json.dumps(result,indent=2))
if not result["passed"]:
    raise SystemExit(1)
