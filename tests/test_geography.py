"""Independent geographic expectations: export must equal threshold cells ∩ user polygon."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
import fiona
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import unary_union, transform
from terrain_export.engine import Options, extract


class GeographicContractTests(unittest.TestCase):
    def write_raster(self, path, values, grid, crs, unit='m'):
        with rasterio.open(path, 'w', driver='GTiff', width=values.shape[1], height=values.shape[0],
                           count=1, dtype='float32', crs=crs, transform=grid, nodata=-9999) as dst:
            dst.write(values.astype('float32'), 1)
            if unit: dst.set_band_unit(1, unit)
    def write_area(self, path, polygon, crs):
        with fiona.open(path, 'w', driver='ESRI Shapefile', crs=f'EPSG:{crs}',
                        schema={'geometry':'Polygon','properties':{}}) as dst:
            dst.write({'geometry':mapping(polygon),'properties':{}})
    def test_worldwide_exact_threshold_and_boundary(self):
        # Northern/southern and eastern/western hemispheres, at real longitude/latitude locations.
        for lon, lat in [(51.,35.),(-74.,40.7),(139.,35.6),(36.,-1.3),(-43.,-22.9)]:
            with self.subTest(location=(lon,lat)), tempfile.TemporaryDirectory() as folder:
                p=Path(folder); grid=from_origin(lon,lat,.001,.001)
                heights=np.array([[10,40,0,60],[35,30,45,0],[20,50,30,20],[40,20,20,40]],dtype='float32')
                self.write_raster(p/'dem.tif',np.full((4,4),100),grid,4326)
                self.write_raster(p/'dsm.tif',100+heights,grid,4326)
                area=Polygon([(lon+.0002,lat-.0002),(lon+.0037,lat-.0003),(lon+.0034,lat-.0038),(lon+.001,lat-.0035)],
                             holes=[[(lon+.0012,lat-.0012),(lon+.0022,lat-.0012),(lon+.0022,lat-.0022),(lon+.0012,lat-.0022)]])
                self.write_area(p/'area.shp',area,4326)
                o=Options(str(p/'area.shp'),str(p/'dem.tif'),str(p/'out.shp'),mode='above',minimum=30,
                          dsm=str(p/'dsm.tif'),height_kind='relative',unit_scale=1.)
                extract(o)
                with fiona.open(p/'out.shp') as src:
                    self.assertEqual(src.crs.to_epsg(),4326)
                    rows=list(src)
                result=unary_union([shape(r['geometry']) for r in rows])
                expected=[]
                for row,col in zip(*np.where(heights>30)):
                    x,y=grid*(int(col),int(row));expected.append(box(x,y-.001,x+.001,y).intersection(area))
                expected=unary_union(expected)
                self.assertLess(result.symmetric_difference(expected).area,1e-12)
                self.assertLess(result.difference(area).area,1e-14)
                self.assertTrue(all(r['properties']['elev_min']>30 and r['properties']['h_ref']=='DSM-DEM' for r in rows))
                self.assertEqual(CRS.from_wkt((p/'out.prj').read_text()).to_epsg(),4326)
    def test_absolute_and_ground_relative_are_different(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder); grid=from_origin(500000,4000000,10,10)
            self.write_raster(p/'dem.tif',np.full((2,2),100),grid,32639)
            self.write_raster(p/'dsm.tif',np.array([[110,145],[130,160]]),grid,32639)
            self.write_area(p/'area.shp',box(500000,3999980,500020,4000000),32639)
            common=dict(vector=str(p/'area.shp'),raster=str(p/'dem.tif'),dsm=str(p/'dsm.tif'),mode='above',minimum=30)
            extract(Options(**common,output=str(p/'rel.shp'),height_kind='relative'))
            extract(Options(**common,output=str(p/'abs.shp'),height_kind='dem'))
            with fiona.open(p/'rel.shp') as src:self.assertEqual(sum(f['properties']['pix_count'] for f in src),2)
            with fiona.open(p/'abs.shp') as src:self.assertEqual(sum(f['properties']['pix_count'] for f in src),4)
    def test_dem_dsm_alignment_across_crs(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder); tx=Transformer.from_crs(4326,3857,always_xy=True)
            x,y=tx.transform(-74,40.7)
            self.write_raster(p/'dsm.tif',np.full((4,4),140),from_origin(x,y,10,10),3857)
            self.write_raster(p/'dem.tif',np.full((20,20),100),from_origin(-74.01,40.71,.001,.001),4326)
            area=transform(Transformer.from_crs(3857,4326,always_xy=True).transform,box(x+5,y-35,x+35,y-5))
            self.write_area(p/'area.shp',area,4326)
            extract(Options(str(p/'area.shp'),str(p/'dem.tif'),str(p/'out.shp'),mode='above',minimum=30,
                            dsm=str(p/'dsm.tif'),height_kind='relative'))
            with fiona.open(p/'out.shp') as src:
                self.assertEqual(src.crs.to_epsg(),4326)
                rows=list(src)
            self.assertAlmostEqual(rows[0]['properties']['elev_mean'],40,places=6)
            self.assertLess(unary_union([shape(r['geometry']) for r in rows]).symmetric_difference(area).area,1e-12)
    def test_conflicting_crs_override_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)
            self.write_area(p/'area.shp',box(500000,3999980,500020,4000000),32639)
            with self.assertRaisesRegex(ValueError,'conflicts'):
                extract(Options(str(p/'area.shp'),'unused.tif',str(p/'out.shp'),vector_crs='EPSG:4326'))

    def test_relative_uses_each_rasters_vertical_units(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder); grid=from_origin(500000,4000000,10,10)
            self.write_raster(p/'dem.tif',np.full((2,2),20),grid,32639,'m')
            self.write_raster(p/'dsm.tif',np.full((2,2),200),grid,32639,'ft')
            self.write_area(p/'area.shp',box(500000,3999980,500020,4000000),32639)
            extract(Options(str(p/'area.shp'),str(p/'dem.tif'),str(p/'out.shp'),mode='above',minimum=30,
                            dsm=str(p/'dsm.tif'),height_kind='relative'))
            with fiona.open(p/'out.shp') as src:
                props=next(iter(src))['properties']
                self.assertAlmostEqual(props['elev_mean'],40.96,places=5)
                self.assertEqual(props['z_unit'],'m')
    def test_relative_excludes_nodata_from_either_source(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder); grid=from_origin(500000,4000000,10,10)
            self.write_raster(p/'dem.tif',np.array([[100,-9999],[100,100]]),grid,32639)
            self.write_raster(p/'dsm.tif',np.array([[140,140],[-9999,140]]),grid,32639)
            self.write_area(p/'area.shp',box(500000,3999980,500020,4000000),32639)
            extract(Options(str(p/'area.shp'),str(p/'dem.tif'),str(p/'out.shp'),mode='above',minimum=30,
                            dsm=str(p/'dsm.tif'),height_kind='relative'))
            with fiona.open(p/'out.shp') as src:
                self.assertEqual(sum(f['properties']['pix_count'] for f in src),2)
