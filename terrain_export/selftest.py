"""Functional check of bundled GIS libraries and GUI without network access."""
from pathlib import Path
import tempfile
import numpy as np
import fiona
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping
from terrain_export.engine import Options, extract
from terrain_export.surface import project
from shapely.geometry import shape


def run():
    with tempfile.TemporaryDirectory(prefix='terrain-check-') as folder:
        root = Path(folder)
        vector, raster, output = (root / name for name in ('area.shp', 'dem.tif', 'output.shp'))
        dsm = root / 'dsm.tif'
        area = project(box(500000, 3999980, 500020, 4000000), 32639, 4326)
        with fiona.open(vector, 'w', driver='ESRI Shapefile', crs='EPSG:4326',
                        schema={'geometry': 'Polygon', 'properties': {}}) as dst:
            dst.write({'geometry': mapping(area), 'properties': {}})
        with rasterio.open(raster, 'w', driver='GTiff', width=20, height=20, count=1,
                           dtype='float32', crs='EPSG:4326', nodata=-9999,
                           transform=from_origin(area.centroid.x-.01, area.centroid.y+.01, .001, .001)) as dst:
            dst.write(np.full((20, 20), 100, dtype='float32'), 1)
            dst.set_band_unit(1, 'm')
        with rasterio.open(dsm, 'w', driver='GTiff', width=2, height=2, count=1,
                           dtype='float32', crs='EPSG:32639', nodata=-9999,
                           transform=from_origin(500000, 4000000, 10, 10)) as dst:
            dst.write(np.array([[140, 160], [100, -9999]], dtype='float32'), 1)
            dst.set_band_unit(1, 'm')
        count = extract(Options(str(vector), str(raster), str(output), workers=2, dsm=str(dsm), height_kind='relative', mode='above', minimum=30))
        with fiona.open(output) as src:
            rows = list(src)
            props = dict(rows[0]['properties'])
            if not (count == 1 and len(rows) == 1 and props['elev_mean'] == 50
                    and props['pix_count'] == 2 and src.crs.to_epsg() == 4326 and props['h_ref'] == 'DSM-DEM'):
                raise RuntimeError('Bundled GIS functional check failed')
            if shape(rows[0]['geometry']).difference(area).area > 1e-15:
                raise RuntimeError('Output does not respect the requested AOI')
        if not all(output.with_suffix(s).exists() for s in ('.shp', '.shx', '.dbf', '.prj', '.cpg')):
            raise RuntimeError('Incomplete Shapefile bundle')
    return {'status': 'passed', 'gis': 'DSM-DEM >30; UTM raster to WGS84 polygon CRS; exact AOI clipping; stats', 'gui': 'checked by launcher',
            'network_required': False}
