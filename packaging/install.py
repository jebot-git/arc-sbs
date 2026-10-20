#!/usr/bin/env python3
"""Install a built Arc SBS bundle for the current Linux user."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, help='Prepared depth-small.xml (Intel) or depth-small.pt (AMD/NVIDIA), with provenance JSON')
    args = parser.parse_args()
    assets = Path(__file__).resolve().parent
    manifest = assets / 'backend.json'
    if manifest.exists():
        backend = json.loads(manifest.read_text())['backend']
    elif (assets.parent / 'pyproject.toml').exists():
        config = tomllib.loads((assets.parent / 'pyproject.toml').read_text())
        backend = config.get('tool', {}).get('arc-sbs', {}).get('backend', 'intel')
    else:
        parser.error('Missing backend.json; extract a fresh bundle first')
    if backend not in ('intel', 'amd', 'nvidia'):
        parser.error(f'Unknown bundle backend: {backend}')
    suffixes = ('.xml', '.bin', '.json') if backend == 'intel' else ('.pt', '.json')
    model_name = 'depth-small' + suffixes[0]
    wheels = sorted(assets.glob('*.whl'))
    if not wheels and (assets.parent / 'pyproject.toml').exists():
        version = tomllib.loads((assets.parent / 'pyproject.toml').read_text())['project']['version']
        wheels = list((assets.parent / f'dist/{backend}').glob(f'arc_sbs-{version}-*.whl'))
    if len(wheels) != 1:
        parser.error('Expected exactly one wheel; build/extract a fresh bundle first')
    wheel = wheels[0]
    uv = shutil.which('uv')
    if not uv:
        parser.error('Install uv and put it on PATH first: https://docs.astral.sh/uv/')
    model = args.model
    if model is None:
        candidate = assets.parent / 'models' / model_name
        if candidate.is_file():
            model = candidate
    if model and model.suffix != suffixes[0]:
        parser.error(f'This {backend} bundle requires a {suffixes[0]} model')
    if model:
        for suffix in suffixes:
            if not model.with_suffix(suffix).is_file():
                parser.error(f'Missing model file: {model.with_suffix(suffix)}')
    root = Path.home() / '.local/opt/arc-sbs'
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()[:12]
    release = root / 'releases' / f'{wheel.stem}-{digest}'
    venv = release / 'venv'
    release.mkdir(parents=True, exist_ok=True)
    if not (venv / 'bin/python').exists():
        subprocess.run([uv, 'venv', '--python', '3.12', str(venv)], check=True)
    # Exported requirements flatten uv's per-package indexes. Exact versions
    # and hashes still constrain every artifact; consult both official indexes
    # so PyTorch's older NumPy listings cannot shadow the locked PyPI version.
    subprocess.run([uv, 'pip', 'install', '--python', str(venv / 'bin/python'),
                    '--index-strategy', 'unsafe-best-match', '--require-hashes', '-r', str(assets / 'runtime-requirements.txt')], check=True)
    subprocess.run([uv, 'pip', 'install', '--python', str(venv / 'bin/python'),
                    '--no-deps', str(wheel)], check=True)
    subprocess.run([str(venv / 'bin/arc-sbs'), '--version'], check=True, cwd='/tmp')
    # Validate GPU shader and Qt resources before changing the active release.
    subprocess.run([str(venv / 'bin/python'), '-c',
                    'from pathlib import Path; import arc_sbs; from PySide6 import QtWidgets; '
                    'assert (Path(arc_sbs.__file__).parent / "stereo.cl").is_file()'], check=True)
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    if model:
        target = data / 'arc-sbs/models'
        target.mkdir(parents=True, exist_ok=True)
        for suffix in suffixes:
            source = model.with_suffix(suffix)
            destination = target / f'depth-small{suffix}'
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
        # Move an existing selection of this exact model to the installed copy.
        migration = ('from PySide6.QtCore import QSettings; from pathlib import Path; '
                     's=QSettings("ArcSBS", "Converter"); '
                     f'old=Path({json.dumps(str(model.resolve()))}); '
                     f'new={json.dumps(str(target / model_name))}; '
                     'selected=s.value("model", "", type=str); '
                     's.setValue("model", new) if selected and Path(selected).resolve()==old else None; '
                     's.sync()')
        subprocess.run([str(venv / 'bin/python'), '-c', migration], check=True)
    pending = root / 'current.new'
    pending.unlink(missing_ok=True)
    pending.symlink_to(release, target_is_directory=True)
    pending.replace(root / 'current')
    binaries = Path.home() / '.local/bin'
    binaries.mkdir(parents=True, exist_ok=True)
    for command in ('arc-sbs', 'arc-sbs-gui'):
        launcher = binaries / command
        launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(root / 'current/venv/bin' / command)) + ' "$@"\n')
        launcher.chmod(0o755)
    icons = data / 'icons/hicolor/scalable/apps'
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copy2(assets / 'arc-sbs.svg', icons / 'arc-sbs.svg')
    applications = data / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    # Desktop Exec uses double-quoted arguments, not shell quoting.
    executable = str(binaries / 'arc-sbs-gui').replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    desktop = applications / 'arc-sbs.desktop'
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Arc SBS\n'
                       'Comment=Convert 2D video to side-by-side 3D\n'
                       f'Exec="{executable}" %f\nIcon=arc-sbs\nTerminal=false\n'
                       'Categories=AudioVideo;Video;\nStartupNotify=true\n')
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(applications)], check=True)
    print(f'Installed: {binaries / "arc-sbs-gui"}\nDesktop entry: {desktop}\nRelease: {release}')
    if not model:
        print('No model copied. Select an existing model in the GUI or pass --model to this installer.')


if __name__ == '__main__':
    main()
