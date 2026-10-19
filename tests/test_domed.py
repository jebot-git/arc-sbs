import json
from pathlib import Path

from arc_sbs import domed


def test_discovery_validates_socket_identity_private_record_and_process(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(tmp_path))
    monkeypatch.setenv('WAYLAND_DISPLAY', 'dome-test')
    monkeypatch.setattr(domed, 'socket_identity', lambda path: 42)
    record = tmp_path / 'dome-test.dome-session.json'
    value = {'version': 1, 'compositor_pid': 42, 'realm': 'desktop', 'control_port': 12345, 'xr_socket': 'dome-xr'}
    record.write_text(json.dumps(value)); record.chmod(0o600)
    assert domed.discover() == {'pid': 42, 'base': 'http://127.0.0.1:12345', 'socket': 'dome-xr'}
    record.chmod(0o666)
    assert domed.discover() is None
    record.chmod(0o600)
    value['compositor_pid'] = 43
    record.write_text(json.dumps(value))
    assert domed.discover() is None


def test_presenter_uses_only_the_selected_xr_socket_and_owns_its_child(monkeypatch):
    output = domed.DomeDOutput()
    context = {'pid': 42, 'base': 'http://127.0.0.1:12345', 'socket': 'dome-xr'}
    monkeypatch.setattr(output, 'status', lambda: (context, {'xr_active': True}))
    monkeypatch.setenv('WAYLAND_SOCKET', '77')
    calls = []
    class Child:
        stopped = False
        def poll(self): return None
        def terminate(self): self.stopped = True
        def wait(self, timeout): return 0
    child = Child()
    def launch(args, **options):
        calls.append((args, options)); return child
    monkeypatch.setattr(domed.subprocess, 'Popen', launch)
    output.present('/viewer with spaces', Path('/image with spaces.png'))
    output.present('/viewer with spaces', Path('/image with spaces.png'))
    assert len(calls) == 1
    arguments, options = calls[0]
    assert '--dome-present' in arguments
    assert '/image with spaces.png' in arguments
    assert options['env']['WAYLAND_DISPLAY'] == 'dome-xr'
    assert 'WAYLAND_SOCKET' not in options['env']
    output.hide()
    assert child.stopped
