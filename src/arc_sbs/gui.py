"""Small Qt6 conversion UI. Conversion and GPU drivers live in a child process."""
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile

from .backend import BACKEND, DEFAULT_DEVICE, GPU_LABEL, MODEL_SUFFIX

try:
    from PySide6.QtCore import QEvent, QProcess, QSettings, QTimer, QUrl, Qt, Signal
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
        QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
        QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget)
except ImportError as exc:
    raise SystemExit('Qt6 GUI requires the gui extra: uv sync --extra gui') from exc


def default_model():
    from .paths import model_path
    return str(model_path())


class ConversionWindow(QMainWindow):
    job_finished = Signal(str)

    def __init__(self, settings=None):
        super().__init__()
        self.settings = settings if settings is not None else QSettings('ArcSBS', 'Converter')
        self.process = QProcess(self)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.kill_timer = QTimer(self)
        self.kill_timer.setSingleShot(True)
        self.kill_timer.timeout.connect(self._force_stop)
        self.active = False
        self.result = None
        self._terminal = None
        self._close_pending = False
        self._job_dir = None
        self._scratch = None
        self._stdout_buffer = ''
        self._auto_output = ''
        self.setWindowTitle('Arc SBS — Video converter')
        self.resize(840, 680)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        title = QLabel('Arc SBS')
        title.setStyleSheet('font-size: 26px; font-weight: 600;')
        root.addWidget(title)
        subtitle = QLabel('Convert a 2D video to side-by-side 3D')
        root.addWidget(subtitle)

        settings_panel = QWidget()
        settings_layout = QVBoxLayout(settings_panel)
        settings_layout.setContentsMargins(0, 0, 8, 0)
        settings_layout.setSpacing(12)
        self.form = QWidget()
        form = QFormLayout(self.form)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        self.input_path, row = self._path_row('Choose an input video', self._browse_input)
        self.input_path.setPlaceholderText('Choose a video or drop one here')
        self.input_path.setAcceptDrops(True)
        self.input_path.installEventFilter(self)
        self.setAcceptDrops(True)
        form.addRow('Input video', row)
        self.output_path, row = self._path_row('Choose an .mp4 or .mkv destination', self._browse_output)
        self.output_path.setAcceptDrops(False)
        form.addRow('Output video', row)
        self.overwrite = QCheckBox('Replace existing output')
        form.addRow('', self.overwrite)
        self.model_path, row = self._path_row(f'Depth model ({MODEL_SUFFIX})', self._browse_model)
        self.model_path.setAcceptDrops(False)
        self.model_path.setText(self.settings.value('model', default_model(), type=str))
        form.addRow('Depth model', row)
        self.layout_choice = QComboBox()
        self.layout_choice.addItem('Half SBS — same frame size', 'half')
        self.layout_choice.addItem('Full SBS — double width', 'full')
        form.addRow('Output layout', self.layout_choice)
        self.quality = QComboBox()
        self.quality.addItem('Reference — full-resolution depth processing', 'full')
        self.quality.addItem('Fast — sampled depth normalization', 'sampled')
        self.quality.addItem('Experimental — process at model resolution', 'model')
        self.quality.setToolTip('Fast approximates global depth bounds. Experimental changes edges more.')
        form.addRow('Depth processing', self.quality)
        self.acceleration = QComboBox()
        self.acceleration.addItem(GPU_LABEL, DEFAULT_DEVICE)
        self.acceleration.addItem('CPU', 'CPU')
        form.addRow('Acceleration', self.acceleration)
        settings_layout.addWidget(self.form)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText('More options')
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        settings_layout.addWidget(self.advanced_toggle)
        self.advanced = QGroupBox()
        options = QFormLayout(self.advanced)
        self.encoder = QComboBox()
        self._set_encoders()
        options.addRow('Video encoder', self.encoder)
        self.strength = QDoubleSpinBox()
        self.strength.setRange(0, 10)
        self.strength.setDecimals(2)
        self.strength.setSingleStep(0.25)
        self.strength.setValue(2.5)
        self.strength.setSuffix(' %')
        self.strength.setToolTip('Binocular disparity span as a percentage of source width.')
        options.addRow('Depth strength', self.strength)
        self.convergence = QDoubleSpinBox()
        self.convergence.setRange(0, 1)
        self.convergence.setSingleStep(0.05)
        self.convergence.setValue(0.5)
        self.convergence.setToolTip('Screen-plane depth: 0 = far, 1 = near.')
        options.addRow('Convergence', self.convergence)
        self.swap = QCheckBox('Right eye first')
        options.addRow('Eye order', self.swap)
        self.frame_limit = QSpinBox()
        self.frame_limit.setRange(0, 100000000)
        self.frame_limit.setSpecialValueText('Entire video')
        options.addRow('Frame limit', self.frame_limit)
        self.vaapi_path = QLineEdit('/dev/dri/by-path/pci-0000:03:00.0-render')
        options.addRow('Intel media device', self.vaapi_path)
        self.advanced.hide()
        settings_layout.addWidget(self.advanced)
        self.note = QLabel('SDR video with square pixels and even dimensions. Audio is included when present.')
        self.note.setWordWrap(True)
        settings_layout.addWidget(self.note)
        settings_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(settings_panel)
        root.addWidget(scroll, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.status = QLabel('Choose an input video to begin.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.open_folder = QPushButton('Open output folder')
        self.open_folder.setEnabled(False)
        self.open_folder.clicked.connect(self._open_folder)
        buttons.addWidget(self.open_folder)
        buttons.addStretch()
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self.cancel_button)
        self.start_button = QPushButton('Convert video')
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start)
        buttons.addWidget(self.start_button)
        root.addLayout(buttons)
        self.log_toggle = QToolButton()
        self.log_toggle.setText('Show details')
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(lambda visible: self.log.setVisible(visible))
        root.addWidget(self.log_toggle)
        self.log = QPlainTextEdit()
        self.log.setAcceptDrops(False)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(300)
        self.log.setMaximumHeight(170)
        self.log.hide()
        root.addWidget(self.log)
        self.input_path.editingFinished.connect(self._suggest_output)
        self.acceleration.currentIndexChanged.connect(self._set_encoders)

    @staticmethod
    def _path_row(placeholder, handler):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        layout.addWidget(field, 1)
        button = QPushButton('Browse…')
        button.clicked.connect(handler)
        layout.addWidget(button)
        return field, row

    def _browse_input(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Input video', self.settings.value('input_dir', '', type=str),
            'Videos (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts);;All files (*)')
        if path:
            self.input_path.setText(path)
            self._suggest_output()

    def _browse_output(self):
        path, selected = QFileDialog.getSaveFileName(self, 'Output video', self.output_path.text(),
            'MP4 video (*.mp4);;Matroska video (*.mkv)', options=QFileDialog.Option.DontConfirmOverwrite)
        if path:
            self.output_path.setText(path if Path(path).suffix else path+('.mkv' if 'Matroska' in selected else '.mp4'))

    def _browse_model(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Depth model', self.model_path.text(), f'Depth model (*{MODEL_SUFFIX})')
        if path:
            self.model_path.setText(path)

    def _suggest_output(self):
        text = self.input_path.text().strip()
        if text and (not self.output_path.text().strip() or self.output_path.text() == self._auto_output):
            source = Path(text).expanduser()
            self._auto_output = str(source.with_name(source.stem+'_sbs.mp4'))
            self.output_path.setText(self._auto_output)

    def _toggle_advanced(self, checked):
        self.advanced.setVisible(checked)
        self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def _set_encoders(self):
        self.encoder.clear()
        if self.acceleration.currentData() == 'ARC':
            for label, value in [('H.264 — Intel hardware', 'h264_vaapi'),
                                 ('HEVC — Intel hardware', 'hevc_vaapi'), ('AV1 — Intel hardware', 'av1_vaapi')]:
                self.encoder.addItem(label, value)
        self.encoder.addItem('H.264 — software', 'libx264')

    def build_job(self):
        def existing_file(field, name):
            text = field.text().strip()
            if not text:
                raise ValueError(f'Choose {name}.')
            path = Path(text).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f'{name.capitalize()} does not exist: {path}')
            return path
        source = existing_file(self.input_path, 'an input video')
        model = existing_file(self.model_path, 'a depth model')
        if model.suffix.lower() != MODEL_SUFFIX or (BACKEND == 'intel' and not model.with_suffix('.bin').is_file()):
            raise ValueError(f'Choose a {MODEL_SUFFIX} depth model for this {BACKEND} build (XML requires matching BIN weights).')
        text = self.output_path.text().strip()
        if not text:
            raise ValueError('Choose an output video.')
        output = Path(text).expanduser().resolve()
        if output.suffix.lower() not in ('.mp4', '.mkv'):
            raise ValueError('Output must end in .mp4 or .mkv.')
        if output in (source, model, model.with_suffix('.bin')):
            raise ValueError('Output must be different from the input and model files.')
        if output.exists() and (not output.is_file() or not self.overwrite.isChecked()):
            raise ValueError('Output already exists. Choose another name or enable Replace existing output.')
        gpu = self.acceleration.currentData() != 'CPU'
        return {'input': str(source), 'output': str(output), 'model': str(model),
            'device': DEFAULT_DEVICE if gpu else 'CPU', 'renderer': 'opencl' if gpu else 'cpu',
            'encoder': self.encoder.currentData(), 'layout': self.layout_choice.currentData(),
            'depth_processing': self.quality.currentData(), 'pipeline': 'overlap',
            'strength': self.strength.value()/100, 'convergence': self.convergence.value(),
            'swap': self.swap.isChecked(), 'frames': self.frame_limit.value() or None,
            'vaapi_device': self.vaapi_path.text().strip(), 'overwrite': self.overwrite.isChecked()}

    def _set_active(self, active):
        self.active = active
        self.form.setEnabled(not active)
        self.advanced.setEnabled(not active)
        self.advanced_toggle.setEnabled(not active)
        self.start_button.setEnabled(not active)
        self.cancel_button.setEnabled(active)

    def start(self):
        if self.active:
            return
        try:
            job = self.build_job()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.settings.setValue('model', job['model'])
        self.settings.setValue('input_dir', str(Path(job['input']).parent))
        self.settings.sync()
        self._job = job
        self.result = None
        self._terminal = None
        self._error_message = ""
        self._cancel_sent = False
        self._scratch = None
        self._stdout_buffer = ''
        self._job_dir = tempfile.TemporaryDirectory(prefix='arc-sbs-gui-')
        job_path = Path(self._job_dir.name)/'job.json'
        job_path.write_text(json.dumps(job))
        self.log.clear()
        self.open_folder.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText('Starting conversion…')
        self._set_active(True)
        self.process.setProgram(sys.executable)
        self.process.setArguments(['-u', '-m', 'arc_sbs.gui_worker', str(job_path)])
        self.process.start()

    def _read_stdout(self):
        self._stdout_buffer += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split('\n', 1)
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('Not an event')
            except ValueError:
                self.log.appendPlainText(line)
                continue
            self._event(event)

    def _read_stderr(self):
        text = bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace').strip()
        if text:
            self.log.appendPlainText(text)

    def _event(self, event):
        kind = event.get('event')
        if kind == 'progress':
            phase = event.get('phase')
            if getattr(self, '_cancel_sent', False) and phase != 'preparing':
                return
            if phase == 'preparing':
                self._scratch = event.get('temporary_directory')
            elif phase == 'initializing':
                self.status.setText('Loading the model and preparing acceleration…')
            elif phase == 'converting':
                count, total = event.get('frames', 0), event.get('total', 0)
                if total:
                    self.progress.setRange(0, 100)
                    self.progress.setValue(min(99, int(count/total*100)))
                rate = event.get('fps', 0)
                self.status.setText(f'Converting: {count:,}'+(f' / ~{total:,} frames' if total else ' frames')+f' · {rate:.1f} fps')
            elif phase in ('encoding', 'finalizing'):
                self.status.setText('Finalizing video and audio…')
        elif kind == 'completed':
            self.result = event['result']
            self._terminal = 'completed'
        elif kind == 'cancelled':
            self._terminal = 'cancelled'
        elif kind == 'failed':
            self._terminal = 'failed'
            self._error_message = event.get('message', 'Conversion failed.')
            self.log.appendPlainText(self._error_message)

    def cancel(self):
        if not self.active or self.process.state() == QProcess.ProcessState.NotRunning or getattr(self, '_cancel_sent', False):
            return
        self.cancel_button.setEnabled(False)
        self.status.setText('Cancelling and cleaning up…')
        self._cancel_sent = True
        # The worker handles SIGTERM cooperatively and cleans up FFmpeg/files.
        self.process.terminate()
        self.kill_timer.start(10000)

    def _force_stop(self):
        pid = self.process.processId()
        if pid:
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGKILL)
                else:
                    self.process.kill()
            except ProcessLookupError:
                pass

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._error_message = self.process.errorString()
            self._terminal = 'failed'
            self._finished(1, QProcess.ExitStatus.CrashExit)

    def _finished(self, code, _status):
        if not self.active:
            return
        self.kill_timer.stop()
        self._read_stdout()
        self._read_stderr()
        terminal = self._terminal or ('cancelled' if getattr(self, '_cancel_sent', False) else 'failed')
        if terminal == 'completed' and code == 0 and self.result:
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
            self.status.setText(f'Complete · {self.result["frames"]:,} frames · {self.result["pipeline_fps"]:.1f} fps')
            self.open_folder.setEnabled(True)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            if terminal == 'cancelled':
                self.status.setText('Conversion cancelled.')
            else:
                message = getattr(self, '_error_message', '') or f'Conversion worker exited with code {code}.'
                summary = message.splitlines()[0][:220]
                self.status.setText(summary+' See details.')
                self.log.appendPlainText(message)
                self.log_toggle.setChecked(True)
        # Only clean the exact scratch directory announced by this job after exit.
        if self._scratch:
            scratch = Path(self._scratch)
            if scratch.name.startswith('.arc-sbs-') and scratch.parent == Path(self._job['output']).parent:
                shutil.rmtree(scratch, ignore_errors=True)
        if self._job_dir:
            self._job_dir.cleanup()
            self._job_dir = None
        self._cancel_sent = False
        self._set_active(False)
        self.job_finished.emit(terminal)
        if self._close_pending:
            self.close()

    def _open_folder(self):
        if self.result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.result['output']).parent)))

    def closeEvent(self, event):
        if self.active:
            self._close_pending = True
            self.cancel()
            event.ignore()
        else:
            event.accept()

    def _drop_input(self, event):
        mime = event.mimeData()
        urls = mime.urls() if mime.hasUrls() else []
        valid = (not self.active and len(urls) == 1 and urls[0].isLocalFile()
                 and Path(urls[0].toLocalFile()).is_file()
                 and bool(event.possibleActions() & Qt.DropAction.CopyAction))
        if not valid:
            event.ignore()
            return
        # Import a path; never acknowledge a move that could remove the source.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if event.type() == QEvent.Type.Drop:
            source = Path(urls[0].toLocalFile()).absolute()
            self.input_path.setText(str(source))
            self.settings.setValue('input_dir', str(source.parent))
            self._suggest_output()

    def eventFilter(self, target, event):
        if target is self.input_path and event.type() in (
                QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            self._drop_input(event)
            return True
        return super().eventFilter(target, event)

    def dragEnterEvent(self, event):
        self._drop_input(event)

    def dragMoveEvent(self, event):
        self._drop_input(event)

    def dropEvent(self, event):
        self._drop_input(event)


def main():
    if sys.argv[1:] == ['--version']:
        from . import __version__
        print(f'Arc SBS {__version__}')
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName('Arc SBS')
    app.setOrganizationName('ArcSBS')
    window = ConversionWindow()
    if len(sys.argv) == 2:
        window.input_path.setText(sys.argv[1])
        window._suggest_output()
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
