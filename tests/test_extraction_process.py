import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fiona

from terrain_export.engine import Options, CancelledError
from terrain_export.extraction_process import extract_isolated
import test_engine as fixtures


def blocked_child(options, connection):
    Path(options.output).write_text('incomplete dataset', encoding='utf-8')
    connection.send(('progress', f'blocked child: {os.getpid()}'))
    # Model a native polygonizer that neither releases the GIL nor checks cancellation.
    while True:
        pass


def crashed_child(options, connection):
    Path(options.output).write_text('incomplete dataset', encoding='utf-8')
    os._exit(17)


def incomplete_child(options, connection):
    Path(options.output).write_text('incomplete dataset', encoding='utf-8')
    connection.send(('success', 1))
    connection.close()


class ExtractionProcessTests(unittest.TestCase):
    def test_spawned_dsm_export_preserves_statistics_crs_and_sidecars(self):
        fixture = fixtures.ExtractionTests(); fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        output = fixture.root/'isolated.shp'
        count = extract_isolated(Options(str(fixture.vector), str(fixture.raster), str(output),
                                        dsm=str(fixture.raster), height_kind='dsm', unit_scale=1., workers=2))
        self.assertEqual(count, 3)
        with fiona.open(output) as src:
            self.assertEqual(src.crs.to_epsg(), 32639)
            records = list(src)
        self.assertEqual(sorted(row['properties']['elev_mean'] for row in records), [55, 90, 100])
        self.assertTrue(all(row['properties']['h_ref'] == 'DSM' for row in records))
        self.assertEqual(len(list(fixture.root.glob('isolated.*'))), 6)
        self.assertEqual(list(fixture.root.glob('terrain-export-*')), [])

    def test_cancel_stuck_native_operation_terminates_child_and_removes_partial_files(self):
        children_before = {child.pid for child in multiprocessing.active_children()}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'cancelled.shp'
            messages = []
            with patch('terrain_export.extraction_process._extract_child', blocked_child):
                with self.assertRaises(CancelledError):
                    extract_isolated(Options('', '', str(output)), messages.append, lambda: bool(messages))
            self.assertNotEqual(int(messages[0].split(': ')[1]), os.getpid())
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertEqual({child.pid for child in multiprocessing.active_children()}, children_before)

    def test_crash_or_incomplete_result_does_not_publish_a_partial_dataset(self):
        for child in (crashed_child, incomplete_child):
            with self.subTest(child=child.__name__), tempfile.TemporaryDirectory() as folder:
                with patch('terrain_export.extraction_process._extract_child', child):
                    with self.assertRaises((RuntimeError, FileNotFoundError)):
                        extract_isolated(Options('', '', str(Path(folder)/'failed.shp')))
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/'existing.shp'
            output.write_text('existing data', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'existing'):
                extract_isolated(Options('', '', str(output)))
            self.assertEqual(output.read_text(encoding='utf-8'), 'existing data')


if __name__ == '__main__':
    unittest.main()
