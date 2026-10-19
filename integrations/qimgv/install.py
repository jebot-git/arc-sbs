#!/usr/bin/env python3
"""Fetch pinned qimgv, apply the SBS patch, build, test and install for this user."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess

UPSTREAM = '3127a2d211b124ad4fcf853d01e6df9323bdfdc3'


def run(*args, **kwargs):
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path.home() / 'qimgv-sbs-source')
    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    patch = Path(__file__).resolve().with_name('qimgv-sbs.patch')
    if not source.exists():
        run('git', 'clone', '--no-checkout', 'https://github.com/easymodo/qimgv.git', source)
        run('git', '-C', source, 'checkout', '--detach', UPSTREAM)
    applied = subprocess.run(['git', '-C', str(source), 'apply', '--reverse', '--check', str(patch)],
                             capture_output=True).returncode == 0
    if not applied:
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        dirty = subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True).strip()
        if head != UPSTREAM or dirty:
            parser.error('Source is neither the clean pinned upstream nor an already patched tree; use a fresh --source directory')
        run('git', '-C', source, 'apply', '--check', patch)
        run('git', '-C', source, 'apply', patch)
    prefix = Path.home() / '.local/opt/qimgv-sbs'
    build = source / 'build'
    run('cmake', '-S', source, '-B', build, '-DUSE_QT5=OFF', '-DVIDEO_SUPPORT=OFF',
        '-DOPENCV_SUPPORT=OFF', '-DSBS_TESTS=ON', '-DCMAKE_BUILD_TYPE=Release',
        f'-DCMAKE_INSTALL_PREFIX={prefix}')
    run('cmake', '--build', build, '--parallel', min(os.cpu_count() or 1, 8))
    run('ctest', '--test-dir', build, '--output-on-failure')
    run('cmake', '--install', build)
    launcher = Path.home() / '.local/bin/qimgv-sbs'
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(str(prefix / 'bin/qimgv')) + ' "$@"\n')
    launcher.chmod(0o755)
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    desktop = data / 'applications/qimgv-sbs.desktop'
    desktop.parent.mkdir(parents=True, exist_ok=True)
    executable = str(launcher).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
    desktop.write_text('[Desktop Entry]\nType=Application\nName=qimgv SBS\n'
                       'Comment=View images with automatic Intel Arc 3D conversion in XR\n'
                       f'Exec="{executable}" %f\n'
                       f'Icon={prefix / "share/icons/hicolor/scalable/apps/qimgv.svg"}\n'
                       'Terminal=false\nCategories=Graphics;Viewer;\n'
                       'MimeType=image/jpeg;image/png;image/webp;image/bmp;image/tiff;\n')
    if shutil.which('update-desktop-database'):
        run('update-desktop-database', desktop.parent)
    print(f'Installed {launcher}. Install Arc SBS >=0.3.0 and its model before conversion.')


if __name__ == '__main__':
    main()
