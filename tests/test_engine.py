import tempfile
import unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
import fiona
from shapely.geometry import box, mapping, Point
from terrain_export.engine import Options, extract, area_of_interest


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vector = self.root / 'area.shp'
        self.raster = self.root / 'dem.tif'
        with fiona.open(self.vector, 'w', driver='ESRI Shapefile', crs='EPSG:32639',
                        schema={'geometry': 'Polygon', 'properties': {}}) as dst:
            dst.write({'geometry': mapping(box(500000, 3999960, 500040, 4000000)), 'properties': {}})
        values = np.array([[0, 40, 50, -9999], [0, 60, 70, 0],
                           [0, 0, 0, 0], [90, 0, 100, 0]], dtype='float32')
        with rasterio.open(self.raster, 'w', driver='GTiff', width=4, height=4, count=1,
                           dtype='float32', crs='EPSG:32639', transform=from_origin(500000, 4000000, 10, 10),
                           nodata=-9999) as dst:
            dst.write(values, 1)
    def run_filter(self, mode='between', minimum=30, maximum=100, gpu=False):
        output = self.root / f'{mode}.shp'
        count = extract(Options(str(self.vector), str(self.raster), str(output), mode, minimum, maximum, gpu=gpu, workers=2))
        with fiona.open(output) as src:
            records = [dict(f['properties']) for f in src]
            self.assertEqual(src.crs.to_epsg(), 32639)
        return count, records
    def test_connected_objects_and_statistics(self):
        count, records = self.run_filter()
        self.assertEqual(count, 3)
        first = records[0]
        self.assertEqual(first['pix_count'], 4)
        self.assertEqual(first['elev_min'], 40)
        self.assertEqual(first['elev_max'], 70)
        self.assertEqual(first['elev_mean'], 55)
    def test_strict_above(self):
        _, rows = self.run_filter('above', 90)
        self.assertEqual([r['elev_min'] for r in rows], [100])
    def test_strict_below_excludes_nodata(self):
        _, rows = self.run_filter('below', maximum=40)
        self.assertEqual(sum(r['pix_count'] for r in rows), 9)
    def test_empty_selection(self):
        with self.assertRaisesRegex(ValueError, 'No valid'):
            self.run_filter('above', 1000)
    def test_gpu_fallback(self):
        count, _ = self.run_filter(gpu=True)
        self.assertEqual(count, 3)
    def test_geographic_point_buffer_metres(self):
        point_file = self.root / 'point.shp'
        with fiona.open(point_file, 'w', driver='ESRI Shapefile', crs='EPSG:4326',
                        schema={'geometry': 'Point', 'properties': {}}) as dst:
            dst.write({'geometry': mapping(Point(51, 35)), 'properties': {}})
        aoi, _ = area_of_interest(point_file, 100)
        self.assertTrue(aoi.contains(Point(51, 35)))
        self.assertLess(aoi.bounds[2] - aoi.bounds[0], 0.003)
    def test_scale_offset(self):
        with rasterio.open(self.raster, 'r+') as dst:
            dst.scales = (2.0,)
            dst.offsets = (10.0,)
        _, rows = self.run_filter('above', 190)
        self.assertEqual(rows[0]['elev_min'], 210)
    def test_mask_clips_to_area(self):
        with fiona.open(self.vector, 'w', driver='ESRI Shapefile', crs='EPSG:32639',
                        schema={'geometry': 'Polygon', 'properties': {}}) as dst:
            dst.write({'geometry': mapping(box(500010, 3999980, 500030, 4000000)), 'properties': {}})
        count, rows = self.run_filter()
        self.assertEqual(count, 1)
        self.assertEqual(rows[0]['pix_count'], 4)
    def test_reprojects_geographic_polygon(self):
        from pyproj import Transformer
        from shapely.ops import transform
        geographic = transform(Transformer.from_crs(32639, 4326, always_xy=True).transform,
                               box(499999, 3999959, 500041, 4000001))
        with fiona.open(self.vector, 'w', driver='ESRI Shapefile', crs='EPSG:4326',
                        schema={'geometry': 'Polygon', 'properties': {}}) as dst:
            dst.write({'geometry': mapping(geographic), 'properties': {}})
        count, rows = self.run_filter()
        self.assertEqual(count, 3)
        self.assertEqual(sum(r['pix_count'] for r in rows), 6)
    def test_multiband_selection(self):
        with rasterio.open(self.raster) as src:
            profile = src.profile
        profile['count'] = 2
        with rasterio.open(self.raster, 'w', **profile) as dst:
            dst.write(np.zeros((4, 4), dtype='float32'), 1)
            dst.write(np.full((4, 4), 50, dtype='float32'), 2)
        output = self.root / 'band2.shp'
        self.assertEqual(extract(Options(str(self.vector), str(self.raster), str(output), band=2)), 1)
        with fiona.open(output) as src:
            self.assertEqual(next(iter(src))['properties']['elev_mean'], 50)
    def test_missing_vector_crs_override(self):
        self.vector.with_suffix('.prj').unlink()
        output = self.root / 'override.shp'
        self.assertEqual(extract(Options(str(self.vector), str(self.raster), str(output), vector_crs='EPSG:32639')), 3)
    def test_persian_output_and_new_directory(self):
        output = self.root / 'نتیجه پروژه' / 'ارتفاع.shp'
        self.assertEqual(extract(Options(str(self.vector), str(self.raster), str(output))), 3)
        with fiona.open(output) as src:
            self.assertEqual(len(src), 3)
    def test_cancel_before_publication(self):
        from terrain_export.engine import CancelledError
        stop = [False]
        output = self.root / 'cancel.shp'
        def progress(message):
            if message.startswith('Writing polygons:'): stop[0] = True
        with self.assertRaises(CancelledError):
            extract(Options(str(self.vector), str(self.raster), str(output)), progress, lambda: stop[0])
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('tmp*')))
    def test_rotated_raster(self):
        from affine import Affine
        with rasterio.open(self.raster, 'r+') as dst:
            dst.transform = from_origin(500000, 4000000, 10, 10) * Affine.rotation(5)
            dst.write(np.full((4, 4), 50, dtype='float32'), 1)
        _, rows = self.run_filter()
        self.assertTrue(all(r['elev_mean'] == 50 for r in rows))
    def test_no_overlap(self):
        with fiona.open(self.vector, 'w', driver='ESRI Shapefile', crs='EPSG:32639',
                        schema={'geometry': 'Polygon', 'properties': {}}) as dst:
            dst.write({'geometry': mapping(box(0, 0, 10, 10)), 'properties': {}})
        with self.assertRaisesRegex(ValueError, 'do not overlap'):
            self.run_filter()
    def test_no_overwrite(self):
        self.run_filter()
        with self.assertRaisesRegex(ValueError, 'existing'):
            self.run_filter()

if __name__ == '__main__':
    unittest.main()
