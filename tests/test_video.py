import io
from pathlib import Path
import shutil
import subprocess

import openvino as ov
from openvino import opset13 as ops
import pytest

from arc_sbs.video import convert, probe, read_frame


def test_partial_frame_reads():
    assert read_frame(io.BytesIO(b"abc"), 3) == b"abc"
    assert read_frame(io.BytesIO(b""), 3) is None
    with pytest.raises(RuntimeError):
        read_frame(io.BytesIO(b"ab"), 3)


@pytest.fixture
def media(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is required")
    source = tmp_path / "source with spaces.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "1",
                    "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
    x = ops.parameter([1,3,28,42], ov.Type.f32)
    y = ops.reduce_mean(x, ops.constant([1]), False)
    model = tmp_path / "tiny.xml"
    ov.save_model(ov.Model([y], [x]), model)
    return source, model


def test_real_cpu_pipeline_preserves_audio_limits_frames_and_refuses_overwrite(media, tmp_path):
    source, model = media
    output = tmp_path / "stereo.mkv"
    result = convert(source, output, model=model, device="CPU", renderer="cpu", encoder="libx264", layout="full", frames=4)
    assert result["frames"] == 4
    assert result["duration_seconds"] == 0.5
    info = probe(output)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (128,48)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert abs(float(info["format"]["duration"])-0.5) < 0.08
    assert video.get("tags", {}).get("stereo_mode") == "left_right"
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        convert(source, output, model=model, device="CPU", renderer="cpu", encoder="libx264")
    assert output.read_bytes() == before


def test_encoder_failure_is_atomic_and_cleans_temporary_files(media, tmp_path):
    source, model = media
    output = tmp_path / "broken.mp4"
    with pytest.raises(RuntimeError, match="Encoder:"):
        convert(source, output, model=model, device="CPU", renderer="cpu", encoder="h264_vaapi",
                vaapi_device="/no/such/device", frames=1)
    assert not output.exists()
    assert not list(tmp_path.glob(".arc-sbs-*"))
