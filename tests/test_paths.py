from arc_sbs.paths import cache_path, model_path


def test_installed_paths_and_override(tmp_path, monkeypatch):
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
