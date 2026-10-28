import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import time
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from terrain_export.gui import Window
import test_engine as fixtures

APP = QApplication.instance() or QApplication([])


class GuiIntegrationTests(unittest.TestCase):
    def tearDown(self):
        import gc
        for window in APP.topLevelWidgets():
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        gc.collect()

    def test_full_workflow_and_repeated_output(self):
        fixture = fixtures.ExtractionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        window = Window(); window.silent = True; window.height_kind.setCurrentIndex(1); window.unit.setCurrentIndex(1)
        window.show()
        window.vector.setText(str(fixture.vector)); window.inspect_vector()
        window.raster.setText(str(fixture.raster)); window.inspect_raster()
        self.assertIsNotNone(window.sample)
        self.assertFalse(window.radius.isEnabled())
        self.assertFalse(window.preview.pixmap().isNull())
        for attempt in range(2):
            window.start()
            deadline = time.monotonic() + 15
            while not window.run_button.isEnabled() and time.monotonic() < deadline:
                QTest.qWait(20)
            self.assertTrue(window.run_button.isEnabled(), 'GUI worker did not finish')
            self.assertTrue(window.open_output.isEnabled(), window.log.toPlainText())
            self.assertTrue(window.result_path.exists())
            self.assertEqual(window.progress.value(), 100)
        self.assertTrue(window.result_path.stem.endswith('_1'))
        window.close()
    def test_error_then_success_without_restart(self):
        fixture = fixtures.ExtractionTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        window = Window(); window.silent = True; window.height_kind.setCurrentIndex(1); window.unit.setCurrentIndex(1)
        window.vector.setText(str(fixture.vector)); window.inspect_vector()
        window.raster.setText(str(fixture.root / 'missing.tif'))
        window.start()
        deadline = time.monotonic() + 15
        while not window.run_button.isEnabled() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(window.run_button.isEnabled())
        self.assertFalse(window.open_output.isEnabled())
        window.raster.setText(str(fixture.raster)); window.inspect_raster()
        window.start()
        deadline = time.monotonic() + 15
        while not window.run_button.isEnabled() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(window.run_button.isEnabled())
        self.assertTrue(window.result_path.exists(), window.log.toPlainText())
        window.close()
    def test_filter_controls(self):
        window = Window(); window.silent = True; window.height_kind.setCurrentIndex(1); window.unit.setCurrentIndex(1)
        window.mode.setCurrentIndex(1)
        self.assertFalse(window.maximum.isEnabled())
        window.mode.setCurrentIndex(2)
        self.assertFalse(window.minimum.isEnabled())
        self.assertTrue(window.maximum.isEnabled())
        window.close()
