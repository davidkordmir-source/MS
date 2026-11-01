import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import time
import tempfile
from unittest.mock import patch
from pathlib import Path
import numpy as np
import rasterio
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtCore import QPointF, Qt
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
            while window.progress.value() != 100 and time.monotonic() < deadline:
                QTest.qWait(20)
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

    def test_dsm_only_workflow_does_not_require_dem(self):
        fixture = fixtures.ExtractionTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        window = Window(); window.silent = True
        window.vector.setText(str(fixture.vector)); window.inspect_vector()
        window.raster.setText(str(fixture.raster))
        window.unit.setCurrentIndex(1); window.height_kind.setCurrentIndex(2)
        self.assertIsNotNone(window.sample)
        self.assertEqual(window.options().dsm, str(fixture.raster))
        with patch('terrain_export.gui.QMessageBox.warning') as warning:
            window.start()
            self.assertFalse(warning.called, 'DSM-only extraction should not ask for a DEM')
        deadline = time.monotonic() + 15
        while not window.run_button.isEnabled() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(window.run_button.isEnabled(), 'GUI worker did not finish')
        self.assertTrue(window.open_output.isEnabled(), window.log.toPlainText())
        self.assertTrue(window.result_path.exists())
        window.close()

    def test_removed_inputs_cannot_restore_previous_preview(self):
        window = Window(); window.silent = True
        for field in ('vector', 'height_kind'):
            with self.subTest(field=field):
                window.load_sample()
                self.assertIsNotNone(window.sample)
                self.assertFalse(window.preview.pixmap().isNull())
                if field == 'vector':
                    window.vector.clear(); window.vector.editingFinished.emit()
                else:
                    window.height_kind.setCurrentIndex(0)
                self.assertIsNone(window.sample)
                window.minimum.setValue(31)
                self.assertTrue(window.preview.pixmap().isNull())
                self.assertIsNone(window.preview_geometry)
                self.assertIsNone(window.preview_affine)
                self.assertIsNone(window.preview_crs)
        window.close()

    def test_unreadable_vector_clears_previous_preview(self):
        window = Window(); window.silent = True; window.load_sample()
        self.assertIsNotNone(window.sample)
        with tempfile.TemporaryDirectory() as folder:
            window.vector.setText(str(Path(folder) / 'missing-area.shp'))
            window.inspect_vector()
        self.assertIsNone(window.sample)
        window.maximum.setValue(101)
        self.assertTrue(window.preview.pixmap().isNull())
        window.close()

    def test_nodata_raster_cannot_keep_previous_preview(self):
        fixture = fixtures.ExtractionTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        window = Window(); window.silent = True
        window.unit.setCurrentIndex(1); window.height_kind.setCurrentIndex(1)
        window.vector.setText(str(fixture.vector)); window.inspect_vector()
        window.raster.setText(str(fixture.raster)); window.inspect_raster()
        self.assertFalse(window.preview.pixmap().isNull())
        with rasterio.open(fixture.raster, 'r+') as dst:
            dst.write(np.full((dst.height, dst.width), dst.nodata, dtype=dst.dtypes[0]), 1)
        window.inspect_raster()
        self.assertTrue(np.isnan(window.sample).all())
        self.assertTrue(window.preview.pixmap().isNull())
        self.assertIn('داده ارتفاعی معتبر', window.preview.text())
        window.close()

    def test_draw_area_and_export_one_shapefile_from_single_dsm(self):
        import fiona
        from shapely.geometry import shape
        fixture = fixtures.ExtractionTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        window = Window(); window.silent = True; window.show(); APP.processEvents()
        window.height_kind.setCurrentIndex(2); window.unit.setCurrentIndex(1)
        window.raster.setText(str(fixture.raster)); window.raster.editingFinished.emit()
        self.assertEqual(window.vector.text(), '')
        self.assertEqual(window.aoi_mode.currentIndex(), 0)
        window.canvas.fit_map()
        QTest.mouseClick(window.draw_button, Qt.LeftButton)
        for x, y in [(0.1, 0.1), (3.9, 0.1), (3.9, 3.9), (0.1, 3.9)]:
            QTest.mouseClick(window.canvas.viewport(), Qt.LeftButton,
                             pos=window.canvas.mapFromScene(QPointF(x, y)))
        QTest.mouseClick(window.finish_draw, Qt.LeftButton)
        self.assertIsNotNone(window.canvas.area_geometry())
        self.assertIsNotNone(window.sample)
        window.output.setText(str(fixture.root / 'drawn.shp'))
        window.start()
        deadline = time.monotonic() + 15
        while not window.run_button.isEnabled() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(window.run_button.isEnabled(), 'GUI worker did not finish')
        self.assertTrue(window.open_output.isEnabled(), window.log.toPlainText())
        self.assertEqual(list(fixture.root.glob('drawn*.shp')), [window.result_path])
        with fiona.open(window.result_path) as src:
            self.assertEqual(src.crs.to_epsg(), 32639)
            rows = list(src)
        self.assertEqual(len(rows), 3)
        self.assertEqual(sorted(row['properties']['elev_mean'] for row in rows), [55, 90, 100])
        for row in rows:
            self.assertEqual(row['properties']['h_ref'], 'DSM')
            self.assertLess(shape(row['geometry']).difference(window.canvas.polygon).area, 1e-8)
        window.close()

    def test_invalid_drawing_cannot_be_exported(self):
        window = Window(); window.silent = True; window.show(); APP.processEvents()
        window.raster.setText(str(Path('examples/demo_dsm.tif').resolve())); window.inspect_raster()
        window.canvas.begin_area()
        for x, y in [(10, 10), (100, 100), (10, 100), (100, 10)]:
            QTest.mouseClick(window.canvas.viewport(), Qt.LeftButton,
                             pos=window.canvas.mapFromScene(QPointF(x, y)))
        self.assertFalse(window.canvas.finish_area())
        self.assertIsNone(window.options().aoi_geometry)
        with patch('terrain_export.gui.QMessageBox.warning') as warning:
            window.start()
        warning.assert_called_once()
        self.assertIsNone(window.worker)
        window.close()

    def test_preview_cannot_cover_map_controls_or_status_on_small_screens(self):
        window = Window(); window.silent = True; window.show(); window.load_sample()
        map_card = window.canvas.parentWidget()
        while map_card.objectName() != 'card':
            map_card = map_card.parentWidget()
        status_card = window.status.parentWidget()
        for width, height in [(1220, 950), (980, 700)]:
            with self.subTest(size=(width, height)):
                window.resize(width, height); APP.processEvents(); window.render_preview(); APP.processEvents()
                self.assertLess(window.canvas.geometry().bottom(), window.map_tools.geometry().top())
                self.assertLess(map_card.geometry().bottom(), status_card.geometry().top())
        window.close()
