"""Functional check of bundled GIS libraries and GUI without network access."""
from pathlib import Path
import tempfile
import numpy as np
import fiona
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping
from terrain_export.engine import Options, extract


def run():
    with tempfile.TemporaryDirectory(prefix='terrain-check-') as folder:
        root = Path(folder)
        vector, raster, output = (root / name for name in ('area.shp', 'dem.tif', 'output.shp'))
        with fiona.open(vector, 'w', driver='ESRI Shapefile', crs='EPSG:32639',
                        schema={'geometry': 'Polygon', 'properties': {}}) as dst:
            dst.write({'geometry': mapping(box(500000, 3999980, 500020, 4000000)), 'properties': {}})
        with rasterio.open(raster, 'w', driver='GTiff', width=2, height=2, count=1,
                           dtype='float32', crs='EPSG:32639', nodata=-9999,
                           transform=from_origin(500000, 4000000, 10, 10)) as dst:
            dst.write(np.array([[40, 60], [0, -9999]], dtype='float32'), 1)
        count = extract(Options(str(vector), str(raster), str(output), workers=2))
        with fiona.open(output) as src:
            rows = list(src)
            props = dict(rows[0]['properties'])
            if not (count == 1 and len(rows) == 1 and props['elev_mean'] == 50
                    and props['pix_count'] == 2 and src.crs.to_epsg() == 32639):
                raise RuntimeError('Bundled GIS functional check failed')
        if not all(output.with_suffix(s).exists() for s in ('.shp', '.shx', '.dbf', '.prj', '.cpg')):
            raise RuntimeError('Incomplete Shapefile bundle')
    return {'status': 'passed', 'gis': 'mask/filter/polygon/statistics/shapefile', 'gui': 'checked by launcher',
            'network_required': False}
