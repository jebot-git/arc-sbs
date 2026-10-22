"""Real bitmap resolution and atomic appearance changes without user settings."""
import json
import os
import time
from pathlib import Path
import pytest
pytest.importorskip('PySide6')
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QPalette
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLabel
from arc_sbs.appearance import DomeAppearance
from arc_sbs.gui import ConversionWindow


def wait(app, predicate):
    until = time.monotonic() + 3
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def environment(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    original, palette = QFont(app.font()), QPalette(app.palette())
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    (tmp_path / 'dome').mkdir()
    path = tmp_path / 'dome/appearance.json'
    assert QFontDatabase.addApplicationFont(str(Path(__file__).parent / 'fonts/ttyp0-13.bdf')) >= 0
    yield app, path, original, palette
    app.setFont(original)
    app.setPalette(palette)


def save(path, value):
    pending = path.with_suffix('.new')
    pending.write_text(json.dumps(value))
    pending.replace(path)


def test_bitmap_title_live_changes_and_invalid_strikes(environment, monkeypatch, tmp_path):
    app, path, original, _ = environment
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', 'Dome')
    save(path, {'font': 'Ttyp0 / Regular', 'font_size': 13})
    window = ConversionWindow(QSettings(str(tmp_path / 'gui.ini'), QSettings.Format.IniFormat))
    window.show()
    title = next(label for label in window.findChildren(QLabel) if label.text() == 'Arc SBS')
    app.processEvents()
    for widget in (title, window.input_path):
        assert QFontInfo(widget.font()).family().startswith('Dome Qt BDF ')
        assert QFontInfo(widget.font()).pixelSize() == 13
        assert widget.font().styleName() == 'Regular'
    save(path, {'font': 'Ttyp0 / Regular', 'font_size': 14, 'light': True})
    wait(app, lambda: QFontInfo(app.font()).pixelSize() == 14)
    wait(app, lambda: app.palette().color(QPalette.ColorRole.Window).name() == '#e5e9ef')
    save(path, {'font': 'Ttyp0 / Regular', 'font_size': 13})
    wait(app, lambda: QFontInfo(app.font()).family().startswith('Dome Qt BDF ') and QFontInfo(title.font()).family() == QFontInfo(app.font()).family() and QFontInfo(title.font()).pixelSize() == 13)
    assert QFontInfo(title.font()).pixelSize() == 13
    path.write_text('{')
    for _ in range(25):
        app.processEvents(); time.sleep(.01)
    assert QFontInfo(title.font()).family().startswith('Dome Qt BDF ')
    window.dome_appearance.watcher.blockSignals(True)
    window.dome_appearance.timer.stop()
    window.close()


def test_other_compositor_uses_default_bdf_without_session_palette(environment, monkeypatch):
    app, path, original, palette = environment
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', 'KDE')
    save(path, {'font': 'Ttyp0 / Regular', 'font_size': 13, 'light': True})
    adapter = DomeAppearance(app)
    assert not adapter.enabled
    assert QFontInfo(app.font()).family().startswith("Dome Qt BDF ")
    assert QFontInfo(app.font()).pixelSize() == 16
    assert app.palette() == palette
    assert not adapter.watcher.files()
