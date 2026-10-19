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


@pytest.mark.parametrize("depth_processing", ["full", "sampled", "model"])
def test_overlap_preserves_frame_count_audio_and_duration(media, tmp_path, depth_processing):
    source,model = media
    output = tmp_path / f"overlap-{depth_processing}.mp4"
    result = convert(source,output,model=model,device="CPU",renderer="cpu",encoder="libx264",
                     pipeline="overlap",depth_processing=depth_processing,frames=5)
    assert result['frames'] == 5
    assert result['pipeline'] == 'overlap'
    assert result['first_frame_submitted_seconds'] < result['last_frame_submitted_seconds']
    info = probe(output)
    assert any(s['codec_type']=='audio' for s in info['streams'])
    assert abs(float(info['format']['duration'])-5/8) < .08


def test_overlap_inference_failure_cleans_processes_and_destination(media, tmp_path, monkeypatch):
    from arc_sbs.depth import DepthEstimator
    source,model = media
    calls = 0
    original = DepthEstimator.infer
    def fail_second(self,frame):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('injected inference failure')
        return original(self,frame)
    monkeypatch.setattr(DepthEstimator,'infer',fail_second)
    output = tmp_path/'failed.mp4'
    with pytest.raises(RuntimeError,match='injected inference failure'):
        convert(source,output,model=model,device='CPU',renderer='cpu',encoder='libx264',pipeline='overlap')
    assert not output.exists()
    assert not list(tmp_path.glob('.arc-sbs-*'))


@pytest.mark.parametrize("include_audio", [True, False])
def test_stream_tee_produces_packets_and_valid_archive(media, tmp_path, include_audio):
    import socket
    source,model = media
    if not include_audio:
        silent=tmp_path/'silent-source.mp4'
        subprocess.run(['ffmpeg','-v','error','-i',str(source),'-c:v','copy','-an',str(silent)],check=True)
        source=silent
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1',0))
        sock.settimeout(2)
        output=tmp_path/'preview.mp4'
        result=convert(source,output,model=model,device='CPU',renderer='cpu',encoder='libx264',
                       pipeline='overlap',preview_port=sock.getsockname()[1],frames=4)
        packet=sock.recv(65535)
        assert packet[0] == 0x47  # MPEG-TS sync byte
    assert result['frames']==4
    assert any(s['codec_type']=='audio' for s in probe(output)['streams']) == include_audio


def test_overlap_and_serial_decode_to_identical_frames(media, tmp_path):
    source,model=media
    hashes=[]
    for mode in ('serial','overlap'):
        output=tmp_path/(mode+'.mp4')
        convert(source,output,model=model,device='CPU',renderer='cpu',encoder='libx264',pipeline=mode)
        result=subprocess.run(['ffmpeg','-v','error','-i',str(output),'-map','0:v:0','-f','framemd5','-'],
                              check=True,capture_output=True,text=True).stdout
        hashes.append([s for s in result.splitlines() if not s.startswith('#')])
    assert hashes[0] and hashes[0]==hashes[1]


@pytest.mark.skipif(not Path('/dev/dri/by-path/pci-0000:03:00.0-render').exists(),
                    reason='Configured Arc media device unavailable')
def test_vaapi_decode_matches_cpu_color_conversion(media, tmp_path):
    source,model=media
    hashes=[]
    for decoder in ('cpu','vaapi'):
        output=tmp_path/(decoder+'-decode.mp4')
        convert(source,output,model=model,device='CPU',renderer='cpu',encoder='libx264',
                pipeline='overlap',decode_device=decoder)
        result=subprocess.run(['ffmpeg','-v','error','-i',str(output),'-map','0:v:0','-f','framemd5','-'],
                              check=True,capture_output=True,text=True).stdout
        hashes.append([s for s in result.splitlines() if not s.startswith('#')])
    assert hashes[0] and hashes[0]==hashes[1]
