# Provenance and third-party components

This prototype was requested in connection with VisionDepth3D. The current
[VisionDepth3D repository](https://github.com/VisionDepth/VisionDepth3D) at commit
`995b8180a21fefc855dd0f83be3b49b5dcb8b5e0` is a release/documentation repository;
application source is private. Its
[license](https://github.com/VisionDepth/VisionDepth3D/blob/995b8180a21fefc855dd0f83be3b49b5dcb8b5e0/LICENSE.txt)
is proprietary and prohibits derivatives. No VisionDepth3D files are included
in this repository, and no compatibility with its protected internals is claimed.
A derivative or direct port would require permission from its owner. Arc SBS
implements a generic independent depth-image-based rendering baseline.

- **Depth Anything V2 Small:** official
  [repository](https://github.com/DepthAnything/Depth-Anything-V2) and
  [Hugging Face model](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf).
  The Small model is Apache-2.0, as stated by its authors. This does not imply
  the same license for the Base/Large/Giant models. Only the pinned Small variant
  is used. Model revision: `5426e4f0f36572d16453bbda7a8389317b1bef99`.
  Weights are downloaded separately and are not redistributed in Git.
- **OpenVINO:** [Apache-2.0](https://github.com/openvinotoolkit/openvino/blob/master/LICENSE).
  Intel's [Depth Anything V2 notebook](https://docs.openvino.ai/2024/notebooks/depth-anything-v2-with-output.html)
  provides independent evidence of model conversion/inference feasibility. This
  project's exporter uses the Hugging Face implementation directly.
- **PyOpenCL:** [MIT](https://github.com/inducer/pyopencl/blob/main/LICENSE),
  used to execute the original OpenCL stereo kernels. The GPU implementation
  uses standard OpenCL atomics and memory operations; it is independent of
  VisionDepth3D.
- **FFmpeg:** invoked as an external executable. Its LGPL/GPL licensing depends
  on the build; no FFmpeg binary is bundled. See [FFmpeg legal](https://ffmpeg.org/legal.html).
- **NumPy, OpenCV, PyTorch, Transformers, Hugging Face Hub:** installed separately
  under their respective upstream licenses. Their code is not vendored here.
- **Natural validation image:** fetched separately from the Depth Anything V2
  repository's `assets/examples/demo01.jpg`. It is a local evaluation artifact
  and is not redistributed here. Procedural test videos come from FFmpeg lavfi.

The MIT license in this repository covers original Arc SBS code only.
