"""Writable per-user runtime data, independent of the launch directory."""
import os
from pathlib import Path


def data_path():
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'arc-sbs'


def model_path():
    if os.environ.get('ARC_SBS_MODEL'):
        return Path(os.environ['ARC_SBS_MODEL']).expanduser()
    installed = data_path() / 'models/depth-small.xml'
    candidates = [installed, Path.cwd() / 'models/depth-small.xml']
    return next((path for path in candidates if path.is_file()), installed)


def cache_path():
    return Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'arc-sbs/openvino'
