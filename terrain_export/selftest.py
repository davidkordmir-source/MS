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
from terrain_export.acceleration import binding_report, ThresholdAccelerator
from shapely.geometry import shape


def run():
    with tempfile.TemporaryDirectory(prefix='terrain-check-') as folder:
        root = Path(folder)
        output = root / 'output.shp'
        dsm = root / 'dsm.tif'
        area = project(box(500000, 3999980, 500020, 4000000), 32639, 4326)
        with rasterio.open(dsm, 'w', driver='GTiff', width=2, height=2, count=1,
                           dtype='float32', crs='EPSG:32639', nodata=-9999,
                           transform=from_origin(500000, 4000000, 10, 10)) as dst:
            dst.write(np.array([[140, 160], [100, -9999]], dtype='float32'), 1)
            dst.set_band_unit(1, 'm')
        count = extract(Options('', '', str(output), workers=2, dsm=str(dsm), height_kind='dsm', mode='above', minimum=130,
                                aoi_geometry=mapping(area), aoi_crs='EPSG:4326', tile_size=1))
        with fiona.open(output) as src:
            rows = list(src)
            props = dict(rows[0]['properties'])
            if not (count == 1 and len(rows) == 1 and props['elev_mean'] == 150
                    and props['pix_count'] == 2 and src.crs.to_epsg() == 4326 and props['h_ref'] == 'DSM'):
                raise RuntimeError('Bundled GIS functional check failed')
            if shape(rows[0]['geometry']).difference(area).area > 1e-15:
                raise RuntimeError('Output does not respect the requested AOI')
        if not all(output.with_suffix(s).exists() for s in ('.shp', '.shx', '.dbf', '.prj', '.cpg')):
            raise RuntimeError('Incomplete Shapefile bundle')
    gpu = binding_report()
    check = ThresholdAccelerator(Options('', '', '', gpu=True, acceleration='gpu', mode='above', minimum=30), lambda message: None)
    if not np.array_equal(check.apply(np.array([[29., 30., 31., np.nan]])), [[False, False, True, False]]):
        raise RuntimeError('Bundled acceleration / CPU fallback check failed')
    gpu['tested_backend'] = check.backend
    return {'status': 'passed', 'gis': 'single DSM >130; mean 150 from input; drawn AOI; UTM to WGS84; tile seams; exact clipping', 'gui': 'checked by launcher', 'gpu': gpu,
            'network_required': False}
