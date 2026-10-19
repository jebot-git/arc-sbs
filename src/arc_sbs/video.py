"""Bounded-memory FFmpeg pipeline, source audio, atomic output, and telemetry."""
from fractions import Fraction
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import cv2
import numpy as np

from .depth import DepthEstimator, DepthNormalizer
from .stereo import create_renderer


class ConversionCancelled(Exception):
    """A cooperative cancellation that preserves the destination file."""


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
            smoothing=0.85, overwrite=False, renderer="opencl",
            depth_processing="full", pipeline="overlap", preview_port=None, pace=False,
            decode_device="cpu", progress_callback=None, cancel_requested=None):
    def check_cancel():
        if cancel_requested is not None and cancel_requested():
            raise ConversionCancelled("Conversion cancelled")

    def progress(phase, **values):
        if progress_callback is not None:
            progress_callback({"phase": phase, **values})

    check_cancel()
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
    if depth_processing not in ("full", "sampled", "model") or pipeline not in ("serial", "overlap"):
        raise ValueError("Invalid depth processing or pipeline mode")
    if decode_device not in ("cpu", "vaapi"):
        raise ValueError("decode_device must be cpu or vaapi")
    if preview_port is not None:
        if not 1024 <= preview_port <= 65535:
            raise ValueError("Preview port must be 1024..65535")
        if encoder not in ("h264_vaapi", "libx264"):
            raise ValueError("Live preview currently requires an H.264 encoder")
    metadata = probe(source)
    stream = next((s for s in metadata["streams"] if s["codec_type"] == "video"
                   and not s.get("disposition", {}).get("attached_pic")), None)
    if stream is None:
        raise ValueError("Input has no video stream")
    width, height = stream["width"], stream["height"]
    if decode_device == "vaapi" and stream.get("pix_fmt") not in ("yuv420p", "nv12"):
        raise ValueError("VA-API decode prototype currently supports 8-bit 4:2:0 sources")
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
    duration_hint = stream.get("duration", metadata.get("format", {}).get("duration"))
    total_hint = int(stream.get("nb_frames", 0)) if str(stream.get("nb_frames", "")).isdigit() else 0
    if not total_hint and duration_hint:
        try:
            total_hint = max(0, round(float(duration_hint)*float(fps)))
        except (TypeError, ValueError, OverflowError):
            pass
    if frames is not None:
        total_hint = min(total_hint, frames) if total_hint else frames
    progress("initializing", frames=0, total=total_hint, source_fps=float(fps))
    output.parent.mkdir(parents=True, exist_ok=True)
    stereo_renderer = create_renderer(renderer)
    estimator = DepthEstimator(model, device)
    check_cancel()
    normalizer = DepthNormalizer(smoothing, sampled=depth_processing == "sampled")
    out_width = width*2 if layout == "full" else width
    ffmpeg = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    decode_cmd = ffmpeg + (["-re"] if pace else [])
    if decode_device == "vaapi":
        decode_cmd += ["-hwaccel", "vaapi", "-hwaccel_device", str(vaapi_device),
                       "-hwaccel_output_format", "vaapi"]
    # Match the CPU decoder's planar swscale path. Direct NV12 -> BGR uses
    # different chroma interpolation and changes the depth model's RGB input.
    decode_filter = ("hwdownload,format=nv12,format=yuv420p," if decode_device == "vaapi" else "") + f"fps={fps_text}"
    decode_cmd += ["-noautorotate", "-i", str(source), "-map", f"0:{stream['index']}",
                   "-vf", decode_filter, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    totals = {"depth_seconds": 0., "inference_seconds": 0., "normalization_seconds": 0.,
              "decode_read_seconds": 0., "render_seconds": 0., "encode_write_seconds": 0.}
    first_submitted = None
    last_submitted = None
    executor = None
    count = 0
    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=".arc-sbs-", dir=output.parent) as tmp:
        tmp = Path(tmp)
        progress("preparing", temporary_directory=str(tmp))
        silent = tmp / "silent.mkv"
        encoded = tmp / ("final"+output.suffix)
        encode_cmd = ffmpeg.copy()
        if encoder.endswith("_vaapi"):
            encode_cmd += ["-vaapi_device", str(vaapi_device)]
        encode_cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{out_width}x{height}",
                       "-r", fps_text, "-i", "pipe:0"]
        if preview_port is None:
            encode_cmd += ["-an"]
        else:
            encode_cmd += ["-i", str(source), "-map", "0:v:0", "-map", "1:a:0?",
                           "-c:a", "aac", "-b:a", "192k", "-af", "apad", "-shortest"]
        encode_cmd += encoder_args(encoder, vaapi_device)
        if preview_port is None:
            encode_cmd += [str(silent)]
        else:
            # One encode, two muxers. Local UDP preview can be consumed before
            # the archive is finalized. No port is exposed outside loopback.
            if encoder == "libx264":
                encode_cmd += ["-tune", "zerolatency"]
            encode_cmd += ["-flags", "+global_header", "-bf", "0", "-g", str(max(1, round(float(fps)))), "-f", "tee",
                           f"[f=matroska]{silent}|[f=mpegts:flush_packets=1:max_delay=0]udp://127.0.0.1:{preview_port}?pkt_size=1316"]
        with (tmp/"decode.log").open("w+b") as dec_log, (tmp/"encode.log").open("w+b") as enc_log:
            decoder = subprocess.Popen(decode_cmd, stdout=subprocess.PIPE, stderr=dec_log)
            try:
                writer = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE, stderr=enc_log)
                try:
                    def prepare_next():
                        t = time.perf_counter()
                        data = read_frame(decoder.stdout, width*height*3)
                        totals["decode_read_seconds"] += time.perf_counter()-t
                        if data is None:
                            return None
                        frame = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
                        t = time.perf_counter()
                        raw = estimator.infer_native(frame) if depth_processing == "model" else estimator.infer(frame)
                        inferred = time.perf_counter()
                        depth = normalizer.apply(raw, frame)
                        if depth_processing == "model":
                            depth = cv2.resize(depth, (width, height), interpolation=cv2.INTER_CUBIC)
                            np.clip(depth, 0, 1, out=depth)
                        finished = time.perf_counter()
                        totals["inference_seconds"] += inferred-t
                        totals["normalization_seconds"] += finished-inferred
                        totals["depth_seconds"] += finished-t
                        return frame, depth

                    if pipeline == "overlap":
                        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="arc-sbs-depth")
                        pending = executor.submit(prepare_next)
                    while frames is None or count < frames:
                        check_cancel()
                        prepared = pending.result() if executor else prepare_next()
                        if prepared is None:
                            break
                        if executor and (frames is None or count+1 < frames):
                            pending = executor.submit(prepare_next)
                        frame, depth = prepared
                        t = time.perf_counter()
                        stereo = stereo_renderer.render(frame, depth, strength, convergence, layout, swap)
                        totals["render_seconds"] += time.perf_counter()-t
                        t = time.perf_counter()
                        writer.stdin.write(stereo.tobytes())
                        totals["encode_write_seconds"] += time.perf_counter()-t
                        first_submitted = first_submitted or time.perf_counter()-start
                        last_submitted = time.perf_counter()-start
                        count += 1
                        if count == 1 or count % 5 == 0:
                            progress("converting", frames=count, total=total_hint,
                                     fps=count/(time.perf_counter()-start))
                        if count % 30 == 0:
                            print(f"{count} frames; {count/(time.perf_counter()-start):.2f} fps", flush=True)
                    check_cancel()
                    progress("encoding", frames=count, total=total_hint)
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
                    if isinstance(exc, (KeyboardInterrupt, ConversionCancelled)):
                        raise
                    raise RuntimeError(f"{exc}\nDecoder: {dec_log.read().decode(errors='replace')[-4000:]}\n"
                                       f"Encoder: {enc_log.read().decode(errors='replace')[-4000:]}") from exc
            finally:
                if decoder.poll() is None:
                    decoder.kill()
                decoder.wait()
                if executor:
                    executor.shutdown(wait=True, cancel_futures=True)
                decoder.stdout.close()
        if count == 0:
            raise RuntimeError("No frames decoded")
        check_cancel()
        progress("finalizing", frames=count, total=total_hint)
        duration = count/float(fps)
        # Remux the produced video and first optional audio stream, bounded to
        # converted duration. AAC keeps MP4 compatibility across input codecs.
        mux_cmd = ffmpeg + ["-i", str(silent), "-i", str(source), "-map", "0:v:0", "-map", "1:a:0?",
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{duration:.9f}",
                            "-map_metadata", "-1", "-metadata:s:v:0", "stereo_mode=left_right" if not swap else "stereo_mode=right_left"]
        if output.suffix.lower() == ".mp4":
            mux_cmd += ["-movflags", "+faststart"]
        subprocess.run(mux_cmd+[str(encoded)], check=True, capture_output=True)
        check_cancel()
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
        "renderer": stereo_renderer.info,
        "depth_processing": depth_processing, "pipeline": pipeline,
        "decode_device": decode_device,
        "preview_url": f"udp://127.0.0.1:{preview_port}" if preview_port else None,
        "paced_input": pace, "first_frame_submitted_seconds": first_submitted,
        "last_frame_submitted_seconds": last_submitted,
        "active_fps": (count-1)/(last_submitted-first_submitted) if count > 1 else None,
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
