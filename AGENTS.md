# Arc SBS development

Keep the implementation independent of proprietary VisionDepth3D code and assets.
Use the pinned official Depth Anything V2 Small model unless explicitly changing
model provenance. Do not commit models, caches, or evaluation media.

Run `uv run --extra dev pytest -q` after changes to rendering or the video pipeline.
Tests exercise geometry, z ordering, preprocessing, audio, and failed-output cleanup.
When measuring acceleration, select A770 by name and report actual OpenVINO device,
model shape, warmup policy, and whether numbers cover inference or the full pipeline.
Run performance measurements sequentially to avoid GPU/CPU contention.
