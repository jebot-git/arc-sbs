"""Build-selected export from the official Apache-2.0 Small model; no remote code."""
import hashlib
import json
from pathlib import Path

from .depth import MODEL_ID, MODEL_REVISION
from .backend import BACKEND, MODEL_SUFFIX, MODEL_SUFFIXES


def prepare_model(destination, height=294, width=518):
    import torch
    from transformers import AutoModelForDepthEstimation

    destination = Path(destination)
    if height < 56 or width < 56 or height % 14 or width % 14:
        raise ValueError("Model height/width must be multiples of 14 and at least 56")
    if destination.suffix != MODEL_SUFFIX:
        raise ValueError(f"Destination must end in {MODEL_SUFFIX} for the {BACKEND} build")
    if any(destination.with_suffix(s).exists() for s in MODEL_SUFFIXES):
        raise FileExistsError(f"Model destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    model = AutoModelForDepthEstimation.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, trust_remote_code=False,
    ).eval()

    class Wrapper(torch.nn.Module):
        def __init__(self, depth_model):
            super().__init__()
            self.depth_model = depth_model

        def forward(self, pixel_values):
            return self.depth_model(pixel_values=pixel_values).predicted_depth

    example = torch.zeros(1, 3, height, width)
    wrapper = Wrapper(model).eval()
    metadata = {
        "model_id": MODEL_ID, "revision": MODEL_REVISION,
        "license": "Apache-2.0", "shape": [1, 3, height, width],
        "near_is_large": True, "backend": BACKEND,
        "format": "openvino" if BACKEND == "intel" else "torchscript",
    }
    with torch.inference_mode():
        if BACKEND == "intel":
            import openvino as ov
            converted = ov.convert_model(wrapper, example_input=example,
                                         input=[(1, 3, height, width)])
            ov.save_model(converted, destination, compress_to_fp16=True)
            metadata["openvino"] = ov.__version__
        else:
            # Fixed-shape FP32 keeps operator/precision requirements portable
            # across supported ROCm and CUDA cards. No remote executable code.
            traced = torch.jit.trace(wrapper, example)
            torch.jit.save(traced, str(destination),
                           _extra_files={"arc_sbs.json": json.dumps(metadata)})
    metadata.update({
        "preprocessing": "RGB /255 ImageNet normalization, replicate letterbox",
        "torch": torch.__version__,
        "sha256": {s: hashlib.sha256(destination.with_suffix(s).read_bytes()).hexdigest()
                   for s in MODEL_SUFFIXES if s != ".json"},
    })
    destination.with_suffix(".json").write_text(json.dumps(metadata, indent=2)+"\n")
    return metadata
