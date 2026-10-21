from arc_sbs import paths
from arc_sbs.paths import cache_path, model_path


def test_installed_paths_and_override(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, 'SYSTEM_MODELS', tmp_path / 'system-models')
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('ARC_SBS_MODEL', raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    installed = tmp_path / 'data/arc-sbs/models/depth-small.xml'
    assert model_path() == installed
    local = tmp_path / 'models/depth-small.xml'
    local.parent.mkdir()
    local.touch()
    assert model_path() == local
    installed.parent.mkdir(parents=True)
    installed.touch()
    assert model_path() == installed
    monkeypatch.setenv('ARC_SBS_MODEL', str(local))
    assert model_path() == local
    assert cache_path() == tmp_path / 'cache/arc-sbs/openvino'


def test_system_model_fallback_preserves_user_and_environment_overrides(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('ARC_SBS_MODEL', raising=False)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'user-data'))
    shared = tmp_path / 'shared'
    shared.mkdir()
    system_model = shared / ('depth-small' + paths.MODEL_SUFFIX)
    system_model.touch()
    monkeypatch.setattr(paths, 'SYSTEM_MODELS', shared)
    assert model_path() == system_model
    user_model = paths.data_path() / 'models' / system_model.name
    user_model.parent.mkdir(parents=True)
    user_model.touch()
    assert model_path() == user_model
    monkeypatch.setenv('ARC_SBS_MODEL', str(tmp_path / 'custom-model.xml'))
    assert model_path() == tmp_path / 'custom-model.xml'
