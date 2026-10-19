"""Isolated conversion worker; stdout is newline-delimited JSON for the Qt UI."""
import contextlib
import json
import os
from pathlib import Path
import signal
import sys
import threading


def main():
    # Isolate FFmpeg descendants so a stuck job can be stopped as a group.
    os.setsid()
    cancelled = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: cancelled.set())

    def notify(kind, **values):
        print(json.dumps({"event": kind, **values}), file=sys.__stdout__, flush=True)

    try:
        job = json.loads(Path(sys.argv[1]).read_text())
        from .video import convert, ConversionCancelled
        with contextlib.redirect_stdout(sys.stderr):
            result = convert(job.pop("input"), job.pop("output"), **job,
                progress_callback=lambda value: notify("progress", **value),
                cancel_requested=cancelled.is_set)
        notify("completed", result=result)
        return 0
    except Exception as exc:
        if cancelled.is_set() or type(exc).__name__ == "ConversionCancelled":
            notify("cancelled")
            return 130
        notify("failed", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
