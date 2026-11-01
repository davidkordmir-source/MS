"""Lightweight Windows bootstrap: paint the splash before loading GIS modules."""
import importlib
import json
from pathlib import Path
import sys
import traceback
import time
from PySide6.QtCore import QObject, Slot, QThread, Signal, QTimer, Qt, QRectF, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QLinearGradient
from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
from terrain_export.branding import app_icon, asset_path, VERSION, WINDOWS_APP_ID


class Splash(QWidget):
    def __init__(self):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowIcon(app_icon())
        self.setFixedSize(640, 380)
        self.setFont(QFont('Segoe UI', 11))
        self.logo = QPixmap(str(asset_path('terrain-studio.png')))
        self.phase = 0
        self.message = 'در حال آماده‌سازی نقشه و ابزارهای ارتفاع…'
        self.timer = QTimer(self); self.timer.timeout.connect(self.advance); self.timer.start(25)

    def advance(self):
        self.phase = (self.phase+5) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor('#12263f')); gradient.setColorAt(1, QColor('#0c4852'))
        p.setPen(QPen(QColor('#2a5869'), 1)); p.setBrush(gradient)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 26, 26)
        p.drawPixmap(264, 38, 112, 112, self.logo)
        p.setPen(QColor('#f4fbff')); p.setFont(QFont('Segoe UI', 25, QFont.Bold))
        p.drawText(QRectF(20, 164, 600, 48), Qt.AlignCenter, 'Terrain Studio')
        p.setPen(QColor('#a6d6d8')); p.setFont(QFont('Segoe UI', 11))
        p.drawText(QRectF(20, 220, 600, 32), Qt.AlignCenter, self.message)
        p.setPen(QPen(QColor('#244e5d'), 4)); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(302, 275, 36, 36))
        p.setPen(QPen(QColor('#50d7be'), 4, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(QRectF(302, 275, 36, 36), self.phase*16, 110*16)
        p.setPen(QColor('#7da9b5')); p.setFont(QFont('Segoe UI', 9))
        p.drawText(QRectF(20, 334, 600, 24), Qt.AlignCenter, f'WINDOWS  •  {VERSION}  •  DEM / DSM')


class ImportWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def run(self):
        try: self.ready.emit(dict(module=importlib.import_module('terrain_export.gui'), background=not QThread.isMainThread()))
        except Exception: self.failed.emit(traceback.format_exc())


class Launcher(QObject):
    def __init__(self, app, test_report=None):
        super().__init__(app)
        self.app, self.test_report = app, Path(test_report) if test_report else None
        self.started = time.monotonic()
        self.window = None
        self.splash = Splash()
        self.worker = ImportWorker(self)
        self.worker.ready.connect(self.loaded)
        self.worker.failed.connect(self.failed)
        self.splash.show()
        app.processEvents()
        self.splash_was_visible = self.splash.isVisible()
        if self.test_report:
            self.test_report.parent.mkdir(parents=True, exist_ok=True)
            self.splash.grab().save(str(self.test_report.with_suffix('.png')))
        self.worker.start()

    @Slot(object)
    def loaded(self, payload):
        module = payload['module']
        self.background_import = payload['background']
        self.splash.message = 'در حال بازکردن محیط کار…'; self.splash.update()
        try:
            self.window = module.Window()
            self.window.showMaximized()
            if self.app.platformName() not in {'offscreen', 'minimal'}:
                self.window.setWindowOpacity(0.)
                self.fade = QPropertyAnimation(self.window, b'windowOpacity', self.window)
                self.fade.setDuration(240); self.fade.setEasingCurve(QEasingCurve.OutCubic)
                self.fade.setStartValue(0.); self.fade.setEndValue(1.); self.fade.start()
            self.splash.timer.stop(); self.splash.close()
            def handle_error(kind, value, tb):
                module.logger().error('Unhandled exception', exc_info=(kind, value, tb))
                QMessageBox.critical(self.window, 'خطای برنامه', module.friendly_error(value))
            sys.excepthook = handle_error
            if self.test_report: QTimer.singleShot(350, self.finish_test)
        except Exception: self.failed(traceback.format_exc())

    def finish_test(self):
        if not self.window.canvas.rendering_ready and time.monotonic()-self.started < 15:
            QTimer.singleShot(100, self.finish_test); return
        result = dict(status='passed', version=VERSION, splash_shown=self.splash_was_visible,
                      background_import=self.background_import, gui_on_main_thread=QThread.isMainThread(),
                      window_visible=self.window.isVisible(), icon_loaded=not self.window.windowIcon().isNull(),
                      startup_seconds=round(time.monotonic()-self.started, 3),
                      renderer_ready=self.window.canvas.rendering_ready,
                      viewport=self.window.canvas.viewport().metaObject().className())
        if not all(result[name] for name in ['splash_shown', 'background_import', 'gui_on_main_thread', 'window_visible', 'icon_loaded', 'renderer_ready']):
            result['status'] = 'failed'
        self.test_report.write_text(json.dumps(result, indent=2), encoding='utf-8')
        self.window.close(); self.app.exit(0 if result['status'] == 'passed' else 1)

    @Slot(str)
    def failed(self, message):
        self.splash.timer.stop(); self.splash.close()
        if self.test_report:
            self.test_report.write_text(json.dumps(dict(status='failed', error=message)), encoding='utf-8')
        else:
            QMessageBox.critical(None, 'شروع برنامه ممکن نشد', message)
        self.app.exit(1)


def main(test_report=None):
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    app = QApplication(sys.argv); app.setStyle('Fusion'); app.setFont(QFont('Segoe UI', 10))
    app.setWindowIcon(app_icon())
    launcher = Launcher(app, test_report)
    # Keep bootstrap widgets and the import worker alive for the whole event loop.
    app.launcher = launcher
    status = app.exec()
    launcher.worker.wait()
    return status
