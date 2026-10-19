"""WayVR overlay output in an already connected WiVRn session.

Never starts/restarts a runtime, or changes another application's overlay.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def headset_connected():
    try:
        for property_name in ('HeadsetConnected', 'SessionRunning'):
            result = subprocess.run(
                ['busctl', '--user', 'get-property', 'io.github.wivrn.Server',
                 '/io/github/wivrn/Server', 'io.github.wivrn.Server', property_name],
                capture_output=True, text=True, timeout=2, check=True)
            if result.stdout.strip() != 'b true':
                return False
        return True
    except (OSError, subprocess.SubprocessError):
        return False


class WayVROutput:
    def __init__(self):
        self.name = f'qimgv-sbs-{os.getpid()}'
        self.handle = None
        self.ctl = os.environ.get('ARC_SBS_WAYVRCTL') or shutil.which('wivrn-pro-wayvrctl') or shutil.which('wayvrctl')
        if not self.ctl:
            candidate = Path.home() / '.local/opt/wivrn-pro/bin/wivrn-pro-wayvrctl'
            if candidate.is_file():
                self.ctl = str(candidate)

    def command(self, *args):
        if not self.ctl:
            raise RuntimeError('Connected headset, but WayVR controller is unavailable')
        result = subprocess.run([self.ctl, *args], capture_output=True, text=True, timeout=8)
        if result.returncode:
            raise RuntimeError(f'WayVR {args[0]}: {result.stderr.strip() or result.stdout.strip()}')
        return result.stdout.strip()

    def present(self, presenter, image):
        if self.name not in self.command('window-list').splitlines():
            # A dedicated surface keeps UI, margins and other desktop windows out
            # of the eye split. Resolution bounds the stream, not source quality.
            # WayVR splits its ARGS field on whitespace, without shell unquoting.
            # Encode paths so Unicode and spaces survive that boundary.
            encoded = base64.b64encode(str(image).encode('utf-8')).decode('ascii')
            arguments = f'--sbs-present-encoded {encoded} --sbs-title {self.name}'
            response = self.command('process-launch', '-n', self.name, '-e',
                                    'QT_QPA_PLATFORM=wayland', '--', str(presenter),
                                    '2560x1440', 'floating', '', arguments)
            payload = json.loads(response)
            self.handle = payload.get('handle', payload)
            deadline = time.monotonic() + 6
            while self.name not in self.command('window-list').splitlines():
                if time.monotonic() > deadline:
                    raise RuntimeError('WayVR presenter did not create an overlay')
                time.sleep(.1)
        self.command('window-attrib', self.name, 'set', 'stereo', 'left_right')
        # The presenter packs two squeezed eye canvases into a 16:9 surface.
        self.command('window-attrib', self.name, 'set', 'stereo_full_frame', 'false')
        self.command('window-set-visible', self.name, '1')
        return self.name

    def hide(self):
        if self.handle is not None:
            if self.name in self.command('window-list').splitlines():
                self.command('window-set-visible', self.name, '0')
            else:
                self.handle = None

    def close(self):
        if self.handle is not None:
            try:
                self.command('process-terminate', json.dumps(self.handle))
            except (OSError, subprocess.SubprocessError, RuntimeError):
                pass
            self.handle = None
