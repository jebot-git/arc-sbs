"""Qt6 integration tests use offscreen widgets and real isolated conversions."""
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

pytest.importorskip('PySide6')
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication
from arc_sbs.gui import ConversionWindow
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent


@pytest.fixture(scope='module')
def app():
    instance=QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    yield instance


@pytest.fixture
def window(app,tmp_path):
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    w=ConversionWindow(settings)
    yield w
    if w.active:
        w.cancel()
        wait_for(app,lambda:not w.active)
    w.close()
    app.processEvents()


def wait_for(app,condition,seconds=20):
    deadline=time.monotonic()+seconds
    while not condition() and time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert condition(),'Qt operation timed out'


@pytest.fixture
def gui_media(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg required')
    import openvino as ov
    from openvino import opset13 as ops
    source=tmp_path/'source with spaces.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=64x48:rate=24',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','3',
        '-c:v','libx264','-c:a','aac',str(source)],check=True)
    x=ops.parameter([1,3,28,42],ov.Type.f32)
    y=ops.reduce_mean(x,ops.constant([1]),False)
    model=tmp_path/'model.xml'
    ov.save_model(ov.Model([y],[x]),model)
    return source,model


def configure(w,media,tmp_path):
    source,model=media
    w.input_path.setText(str(source))
    w.model_path.setText(str(model))
    w.output_path.setText(str(tmp_path/'result.mp4'))
    w.acceleration.setCurrentIndex(w.acceleration.findData('CPU'))
    w.frame_limit.setValue(5)


def test_native_menu_routes_commands_and_tracks_conversion_state(window, app, monkeypatch):
    calls = []
    monkeypatch.setattr(window, 'build_job', lambda: calls.append('convert') or (_ for _ in ()).throw(ValueError('test validation')))
    window.menu_actions['convert'].trigger()
    assert calls == ['convert'] and window.status.text() == 'test validation'
    assert not window.menu_actions['cancel'].isEnabled()
    window._set_active(True)
    assert window.menu_actions['cancel'].isEnabled()
    for name in ('open', 'output', 'model', 'convert', 'options'):
        assert not window.menu_actions[name].isEnabled()
    window._set_active(False)
    assert window.menu_actions['convert'].isEnabled()
    assert not window.menu_actions['cancel'].isEnabled()
    window.menu_actions['details'].trigger()
    assert window.log_toggle.isChecked() and window.menu_actions['details'].isChecked()
    window.log_toggle.setChecked(False)
    assert not window.menu_actions['details'].isChecked()
    window.menu_actions['options'].trigger()
    assert window.advanced_toggle.isChecked()
    window.advanced_toggle.setChecked(False)
    assert not window.menu_actions['options'].isChecked()


def test_native_edit_menu_uses_focused_field_and_readonly_log(window, app):
    window.show()
    window.input_path.setText('video.mp4')
    window.input_path.setFocus()
    app.processEvents()
    window.menu_actions['selectAll'].trigger()
    assert window.input_path.selectedText() == 'video.mp4'
    window.menu_actions['copy'].trigger()
    assert app.clipboard().text() == 'video.mp4'
    window.output_path.setFocus()
    app.processEvents()
    window.menu_actions['selectAll'].trigger()
    window.menu_actions['paste'].trigger()
    assert window.output_path.text() == 'video.mp4'
    window.log_toggle.setChecked(True)
    window.log.setPlainText('details')
    window.log.setFocus()
    window.log.selectAll()
    app.processEvents()
    assert window.menu_actions['copy'].isEnabled()
    assert not window.menu_actions['cut'].isEnabled()
    assert not window.menu_actions['paste'].isEnabled()


