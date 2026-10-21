"""DomeD-only Qt appearance adapter; other compositors retain native Qt defaults."""
import json
import os
from pathlib import Path
from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontInfo, QPalette


def in_dome_session():
    return bool({'Dome', 'DomeD', 'dome'} & set(os.environ.get('XDG_CURRENT_DESKTOP', '').split(':')))


class DomeAppearance(QObject):
    changed = Signal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        self.enabled = in_dome_session()
        self.original_font = QFont(app.font())
        self.original_palette = QPalette(app.palette())
        self.opacity = 1.0
        self.path = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'dome/appearance.json'
        self.watcher = QFileSystemWatcher(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.watcher.fileChanged.connect(lambda _: self.timer.start())
        self.watcher.directoryChanged.connect(lambda _: self.timer.start())
        self.timer.timeout.connect(self.refresh)
        if self.enabled:
            self.refresh()

    def watch(self):
        paths = self.watcher.files() + self.watcher.directories()
        if paths:
            self.watcher.removePaths(paths)
        directory = self.path.parent
        while not directory.exists() and directory != directory.parent:
            directory = directory.parent
        self.watcher.addPath(str(directory))
        if self.path.is_file():
            self.watcher.addPath(str(self.path))

    def refresh(self):
        if not self.enabled:
            return
        try:
            self.reload()
        finally:
            self.watch()

    def reload(self):
        try:
            if self.path.exists() and self.path.stat().st_size > 65536:
                return
            value = json.loads(self.path.read_text()) if self.path.exists() else {}
            if not isinstance(value, dict):
                return
            identifier = value.get('font', 'GNU Unifont / Sans-Serif')
            size = value.get('font_size', 16)
            opacity = value.get('opacity', 248)
            if not isinstance(identifier, str) or len(identifier) > 256:
                return
            if type(size) is not int or not 8 <= size <= 24:
                return
            if type(opacity) is not int or not 160 <= opacity <= 255:
                return
            if 'light' in value and type(value['light']) is not bool:
                return
            accent = QColor(value.get('accent', '#6babff'))
            if not accent.isValid():
                return
        except (OSError, ValueError, TypeError):
            return
        font = QFont(self.original_font)
        parts = identifier.split(' / ')
        if (len(parts) == 2 and parts[0] in QFontDatabase.families()
                and parts[1] in QFontDatabase.styles(parts[0])
                and not QFontDatabase.isSmoothlyScalable(*parts)):
            candidate = QFont(parts[0])
            candidate.setStyleName(parts[1])
            candidate.setPixelSize(size)
            candidate.setStyleStrategy(QFont.StyleStrategy.PreferBitmap | QFont.StyleStrategy.NoAntialias)
            resolved = QFontInfo(candidate)
            if resolved.family() == parts[0] and resolved.pixelSize() == size:
                font = candidate
        light = value.get('light', False)
        surface = QColor('#e5e9ef' if light else '#1f252d')
        text = QColor('#1e252e' if light else '#e7ebf1')
        muted = QColor('#687484' if light else '#929aa5')
        palette = QPalette(self.original_palette)
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
            for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button, QPalette.ColorRole.ToolTipBase):
                palette.setColor(group, role, surface)
            for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.ToolTipText):
                palette.setColor(group, role, muted if group == QPalette.ColorGroup.Disabled else text)
            palette.setColor(group, QPalette.ColorRole.Highlight, QColor('#c7d5e8' if light else '#3f4d60'))
            palette.setColor(group, QPalette.ColorRole.HighlightedText, text)
            palette.setColor(group, QPalette.ColorRole.Accent, accent)
            palette.setColor(group, QPalette.ColorRole.Link, accent)
            palette.setColor(group, QPalette.ColorRole.PlaceholderText, muted)
        self.app.setFont(font)
        self.app.setPalette(palette)
        self.opacity = opacity / 255
        self.changed.emit()
