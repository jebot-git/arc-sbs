"""Backend policy, isolated packaging, vendor selection and portable exports."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tomllib

import numpy as np
import pytest

from arc_sbs import depth, gpu_stereo, model, torch_depth

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('backend,version', [('intel', None), ('amd', '2.8.0+rocm6.4'),
                                           ('nvidia', '2.8.0+cu128')])
def test_build_freezes_backend_and_dependencies_without_mutating_checkout(tmp_path, backend, version):
    spec = importlib.util.spec_from_file_location('arc_sbs_build', ROOT / 'packaging/build.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    original = (ROOT / 'src/arc_sbs/_build_backend.py').read_text()
    builder.stage_source(ROOT, tmp_path, backend)
    config = tomllib.loads((tmp_path / 'pyproject.toml').read_text())
    assert (tmp_path / 'src/arc_sbs/_build_backend.py').read_text() == f'BACKEND = {backend!r}\n'
    assert (ROOT / 'src/arc_sbs/_build_backend.py').read_text() == original
    assert not (tmp_path / 'models').exists()
    if version:
        assert f'torch=={version}' in config['project']['dependencies']
        assert not any(d.startswith('openvino') for d in config['project']['dependencies'])
        assert config['tool']['uv']['sources']['torch']['index'] == 'pytorch-runtime'
    else:
        assert any(d.startswith('openvino') for d in config['project']['dependencies'])
    rebuilt = tmp_path / 'rebuilt'
    rebuilt.mkdir()
    builder.stage_source(tmp_path, rebuilt, backend)
    assert tomllib.loads((rebuilt / 'pyproject.toml').read_text()) == config


@pytest.mark.parametrize('backend,vendor,name,match', [
    ('intel', 'Intel(R) Corporation', 'Intel Arc A770', True),
    ('intel', 'Intel', 'UHD 770', False),
    ('amd', 'Advanced Micro Devices, Inc.', 'Radeon RX 7900', True),
    ('amd', 'NVIDIA Corporation', 'RTX 4090', False),
    ('nvidia', 'NVIDIA Corporation', 'RTX 4090', True),
    ('nvidia', 'Intel', 'Intel Arc A770', False),
])
def test_renderer_uses_built_vendor(monkeypatch, backend, vendor, name, match):
    monkeypatch.setattr(gpu_stereo, 'BACKEND', backend)
    assert gpu_stereo.matches_backend(SimpleNamespace(vendor=vendor, name=name)) is match


@pytest.mark.parametrize('backend,alias', [('amd', 'AMD'), ('nvidia', 'NVIDIA')])
def test_torch_device_selection_never_silently_falls_back(monkeypatch, backend, alias):
    monkeypatch.setattr(torch_depth, 'BACKEND', backend)
    monkeypatch.setattr(torch_depth, 'DEFAULT_DEVICE', alias)
    cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 2)
    runtime = SimpleNamespace(cuda=cuda)
    assert torch_depth.select_torch_device(runtime, alias) == 'cuda:0'
    assert torch_depth.select_torch_device(runtime, 'gpu.1') == 'cuda:1'
    assert torch_depth.select_torch_device(runtime, 'CPU') == 'cpu'
    for invalid in ('ARC', 'GPU.2', 'GPU.-1', 'cuda:0'):
        with pytest.raises(ValueError):
            torch_depth.select_torch_device(runtime, invalid)
    cuda.is_available = lambda: False
    with pytest.raises(ValueError, match='unavailable'):
        torch_depth.select_torch_device(runtime, alias)


def test_factory_dispatches_without_openvino(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(depth, 'BACKEND', 'amd')
    monkeypatch.setattr(torch_depth, 'TorchDepthEstimator', lambda *args: sentinel)
    assert depth.DepthEstimator('model.pt', 'AMD') is sentinel


def test_real_torchscript_cpu_inference_and_metadata_validation(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    monkeypatch.setattr(torch_depth, 'torch_runtime', lambda: torch)
    class Tiny(torch.nn.Module):
        def forward(self, x):
            return x[:, 0]
    traced = torch.jit.trace(Tiny(), torch.zeros(1, 3, 56, 84))
    path = tmp_path / 'tiny.pt'
    metadata = {'model_id': depth.MODEL_ID, 'revision': depth.MODEL_REVISION,
                'shape': [1, 3, 56, 84], 'format': 'torchscript', 'near_is_large': True}
    def save():
        torch.jit.save(traced, str(path), _extra_files={'arc_sbs.json': json.dumps(metadata)})
    save()
    estimator = torch_depth.TorchDepthEstimator(path, 'CPU')
    frame = np.full((24, 48, 3), 127, np.uint8)
    assert estimator.infer(frame).shape == (24, 48)
    assert np.isfinite(estimator.infer_native(frame)).all()
    metadata['revision'] = 'wrong'
    save()
    with pytest.raises(ValueError, match='pinned'):
        torch_depth.TorchDepthEstimator(path, 'CPU')
    metadata['revision'] = depth.MODEL_REVISION
    metadata['shape'] = [1, 3, 55, 84]
    save()
    with pytest.raises(ValueError, match='static'):
        torch_depth.TorchDepthEstimator(path, 'CPU')


@pytest.mark.parametrize('backend,hip,cuda', [('amd', None, '12.8'),
                                            ('nvidia', '6.4', None),
                                            ('amd', None, None)])
def test_wrong_torch_distribution_is_rejected(monkeypatch, backend, hip, cuda):
    import sys
    monkeypatch.setattr(torch_depth, 'BACKEND', backend)
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(version=SimpleNamespace(hip=hip, cuda=cuda)))
    with pytest.raises(RuntimeError, match='requires PyTorch'):
        torch_depth.torch_runtime()


@pytest.mark.parametrize('backend,alias', [('amd', 'AMD'), ('nvidia', 'NVIDIA')])
def test_experimental_cli_paths_and_defaults(monkeypatch, tmp_path, backend, alias):
    from arc_sbs import cli, paths
    monkeypatch.setattr(cli, 'DEFAULT_DEVICE', alias)
    monkeypatch.setattr(cli, 'DEFAULT_ENCODER', 'libx264')
    monkeypatch.setattr(paths, 'MODEL_SUFFIX', '.pt')
    monkeypatch.delenv('ARC_SBS_MODEL', raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    args = cli.parser().parse_args(['convert', 'in.mp4', 'out.mp4'])
    assert args.device == alias and args.encoder == 'libx264'
    assert args.model == tmp_path / 'arc-sbs/models/depth-small.pt'
