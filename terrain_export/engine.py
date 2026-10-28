"""Standalone raster masking, connected polygon extraction and elevation statistics."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
import fiona
import numpy as np
import rasterio
from rasterio.features import geometry_mask, shapes
from rasterio.windows import from_bounds, transform as window_transform
from pyproj import CRS
from shapely.geometry import shape, mapping
from shapely.ops import unary_union


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
    dsm: str = ""
    height_kind: str = "absolute"
    dsm_band: int = 1
    unit_scale: float | None = None


class CancelledError(Exception):
    pass


from terrain_export.surface import read_aoi, load_surface, project


def area_of_interest(path, radius, crs_override=""):
    return read_aoi(path, radius, crs_override)


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
    if any(out.with_suffix(s).exists() for s in ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.json')):
        raise ValueError('Choose a new output name; existing datasets will not be overwritten.')
    progress('Reading area of interest…')
    values, affine, crs, original_aoi, vector_crs, aoi, metadata = load_surface(o, progress)
    unit = metadata['z_unit']
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
        import math
        from rasterio.windows import Window
        def polygonal(g):
            if g.geom_type == 'Polygon': return g if g.area > 0 else None
            parts = [polygonal(p) for p in getattr(g, 'geoms', [])]
            parts = [p for p in parts if p is not None and not p.is_empty]
            return unary_union(parts) if parts else None
        clipped = polygonal(poly.intersection(aoi))
        if clipped is None: return None
        raw = from_bounds(*clipped.bounds, transform=affine)
        r, c = max(0, math.floor(raw.row_off)), max(0, math.floor(raw.col_off))
        end_r = min(values.shape[0], math.ceil(raw.row_off + raw.height))
        end_c = min(values.shape[1], math.ceil(raw.col_off + raw.width))
        win = Window(c, r, end_c-c, end_r-r)
        subset = values[r:end_r, c:end_c]
        inside = geometry_mask([mapping(clipped)], subset.shape, window_transform(win, affine), invert=True, all_touched=True)
        z = subset[inside & selected[r:end_r, c:end_c] & np.isfinite(subset)]
        if not z.size: return None
        output_geometry = polygonal(project(clipped, crs, vector_crs).intersection(original_aoi))
        if output_geometry is None: return None
        return mapping(output_geometry), dict(elev_min=float(z.min()), elev_max=float(z.max()), elev_mean=float(z.mean()),
                                   pix_count=int(z.size), z_unit=unit, h_ref=metadata['reference'])
    schema = {'geometry': 'Polygon', 'properties': {'obj_id': 'int', 'elev_min': 'float:24.8',
              'elev_max': 'float:24.8', 'elev_mean': 'float:24.8', 'pix_count': 'int:18', 'z_unit': 'str:40', 'h_ref': 'str:16'}}
    # Stage the entire shapefile bundle, publishing only after successful processing.
    with tempfile.TemporaryDirectory(dir=out.parent) as staging:
        temp = Path(staging) / out.name
        with fiona.open(temp, 'w', driver='ESRI Shapefile', crs_wkt=vector_crs.to_wkt(version='WKT1_ESRI'), schema=schema, encoding='UTF-8') as dst:
            with ThreadPoolExecutor(max_workers=o.workers) as pool:
                # Bounded batches avoid submitting millions of objects to the executor at once.
                import itertools
                count = 0
                while batch := list(itertools.islice(polygons, max(1, o.workers * 2))):
                    check_cancel()
                    progress(f'Writing polygons: {count} …')
                    for item in pool.map(record, batch):
                        if item is None: continue
                        geometry, props = item
                        count += 1
                        dst.write({'geometry': geometry, 'properties': {'obj_id': count, **props}})
        if not count: raise ValueError('No valid raster cells match the area and elevation filter.')
        with fiona.open(temp) as verified:
            if not CRS(verified.crs).equals(vector_crs, ignore_axis_order=True):
                raise RuntimeError('Written Shapefile CRS differs from the input polygon CRS; output withheld.')
            output_bounds = list(verified.bounds)
            for feature in verified:
                geometry = shape(feature['geometry'])
                if not geometry.is_valid or geometry.difference(original_aoi).area > max(original_aoi.area*1e-10, 1e-18):
                    raise RuntimeError('Output escaped the requested polygon; output withheld.')
        import json
        metadata['output_bounds'] = output_bounds
        metadata.update({'threshold_mode': o.mode, 'minimum': o.minimum, 'maximum': o.maximum,
                         'feature_count': count, 'output_wkt': vector_crs.to_wkt(),
                         'boundary': 'Exact intersection with user AOI; 4-connected selected cells',
                         'dem_or_raster': o.raster, 'dsm': o.dsm})
        temp.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
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
