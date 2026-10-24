"""Default native FLTK GUI, using the unchanged isolated conversion worker."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    if os.environ.get('DE_XR_TOOLKIT', 'FLTK') == 'Qt6':
        from .gui import main as qt_main
        return qt_main()
    executable = os.environ.get('ARC_SBS_FLTK_BINARY')
    if not executable:
        local = Path(__file__).resolve().parents[2] / 'build-fltk/arc-sbs-fltk'
        executable = str(local) if local.is_file() else shutil.which('arc-sbs-fltk')
    if not executable:
        print('Build the FLTK frontend with cmake -S fltk -B build-fltk -DCMAKE_PREFIX_PATH=<FLTK prefix>', file=sys.stderr)
        return 1
    env = dict(os.environ, ARC_SBS_PYTHON=sys.executable)
    prefix = os.environ.get('DE_XR_FLTK_PREFIX')
    if not prefix:
        candidate = Path(executable).resolve().parents[2] / '_toolkit'
        if (candidate / 'lib/libfltk.so.1.4').is_file():
            prefix = str(candidate)
    if prefix:
        env['LD_LIBRARY_PATH'] = str(Path(prefix) / 'lib') + ':' + env.get('LD_LIBRARY_PATH', '')

    from .backend import DEFAULT_DEVICE, DEFAULT_ENCODER, MODEL_SUFFIX
    env.update(ARC_SBS_DEFAULT_DEVICE=DEFAULT_DEVICE, ARC_SBS_DEFAULT_ENCODER=DEFAULT_ENCODER, ARC_SBS_MODEL_SUFFIX=MODEL_SUFFIX)
    from .paths import model_path
    env.setdefault('ARC_SBS_MODEL', str(model_path()))
    return subprocess.call([executable, *sys.argv[1:]], env=env)


if __name__ == '__main__':
    raise SystemExit(main())
