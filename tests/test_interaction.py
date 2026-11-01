import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QSpinBox, QScrollArea
from terrain_export.gui import Window
from terrain_export.map_canvas import usable_frame
from PySide6.QtGui import QImage, QColor

APP = QApplication.instance() or QApplication([])


def wait_until(predicate, seconds=7):
    deadline = time.monotonic()+seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    return bool(predicate())


class InteractionTests(unittest.TestCase):
    def tearDown(self):
        for window in APP.topLevelWidgets():
            window.close(); window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def test_wheel_never_changes_parameters_even_when_focused(self):
        window = Window(); window.silent = True; window.show(); APP.processEvents()
        controls = window.findChildren(QComboBox)+window.findChildren(QSpinBox)+window.findChildren(QDoubleSpinBox)
        self.assertGreater(len(controls), 5)
        for control in controls:
            with self.subTest(control=type(control).__name__):
                getter = control.currentIndex if isinstance(control, QComboBox) else control.value
                before = getter()
                control.setFocus()
                for direction in (120, -120):
                    point = control.rect().center()
                    event = QWheelEvent(QPointF(point), QPointF(control.mapToGlobal(point)),
                                        QPoint(), QPoint(0, direction), Qt.NoButton, Qt.NoModifier,
                                        Qt.ScrollUpdate, False)
                    APP.sendEvent(control, event)
                    self.assertEqual(getter(), before)
        scroll = window.controls.parentWidget()
        while not isinstance(scroll, QScrollArea): scroll = scroll.parentWidget()
        window.band.setFocus(); APP.processEvents()
        bar = scroll.verticalScrollBar(); bar.setValue(0)
        point = window.band.rect().center()
        event = QWheelEvent(QPointF(point), QPointF(window.band.mapToGlobal(point)),
                            QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
        APP.sendEvent(window.band, event); APP.processEvents()
        self.assertGreater(bar.value(), 0, 'Wheel over a parameter should scroll the containing form')
        window.minimum.setValue(30)
        QTest.keyClick(window.minimum, Qt.Key_Up)
        self.assertEqual(window.minimum.value(), 31)

    def test_condition_is_a_sentence_and_switching_direction_keeps_height(self):
        window = Window(); window.silent = True
        window.minimum.setValue(30)
        self.assertEqual(window.condition_summary.text(), 'ارتفاع عوارض از ۳۰ متر بالاتر باشد.')
        window.mode.setCurrentIndex(2)
        self.assertEqual(window.maximum.value(), 30)
        self.assertEqual(window.condition_summary.text(), 'ارتفاع عوارض از ۳۰ متر پایین‌تر باشد.')
        self.assertEqual(window.options().mode, 'below')
        window.maximum.setValue(45)
        window.mode.setCurrentIndex(1)
        self.assertEqual(window.minimum.value(), 45)
        self.assertIn('۴۵ متر بالاتر', window.condition_summary.text())

    def test_blank_graphics_frame_is_rejected_before_enabling_gpu_renderer(self):
        image = QImage(32, 32, QImage.Format_RGB32)
        image.fill(QColor('black')); self.assertFalse(usable_frame(image))
        image.fill(QColor('white')); self.assertFalse(usable_frame(image))
        image.fill(QColor('#102c40')); self.assertTrue(usable_frame(image))

    def test_live_zoom_reads_native_details_in_both_views_without_moving_aoi(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'large-dem.tif'
            values = np.arange(2048*2048, dtype='float32').reshape(2048, 2048)
            with rasterio.open(path, 'w', driver='GTiff', width=2048, height=2048, count=1,
                               dtype='float32', crs='EPSG:32639', transform=from_origin(500000, 4000000, 1, 1)) as dst:
                dst.write(values, 1); dst.set_band_unit(1, 'm')
            window = Window(); window.silent = True; window.show(); APP.processEvents()
            window.raster.setText(str(path)); window.inspect_raster(); window.canvas.fit_map()
            window.canvas.begin_area()
            for x, y in [(100, 100), (900, 100), (900, 900), (100, 900)]:
                QTest.mouseClick(window.canvas.viewport(), Qt.LeftButton,
                                 pos=window.canvas.mapFromScene(QPointF(x, y)))
            self.assertTrue(window.canvas.finish_area())
            original = window.canvas.polygon.wkb
            received = []
            window.canvas.detail_ready.connect(received.append)
            before = window.canvas.transform().m11()
            window.canvas.zoom_by(20)
            self.assertTrue(wait_until(lambda: bool(received)), 'Live raster detail job did not complete')
            self.assertGreater(window.canvas.transform().m11(), before)
            self.assertGreater(received[-1]['image'].width(), received[-1]['rect'][2])
            self.assertEqual(original, window.canvas.polygon.wkb)
            window.canvas.zoom_by(.5)
            self.assertTrue(wait_until(lambda: window.canvas.transform().m11() < before*20))
            window.map_tabs.setCurrentIndex(1); APP.processEvents(); window.render_preview()
            preview_results = []
            window.preview.detail_ready.connect(preview_results.append)
            window.preview.zoom_by(12)
            self.assertTrue(wait_until(lambda: bool(preview_results)), 'Live threshold preview job did not complete')
            self.assertGreater(preview_results[-1]['image'].width(), preview_results[-1]['rect'][2])
            self.assertEqual(original, window.canvas.polygon.wkb)
            window.close()

    def test_source_change_discards_old_detail_result(self):
        window = Window(); window.silent = True; window.load_sample()
        old_generation = window.canvas._generation
        window.canvas.reset_raster()
        window.canvas.accept_detail(dict(generation=old_generation, request='stale', error='stale job'))
        self.assertIsNone(window.canvas.detail_item)
