"""Writable per-user runtime data, independent of the launch directory."""
import os
from pathlib import Path

from .backend import MODEL_SUFFIX

SYSTEM_MODELS = Path('/usr/local/share/arc-sbs/models')


def data_path():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'arc-sbs'


def model_path():
    if os.environ.get('ARC_SBS_MODEL'):
        return Path(os.environ['ARC_SBS_MODEL']).expanduser()
    installed = data_path() / f'models/depth-small{MODEL_SUFFIX}'
    shared = SYSTEM_MODELS / f'depth-small{MODEL_SUFFIX}'
    candidates = [installed, Path.cwd() / f'models/depth-small{MODEL_SUFFIX}', shared]
    return next((path for path in candidates if path.is_file()), installed)


def cache_path():
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'arc-sbs/openvino'
