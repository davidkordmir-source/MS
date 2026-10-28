"""Standalone raster masking, connected polygon extraction and elevation statistics."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
from contextlib import ExitStack
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from shapely import make_valid
from shapely.geometry import box

import fiona
import numpy as np
import rasterio
from rasterio.features import geometry_mask, shapes
from rasterio.mask import mask
from rasterio.windows import from_bounds, transform as window_transform
from pyproj import CRS, Transformer
from shapely.geometry import shape, mapping
from shapely.ops import transform, unary_union


@dataclass
class Options:
    vector: str
    raster: str
    output: str
    mode: str = 'between'
    minimum: float = 30
    maximum: float = 100
    radius: float = 100
    gpu: bool = False
    workers: int = min(32, max(1, os.cpu_count() or 1))
    band: int = 1
    vector_crs: str = ""


class CancelledError(Exception):
    pass


def area_of_interest(path, radius, crs_override=""):
    if not Path(path).is_file():
        raise ValueError("Vector file does not exist.")
    with fiona.open(path) as src:
        if not src.crs and not crs_override:
            raise ValueError('Input vector must have a defined CRS.')
        crs = CRS.from_user_input(crs_override or src.crs)
        geoms = [shape(f['geometry']) for f in src if f['geometry']]
    geoms = [make_valid(g) if not g.is_valid else g for g in geoms if not g.is_empty]
    if not geoms:
        raise ValueError('Vector is empty or contains invalid geometry.')
    types = {g.geom_type for g in geoms}
    if types <= {'Point', 'MultiPoint'}:
        if not np.isfinite(radius) or radius <= 0:
            raise ValueError('Buffer radius must be positive (metres).')
        # Local azimuthal equidistant projection: buffer units stay metres even for lon/lat input.
        ll = Transformer.from_crs(crs, 4326, always_xy=True).transform
        center = transform(ll, unary_union(geoms)).centroid
        metric = CRS.from_proj4(f'+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m')
        forward = Transformer.from_crs(crs, metric, always_xy=True).transform
        backward = Transformer.from_crs(metric, crs, always_xy=True).transform
        aoi = transform(backward, unary_union([transform(forward, g).buffer(radius) for g in geoms]))
    elif types <= {'Polygon', 'MultiPolygon'}:
        aoi = unary_union(geoms)
    else:
        raise ValueError('Use a point layer OR a polygon layer; mixed/line layers are unsupported.')
    return aoi, crs


def extract(options, progress=lambda message: None, cancelled=lambda: False):
    def check_cancel():
        if cancelled():
            raise CancelledError("Processing cancelled; no output saved.")
    check_cancel()
    o = options
    if o.mode not in {'between', 'above', 'below'}:
        raise ValueError('Unknown elevation filter.')
    if not np.isfinite([o.minimum, o.maximum]).all():
        raise ValueError('Elevation limits must be finite.')
    if o.mode == 'between' and o.minimum > o.maximum:
        raise ValueError('Minimum elevation exceeds maximum.')
    if not 1 <= o.workers <= 256:
        raise ValueError('Worker count must be between 1 and 256.')
    out = Path(o.output)
    if out.suffix.lower() != '.shp':
        raise ValueError('Output must end in .shp.')
    out.parent.mkdir(parents=True, exist_ok=True)
    if any(out.with_suffix(s).exists() for s in ('.shp', '.shx', '.dbf', '.prj', '.cpg')):
        raise ValueError('Choose a new output name; existing datasets will not be overwritten.')
    progress('Reading area of interest…')
    aoi, vector_crs = area_of_interest(o.vector, o.radius, o.vector_crs)
    with rasterio.Env(GDAL_NUM_THREADS=str(o.workers)):
        with ExitStack() as stack:
            original = stack.enter_context(rasterio.open(o.raster))
            src = original
            if not src.crs:
                raise ValueError('Raster CRS is missing.')
            if not 1 <= o.band <= src.count:
                raise ValueError('Selected raster band is unavailable.')
            if src.transform.b != 0 or src.transform.d != 0 or src.transform.a <= 0 or src.transform.e >= 0:
                progress('Normalizing rotated raster grid…')
                grid, width, height = calculate_default_transform(src.crs, src.crs, src.width, src.height, *src.bounds)
                src = stack.enter_context(WarpedVRT(src, crs=src.crs, transform=grid, width=width, height=height))
            aoi = transform(Transformer.from_crs(vector_crs, src.crs, always_xy=True).transform, aoi)
            if not aoi.intersects(box(*src.bounds)):
                raise ValueError('Vector and raster do not overlap. Check their CRS.')
            aoi = aoi.intersection(box(*src.bounds))
            check_cancel()
            # Bound peak working memory before reading the cropped raster.
            win = from_bounds(*aoi.bounds, transform=src.transform)
            cells = min(src.width, max(1, win.width + 2)) * min(src.height, max(1, win.height + 2))
            if cells > 40_000_000:
                raise ValueError('AOI exceeds the 40 million cell memory limit. Use a smaller AOI or resample the raster.')
            progress('Masking raster…')
            data, affine = mask(src, [mapping(aoi)], crop=True, indexes=o.band, filled=False)
            values = data.astype('float64').filled(np.nan)
            values = values * original.scales[o.band - 1] + original.offsets[o.band - 1]
            crs = src.crs
            unit = (original.units[o.band - 1] or 'unknown')[:40]
    check_cancel()
    valid = np.isfinite(values)
    progress('Applying elevation filter…')
    backend = 'CPU'
    if o.gpu:
        try:
            import cupy as cp
            if cp.cuda.runtime.getDeviceCount() < 1:
                raise RuntimeError('No CUDA device')
            v = cp.asarray(values)
            selected = cp.isfinite(v)
            if o.mode in ('between', 'above'):
                selected &= v >= o.minimum if o.mode == 'between' else v > o.minimum
            if o.mode in ('between', 'below'):
                selected &= v <= o.maximum if o.mode == 'between' else v < o.maximum
            selected = cp.asnumpy(selected)
            del v
            backend = 'CUDA'
        except Exception as exc:
            progress(f'GPU unavailable ({type(exc).__name__}); using CPU.')
    if backend == 'CPU':
        selected = valid.copy()
        if o.mode in ('between', 'above'):
            selected &= values >= o.minimum if o.mode == 'between' else values > o.minimum
        if o.mode in ('between', 'below'):
            selected &= values <= o.maximum if o.mode == 'between' else values < o.maximum
    if not selected.any():
        raise ValueError('No valid raster cells match the area and elevation filter.')
    progress(f'Creating connected polygons ({backend}); calculating statistics…')
    # 4-connected cells form one object. Values do not identify buildings or mountains semantically.
    polygons = (shape(g) for g, _ in shapes(selected.astype('uint8'), mask=selected, transform=affine, connectivity=4))
    def record(poly):
        win = from_bounds(*poly.bounds, transform=affine).round_offsets().round_lengths()
        r, c = int(win.row_off), int(win.col_off)
        h, w = int(win.height), int(win.width)
        subset = values[r:r+h, c:c+w]
        inside = geometry_mask([mapping(poly)], subset.shape, window_transform(win, affine), invert=True)
        z = subset[inside & np.isfinite(subset)]
        return mapping(poly), dict(elev_min=float(z.min()), elev_max=float(z.max()), elev_mean=float(z.mean()),
                                   pix_count=int(z.size), z_unit=unit)
    schema = {'geometry': 'Polygon', 'properties': {'obj_id': 'int', 'elev_min': 'float:24.8',
              'elev_max': 'float:24.8', 'elev_mean': 'float:24.8', 'pix_count': 'int:18', 'z_unit': 'str:40'}}
    # Stage the entire shapefile bundle, publishing only after successful processing.
    with tempfile.TemporaryDirectory(dir=out.parent) as staging:
        temp = Path(staging) / out.name
        with fiona.open(temp, 'w', driver='ESRI Shapefile', crs_wkt=crs.to_wkt(), schema=schema, encoding='UTF-8') as dst:
            with ThreadPoolExecutor(max_workers=o.workers) as pool:
                # Bounded batches avoid submitting millions of objects to the executor at once.
                import itertools
                count = 0
                while batch := list(itertools.islice(polygons, max(1, o.workers * 2))):
                    check_cancel()
                    progress(f'Writing polygons: {count} …')
                    for geometry, props in pool.map(record, batch):
                        count += 1
                        dst.write({'geometry': geometry, 'properties': {'obj_id': count, **props}})
        check_cancel()
        published = []
        try:
            for file in Path(staging).iterdir():
                target = out.parent / file.name
                if target.exists():
                    raise ValueError('Output appeared during processing; choose a different name.')
                file.rename(target)
                published.append(target)
        except Exception:
            for file in published:
                file.unlink()
            raise
    progress(f'Completed: {count} polygons; filter backend: {backend}.')
    return count