def test_input_file_drop_decodes_paths_suggests_output_and_preserves_custom_destination(window, tmp_path, app):
    source = tmp_path / 'a movie #1 ü.mp4'
    source.touch()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    enter = QDragEnterEvent(QPoint(2, 2), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.input_path, enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(2, 2), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(window.input_path, drop)
    assert drop.isAccepted()
    assert window.input_path.text() == str(source)
    assert window.output_path.text() == str(source.with_name(source.stem + '_sbs.mp4'))
    window.output_path.setText(str(tmp_path / 'custom.mkv'))
    window.dropEvent(QDropEvent(QPointF(2, 2), Qt.DropAction.CopyAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert window.output_path.text().endswith('custom.mkv')


@pytest.mark.parametrize('kind', ['remote', 'directory', 'multiple', 'active', 'move_only'])
def test_input_drop_rejects_unsupported_imports(window, tmp_path, kind):
    source = tmp_path / 'video.mp4'; source.touch()
    mime = QMimeData()
    urls = [QUrl.fromLocalFile(str(source))]
    if kind == 'remote': urls = [QUrl('https://example.com/video.mp4')]
    if kind == 'directory': urls = [QUrl.fromLocalFile(str(tmp_path))]
    if kind == 'multiple': urls *= 2
    if kind == 'active': window.active = True
    mime.setUrls(urls)
    action = Qt.DropAction.MoveAction if kind == 'move_only' else Qt.DropAction.CopyAction
    drop = QDropEvent(QPointF(2, 2), action, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(drop)
    assert not drop.isAccepted()
    assert not window.input_path.text()
    window.active = False


def test_gui_validates_missing_input_and_protects_existing_output(window,gui_media,tmp_path):
    window.start()
    assert not window.active and 'input' in window.status.text()
    configure(window,gui_media,tmp_path)
    output=Path(window.output_path.text())
    output.write_bytes(b'keep me')
    window.start()
    assert not window.active and 'already exists' in window.status.text()
    assert output.read_bytes()==b'keep me'


def test_gui_cpu_preset_and_output_suggestion(window,tmp_path):
    window.input_path.setText(str(tmp_path/'a movie.mov'))
    window._suggest_output()
    assert window.output_path.text().endswith('a movie_sbs.mp4')
    window.output_path.setText(str(tmp_path/'chosen.mkv'))
    window.input_path.setText(str(tmp_path/'another.mp4'))
    window._suggest_output()
    assert window.output_path.text().endswith('chosen.mkv')
    window.acceleration.setCurrentIndex(window.acceleration.findData('CPU'))
    assert window.encoder.currentData()=='libx264'
    assert window.encoder.count()==1


def test_gui_conversion_completes_with_responsive_event_loop(window,gui_media,tmp_path,app):
    configure(window,gui_media,tmp_path)
    beats=[]
    timer=QTimer()
    timer.timeout.connect(lambda:beats.append(time.monotonic()))
    timer.start(10)
    window.start()
    assert window.active and not window.start_button.isEnabled()
    wait_for(app,lambda:not window.active)
    timer.stop()
    assert len(beats)>3
    assert window.result['frames']==5
    assert window.result['device']=='CPU'
    assert Path(window.result['output']).is_file()
    assert window.progress.value()==100 and window.open_folder.isEnabled()
    assert window._job_dir is None
    from arc_sbs.video import probe
    assert any(s['codec_type']=='audio' for s in probe(window.result['output'])['streams'])


def test_gui_reports_worker_failure_and_reenables_controls(window,gui_media,tmp_path,app):
    configure(window,gui_media,tmp_path)
    bad=tmp_path/'bad.xml'
    bad.write_text('not a model')
    bad.with_suffix('.bin').write_bytes(b'bad weights')
    window.model_path.setText(str(bad))
    window.start()
    wait_for(app,lambda:not window.active)
    assert window.result is None
    assert window.start_button.isEnabled()
    assert window.log.toPlainText()
    assert not Path(window.output_path.text()).exists()


def test_gui_cancellation_cleans_worker_and_preserves_destination(window,gui_media,tmp_path,app,monkeypatch):
    configure(window,gui_media,tmp_path)
    output=Path(window.output_path.text())
    output.write_bytes(b'existing output')
    window.overwrite.setChecked(True)
    window.frame_limit.setValue(0)
    build=window.build_job
    monkeypatch.setattr(window,'build_job',lambda:{**build(),'pace':True})
    original=window._event
    events=[]
    def observe(event):
        original(event)
        events.append(event)
        if event.get('phase')=='converting' and window.cancel_button.isEnabled():
            window.cancel()
    monkeypatch.setattr(window,'_event',observe)
    window.start()
    wait_for(app,lambda:not window.active)
    assert any(e.get('event')=='cancelled' for e in events)
    assert window.status.text()=='Conversion cancelled.'
    assert output.read_bytes()==b'existing output'
    assert not list(tmp_path.glob('.arc-sbs-*'))
    assert window._job_dir is None


@pytest.mark.parametrize('backend,alias', [('amd', 'AMD'), ('nvidia', 'NVIDIA')])
def test_experimental_build_gui_uses_pt_model_and_software_encoding(app, tmp_path, monkeypatch, backend, alias):
    from arc_sbs import gui
    monkeypatch.setattr(gui, 'BACKEND', backend)
    monkeypatch.setattr(gui, 'DEFAULT_DEVICE', alias)
    monkeypatch.setattr(gui, 'GPU_LABEL', f'{alias} (experimental)')
    monkeypatch.setattr(gui, 'MODEL_SUFFIX', '.pt')
    source = tmp_path / 'input.mp4'
    model = tmp_path / 'depth.pt'
    source.touch()
    model.touch()
    settings = QSettings(str(tmp_path / 'vendor.ini'), QSettings.Format.IniFormat)
    w = gui.ConversionWindow(settings)
    try:
        w.input_path.setText(str(source))
        w.model_path.setText(str(model))
        w.output_path.setText(str(tmp_path / 'output.mp4'))
        job = w.build_job()
        assert job['device'] == alias and job['renderer'] == 'opencl'
        assert job['encoder'] == 'libx264'
        w.acceleration.setCurrentIndex(1)
        assert w.build_job()['device'] == 'CPU'
        assert w.build_job()['renderer'] == 'cpu'
        w.model_path.setText(str(source))
        with pytest.raises(ValueError, match='.pt'):
            w.build_job()
    finally:
        w.close()
