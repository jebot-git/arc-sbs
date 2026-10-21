"""Opt-in output to the caller's DomeD compositor and its XR Wayland realm."""
import json
import os
from pathlib import Path
import socket
import stat
import struct
import subprocess
import urllib.request


def socket_identity(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        connection.connect(str(path))
        pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    executable = os.readlink(f'/proc/{pid}/exe').removesuffix(' (deleted)')
    if uid != os.getuid() or Path(executable).name != 'dome-compositor':
        raise ValueError('Wayland socket is not owned by this user’s DomeD compositor')
    return pid


def discover():
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/nonexistent'))
    display = os.environ.get('WAYLAND_DISPLAY', '')
    if not display:
        return None
    path = Path(display) if Path(display).is_absolute() else runtime / display
    try:
        pid = socket_identity(path)
        record = Path(str(path) + '.dome-session.json')
        info = record.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_size > 4096:
            return None
        value = json.loads(record.read_text())
        if value.get('version') != 1 or value.get('compositor_pid') != pid:
            return None
        port = value.get('control_port')
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            return None
        xr = value.get('xr_socket', '')
        xr_path = Path(xr) if Path(xr).is_absolute() else runtime / xr
        if not xr or socket_identity(xr_path) != pid:
            return None
        return {'pid': pid, 'base': f'http://127.0.0.1:{port}', 'socket': xr}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def in_session():
    """Keep a Dome caller in its own compositor, including while XR is idle."""
    if 'dome' in os.environ.get('XDG_CURRENT_DESKTOP', '').lower():
        return True
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', '/nonexistent'))
    display = os.environ.get('WAYLAND_DISPLAY', '')
    if display:
        try:
            socket_identity(Path(display) if Path(display).is_absolute() else runtime / display)
            return True
        except (OSError, ValueError):
            pass
    return False


def output_backend(requested='auto'):
    # A saved WayVR preference must not move media out of the active Dome session.
    if requested == 'domed' or in_session():
        return 'domed'
    return 'wayvr'


class DomeDOutput:
    def __init__(self):
        self.context = None
        self.child = None
        self.name = f'qimgvr-dome-sbs-{os.getpid()}'
        # Loopback IPC never goes through a user's HTTP proxy.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def status(self):
        context = discover()
        if not context:
            return None
        try:
            with self.http.open(context['base'] + '/api/integrations/qimgvr', timeout=2) as response:
                value = json.loads(response.read(4097))
            if (value.get('version') == 1 and value.get('compositor_pid') == context['pid']
                    and value.get('xr_socket') == context['socket']
                    and value.get('sbs_app_id') == 'org.qimgvr.Sbs'):
                return context, value
        except (OSError, ValueError):
            pass
        return None

    def connected(self):
        status = self.status()
        return bool(status and status[1].get('xr_active') is True)

    def present(self, presenter, image):
        status = self.status()
        if not status or status[1].get('xr_active') is not True:
            raise RuntimeError('The caller’s DomeD XR session is not active')
        context = status[0]
        if self.context != context:
            self.close()
            self.context = context
        if self.child is None or self.child.poll() is not None:
            env = dict(os.environ, WAYLAND_DISPLAY=context['socket'], QT_QPA_PLATFORM='wayland', XDG_CURRENT_DESKTOP='Dome')
            env.pop('WAYLAND_SOCKET', None)
            self.child = subprocess.Popen([str(presenter), '--dome-present', '--sbs-present', str(image),
                                           '--sbs-title', self.name], env=env,
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=None)
        return self.name

    def hide(self):
        # Terminate only our presenter; a future image creates a fresh XR window.
        self.close()

    def close(self):
        child, self.child = self.child, None
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
