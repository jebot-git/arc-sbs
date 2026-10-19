"""Bounded-memory FFmpeg pipeline, source audio, atomic output, and telemetry."""
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import numpy as np

from .depth import DepthEstimator, DepthNormalizer
from .stereo import render_sbs


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def read_frame(pipe, size):
    data = bytearray()
    while len(data) < size:
        chunk = pipe.read(size-len(data))
        if not chunk:
            if data:
                raise RuntimeError("FFmpeg produced an incomplete video frame")
            return None
        data.extend(chunk)
    return data


def encoder_args(codec, vaapi_device):
    if codec == "libx264":
        return ["-c:v", codec, "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
    if codec not in ("h264_vaapi", "hevc_vaapi", "av1_vaapi"):
        raise ValueError(f"Unsupported encoder: {codec}")
    return ["-vf", "format=nv12,hwupload", "-c:v", codec, "-qp", "23"]


def convert(source, output, *, model, device="ARC", layout="half", strength=0.025,
            convergence=0.5, swap=False, encoder="h264_vaapi",
            vaapi_device="/dev/dri/by-path/pci-0000:03:00.0-render", frames=None,
            smoothing=0.85, overwrite=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output:
        raise ValueError("Input and output paths must differ")
    if output.exists() and not overwrite:
        raise FileExistsError(f"Output exists: {output}; use --overwrite to replace")
    if output.suffix.lower() not in (".mp4", ".mkv"):
        raise ValueError("Output must be .mp4 or .mkv")
    if frames is not None and frames <= 0:
        raise ValueError("--frames must be positive")
    if not 0 <= smoothing < 1:
        raise ValueError("smoothing must be in [0,1)")
    if not 0 <= strength <= 0.1 or not 0 <= convergence <= 1:
        raise ValueError("strength must be 0..0.1 and convergence 0..1")
    metadata = probe(source)
    stream = next((s for s in metadata["streams"] if s["codec_type"] == "video"
                   and not s.get("disposition", {}).get("attached_pic")), None)
    if stream is None:
        raise ValueError("Input has no video stream")
    width, height = stream["width"], stream["height"]
    if width % 2 or height % 2:
        raise ValueError("Even source dimensions are required")
    if stream.get("sample_aspect_ratio", "1:1") not in ("1:1", "0:1", "N/A"):
        raise ValueError("Normalize non-square source pixels before conversion")
    if stream.get("color_transfer") in ("smpte2084", "arib-std-b67"):
        raise ValueError("HDR requires explicit SDR tone mapping before this prototype")
    if any(int(s.get("rotation", 0)) % 360 for s in stream.get("side_data_list", [])) or int(stream.get("tags", {}).get("rotate", 0)) % 360:
        raise ValueError("Normalize video rotation metadata before conversion")
    fps = Fraction(stream.get("avg_frame_rate", "0/1"))
    if fps <= 0 or fps > 240:
        raise ValueError("Input frame rate must be >0 and <=240")
    fps_text = str(fps)
    output.parent.mkdir(parents=True, exist_ok=True)
    estimator = DepthEstimator(model, device)
    normalizer = DepthNormalizer(smoothing)
    out_width = width*2 if layout == "full" else width
    ffmpeg = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    decode_cmd = ffmpeg + ["-noautorotate", "-i", str(source), "-map", f"0:{stream['index']}",
                           "-vf", f"fps={fps_text}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    totals = {"depth_seconds": 0., "render_seconds": 0., "encode_write_seconds": 0.}
    count = 0
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=".arc-sbs-", dir=output.parent) as tmp:
        tmp = Path(tmp)
        silent = tmp / "silent.mkv"
        encoded = tmp / ("final"+output.suffix)
        encode_cmd = ffmpeg.copy()
        if encoder.endswith("_vaapi"):
            encode_cmd += ["-vaapi_device", str(vaapi_device)]
        encode_cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_width}x{height}",
                       "-r", fps_text, "-i", "pipe:0", "-an"]
        encode_cmd += encoder_args(encoder, vaapi_device) + [str(silent)]
        with (tmp/"decode.log").open("w+b") as dec_log, (tmp/"encode.log").open("w+b") as enc_log:
            decoder = subprocess.Popen(decode_cmd, stdout=subprocess.PIPE, stderr=dec_log)
            try:
                writer = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE, stderr=enc_log)
                try:
                    while frames is None or count < frames:
                        data = read_frame(decoder.stdout, width*height*3)
                        if data is None:
                            break
                        frame = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
                        t = time.perf_counter()
                        depth = normalizer.apply(estimator.infer(frame), frame)
                        totals["depth_seconds"] += time.perf_counter()-t
                        t = time.perf_counter()
                        stereo = render_sbs(frame, depth, strength, convergence, layout, swap)
                        totals["render_seconds"] += time.perf_counter()-t
                        t = time.perf_counter()
                        writer.stdin.write(stereo.tobytes())
                        totals["encode_write_seconds"] += time.perf_counter()-t
                        count += 1
                        if count % 30 == 0:
                            print(f"{count} frames; {count/(time.perf_counter()-start):.2f} fps", flush=True)
                    writer.stdin.close()
                    if writer.wait() != 0:
                        raise RuntimeError("FFmpeg encoder failed")
                    if frames is not None and count >= frames:
                        decoder.kill()
                    elif decoder.wait() != 0:
                        raise RuntimeError("FFmpeg decoder failed")
                except (Exception, KeyboardInterrupt) as exc:
                    writer.kill()
                    writer.wait()
                    dec_log.flush()
                    enc_log.flush()
                    dec_log.seek(0)
                    enc_log.seek(0)
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    raise RuntimeError(f"{exc}\nDecoder: {dec_log.read().decode(errors='replace')[-4000:]}\n"
                                       f"Encoder: {enc_log.read().decode(errors='replace')[-4000:]}") from exc
            finally:
                if decoder.poll() is None:
                    decoder.kill()
                decoder.wait()
                decoder.stdout.close()
        if count == 0:
            raise RuntimeError("No frames decoded")
        duration = count/float(fps)
        # Remux the produced video and first optional audio stream, bounded to
        # converted duration. AAC keeps MP4 compatibility across input codecs.
        mux_cmd = ffmpeg + ["-i", str(silent), "-i", str(source), "-map", "0:v:0", "-map", "1:a:0?",
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.9f}",
                            "-map_metadata", "-1", "-metadata:s:v:0", "stereo_mode=left_right" if not swap else "stereo_mode=right_left"]
        if output.suffix.lower() == ".mp4":
            mux_cmd += ["-movflags", "+faststart"]
        subprocess.run(mux_cmd+[str(encoded)], check=True, capture_output=True)
        if overwrite:
            os.replace(encoded, output)
        else:
            # A hard link atomically refuses a competing writer's existing file.
            os.link(encoded, output)
    elapsed = time.perf_counter()-start
    return {
        "input": str(source), "output": str(output), "frames": count,
        "source_fps": float(fps), "duration_seconds": duration,
        "input_size": [width, height], "output_size": [out_width, height],
        "layout": layout, "eye_order": "right_left" if swap else "left_right",
        "strength": strength, "convergence": convergence, "encoder": encoder,
        "vaapi_device": str(vaapi_device) if encoder.endswith("_vaapi") else None,
        "device": estimator.device, "device_name": estimator.device_name,
        "execution_devices": estimator.execution_devices,
        "model": str(Path(model).resolve()), "model_shape": [estimator.height, estimator.width],
        "compile_seconds": estimator.compile_seconds, "pipeline_seconds": elapsed,
        "pipeline_fps": count/elapsed, "detected_scene_cuts": normalizer.cuts,
        **totals,
    }
