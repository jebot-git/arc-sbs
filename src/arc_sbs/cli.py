import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np

from .depth import DepthEstimator, devices
from .model import prepare_model
from .video import convert


def parser():
    p = argparse.ArgumentParser(description="Intel Arc/OpenVINO 2D-to-SBS prototype")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("devices", help="List OpenVINO devices and FFmpeg availability")
    prep = commands.add_parser("prepare-model", help="Download official Small weights and export FP16 IR")
    prep.add_argument("--output", type=Path, default=Path("models/depth-small.xml"))
    prep.add_argument("--height", type=int, default=294)
    prep.add_argument("--width", type=int, default=518)
    bench = commands.add_parser("benchmark", help="Warm depth-only inference test on a fixed image")
    bench.add_argument("--model", type=Path, default=Path("models/depth-small.xml"))
    bench.add_argument("--device", default="ARC")
    bench.add_argument("--image", type=Path)
    bench.add_argument("--iterations", type=int, default=30)
    bench.add_argument("--report", type=Path)
    render_bench = commands.add_parser("benchmark-render", help="Warm rendering-only test including GPU transfers")
    render_bench.add_argument("--renderer", choices=["cpu", "opencl"], default="opencl")
    render_bench.add_argument("--width", type=int, default=1920)
    render_bench.add_argument("--height", type=int, default=1080)
    render_bench.add_argument("--layout", choices=["half", "full"], default="half")
    render_bench.add_argument("--iterations", type=int, default=30)
    render_bench.add_argument("--report", type=Path)
    conv = commands.add_parser("convert", help="Convert SDR video to full or half SBS")
    conv.add_argument("input", type=Path)
    conv.add_argument("output", type=Path)
    conv.add_argument("--model", type=Path, default=Path("models/depth-small.xml"))
    conv.add_argument("--renderer", choices=["cpu", "opencl"], default="opencl", help="Stereo rendering backend (default: A770 OpenCL)")
    conv.add_argument("--device", default="ARC", help="ARC selects A770 by name; or CPU/GPU.N")
    conv.add_argument("--layout", choices=["half", "full"], default="half")
    conv.add_argument("--strength", type=float, default=0.025, help="Disparity / source width, 0..0.1")
    conv.add_argument("--convergence", type=float, default=0.5, help="Screen plane depth, 0=far, 1=near")
    conv.add_argument("--swap-eyes", dest="swap", action="store_true")
    conv.add_argument("--encoder", choices=["libx264", "h264_vaapi", "hevc_vaapi", "av1_vaapi"], default="h264_vaapi")
    conv.add_argument("--vaapi-device", default="/dev/dri/by-path/pci-0000:03:00.0-render")
    conv.add_argument("--frames", type=int, help="Convert only the first N frames")
    conv.add_argument("--smoothing", type=float, default=0.85, help="Normalization bound EMA, [0,1)")
    conv.add_argument("--depth-processing", choices=["full", "sampled", "model"], default="full",
                      help="Reference full-resolution, sampled bounds, or normalize/filter before upsampling")
    conv.add_argument("--pipeline", choices=["serial", "overlap"], default="overlap",
                      help="Overlap one prepared frame with rendering/encoding; bounded memory")
    conv.add_argument("--preview-port", type=int, help="Also stream H.264/AAC MPEG-TS to a local UDP player")
    conv.add_argument("--pace", action="store_true", help="Read source at nominal playback speed")
    conv.add_argument("--decode-device", choices=["cpu", "vaapi"], default="cpu",
                      help="Optional VA-API decode with host download, currently 8-bit 4:2:0 only")
    conv.add_argument("--overwrite", action="store_true")
    conv.add_argument("--report", type=Path)
    demo = commands.add_parser("make-demo", help="Generate a short procedural video with audio")
    demo.add_argument("output", type=Path)
    demo.add_argument("--seconds", type=float, default=3)
    return p


def main():
    args = vars(parser().parse_args())
    command = args.pop("command")
    try:
        report = args.pop("report", None)
        if report and report.exists():
            raise FileExistsError(f"Report exists: {report}")
        if command == "convert" and report and report.resolve() == args["output"].resolve():
            raise ValueError("Report and video output paths must differ")
        if command == "devices":
            result = {"devices": devices(), "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe")}
            try:
                from .gpu_stereo import gpu_devices
                result["opencl_gpus"] = [{"name": d.name.strip(), "platform": d.platform.name,
                                         "driver": d.driver_version} for d in gpu_devices()]
            except Exception as exc:
                result["opencl_error"] = str(exc)
        elif command == "prepare-model":
            result = prepare_model(args.pop("output"), **args)
        elif command == "benchmark":
            if args["iterations"] <= 0:
                raise ValueError("iterations must be positive")
            frame = cv2.imread(str(args["image"])) if args["image"] else np.random.default_rng(42).integers(0,256,(360,640,3),dtype=np.uint8)
            if frame is None:
                raise ValueError("Cannot read benchmark image")
            estimator = DepthEstimator(args["model"], args["device"])
            for _ in range(3):
                estimator.infer(frame)
            times = []
            for _ in range(args["iterations"]):
                t = time.perf_counter()
                depth = estimator.infer(frame)
                times.append(time.perf_counter()-t)
            result = {"device": estimator.device, "device_name": estimator.device_name,
                      "execution_devices": estimator.execution_devices,
                      "iterations": len(times), "compile_seconds": estimator.compile_seconds,
                      "mean_ms": float(np.mean(times)*1000), "p50_ms": float(np.median(times)*1000),
                      "p95_ms": float(np.percentile(times,95)*1000), "fps": float(1/np.mean(times)),
                      "scope": "preprocessing + synchronous inference + resize; 3 warmups excluded",
                      "model_shape": [estimator.height, estimator.width],
                      "depth_range": [float(depth.min()),float(depth.max())]}
        elif command == "benchmark-render":
            from .benchmark import benchmark_render
            result = benchmark_render(**args)
        elif command == "convert":
            result = convert(args.pop("input"), args.pop("output"), **args)
        elif command == "make-demo":
            if args["seconds"] <= 0:
                raise ValueError("seconds must be positive")
            args["output"].parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                            "-t", str(args["seconds"]), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-shortest", str(args["output"])], check=True)
            result = {"output": str(args["output"])}
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            with report.open("x") as f:
                json.dump(result, f, indent=2)
                f.write("\n")
        print(json.dumps(result, indent=2))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f"arc-sbs: {exc}", file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
