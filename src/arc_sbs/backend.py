"""Backend policy fixed in the installed package at build time."""
from ._build_backend import BACKEND

if BACKEND not in ("intel", "amd", "nvidia"):
    raise ValueError(f"Invalid built backend: {BACKEND!r}")

DEFAULT_DEVICE = {"intel": "ARC", "amd": "AMD", "nvidia": "NVIDIA"}[BACKEND]
MODEL_SUFFIX = ".xml" if BACKEND == "intel" else ".pt"
MODEL_SUFFIXES = (".xml", ".bin", ".json") if BACKEND == "intel" else (".pt", ".json")
GPU_LABEL = {"intel": "Intel Arc A770", "amd": "AMD ROCm (experimental)",
             "nvidia": "NVIDIA CUDA (experimental)"}[BACKEND]
DEFAULT_ENCODER = "h264_vaapi" if BACKEND == "intel" else "libx264"
