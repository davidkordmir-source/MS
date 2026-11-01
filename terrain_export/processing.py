"""Bounded raster processing with global polygonization across tile boundaries."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import itertools
import json
import math
import tempfile
import fiona
import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS
from rasterio.features import geometry_mask, shapes
from rasterio.windows import Window, from_bounds
from shapely import box, intersection, area as geometry_area
from shapely.geometry import shape, mapping
from terrain_export.surface import load_surface, project


def polygon_parts(geometry):
    if geometry.geom_type == 'Polygon':
        if not geometry.is_empty and geometry.area > 0:
            yield geometry
    else:
        for part in getattr(geometry, 'geoms', []):
            yield from polygon_parts(part)


def windows(width, height, side, col=0, row=0):
    for r in range(row, row+height, side):
        for c in range(col, col+width, side):
            yield Window(c, r, min(side, col+width-c), min(side, row+height-r))


def threshold(values, options):
    selected = np.isfinite(values)
    if options.mode in ('between', 'above'):
        selected &= values >= options.minimum if options.mode == 'between' else values > options.minimum
    if options.mode in ('between', 'below'):
        selected &= values <= options.maximum if options.mode == 'between' else values < options.maximum
    return selected


def extract(o, progress=lambda message: None, cancelled=lambda: False):
    from terrain_export.engine import CancelledError

    def check_cancel():
        if cancelled():
            raise CancelledError('Processing cancelled; no output saved.')

    check_cancel()
    if o.mode not in {'between', 'above', 'below'}:
        raise ValueError('Unknown elevation filter.')
    if not np.isfinite([o.minimum, o.maximum]).all():
        raise ValueError('Elevation limits must be finite.')
    if o.mode == 'between' and o.minimum > o.maximum:
        raise ValueError('Minimum elevation exceeds maximum.')
    if not 1 <= o.workers <= 256:
        raise ValueError('Worker count must be between 1 and 256.')
    if not isinstance(o.tile_size, int) or not 1 <= o.tile_size <= 4096:
        raise ValueError('Tile size must be between 1 and 4096.')
    out = Path(o.output)
    if out.suffix.lower() != '.shp':
        raise ValueError('Output must end in .shp.')
    out.parent.mkdir(parents=True, exist_ok=True)
    suffixes = ('.shp', '.shx', '.dbf', '.prj', '.cpg', '.json')
    if any(out.with_suffix(s).exists() for s in suffixes):
        raise ValueError('Choose a new output name; existing datasets will not be overwritten.')

    progress('Reading area of interest…')
    _, _, crs, original_aoi, vector_crs, aoi, metadata = load_surface(o, max_side=1)
    col, row, width, height = map(int, metadata['processing_window'])
    affine = Affine(*metadata['processing_transform'])
    backend = 'CPU'
    cupy = None
    if o.gpu:
        try:
            import cupy
            if cupy.cuda.runtime.getDeviceCount() < 1:
                raise RuntimeError('No CUDA device')
        except Exception as exc:
            cupy = None
            progress(f'GPU unavailable ({type(exc).__name__}); using CPU.')

    # Only completed output sidecars are published. Raster caches stay in work/.
    with rasterio.Env(GDAL_CACHEMAX=64*1024*1024), tempfile.TemporaryDirectory(dir=out.parent) as staging:
        stage = Path(staging)
        work = stage / 'work'; work.mkdir()
        z_path, mask_path = work/'heights.tif', work/'selection.tif'
        profile = dict(driver='GTiff', width=width, height=height, count=1, crs=crs,
                       transform=affine, tiled=True, compress='deflate', BIGTIFF='IF_SAFER')
        valid_count = selected_count = 0
        total_tiles = math.ceil(width/o.tile_size)*math.ceil(height/o.tile_size)
        with rasterio.open(z_path, 'w', dtype='float64', nodata=np.nan, **profile) as z_dst, \
             rasterio.open(mask_path, 'w', dtype='uint8', nodata=0, **profile) as mask_dst:
            for i, win in enumerate(windows(width, height, o.tile_size, col, row), 1):
                check_cancel()
                values, *_ = load_surface(o, tile_window=win)
                selected = None
                if cupy is not None:
                    try:
                        v = cupy.asarray(values)
                        selected = cupy.isfinite(v)
                        if o.mode in ('between', 'above'):
                            selected &= v >= o.minimum if o.mode == 'between' else v > o.minimum
                        if o.mode in ('between', 'below'):
                            selected &= v <= o.maximum if o.mode == 'between' else v < o.maximum
                        selected = cupy.asnumpy(selected)
                        del v
                        backend = 'CUDA'
                    except Exception as exc:
                        cupy = None
                        progress(f'GPU unavailable ({type(exc).__name__}); using CPU.')
                if selected is None:
                    selected = threshold(values, o)
                local = Window(win.col_off-col, win.row_off-row, win.width, win.height)
                z_dst.write(values, 1, window=local)
                mask_dst.write(selected.astype('uint8'), 1, window=local)
                valid_count += int(np.isfinite(values).sum())
                selected_count += int(selected.sum())
                progress(f'Applying elevation filter: {i}/{total_tiles} tiles')
        if not selected_count:
            raise ValueError('No valid raster cells match the area and elevation filter.')

        def statistics(part, full_object):
            raw = from_bounds(*part.bounds, transform=affine)
            r, c = max(0, math.floor(raw.row_off)), max(0, math.floor(raw.col_off))
            end_r = min(height, math.ceil(raw.row_off+raw.height))
            end_c = min(width, math.ceil(raw.col_off+raw.width))
            n = 0; total = 0.; minimum = math.inf; maximum = -math.inf
            with rasterio.open(z_path) as z_src, rasterio.open(mask_path) as mask_src:
                for win in windows(end_c-c, end_r-r, o.tile_size, c, r):
                    check_cancel()
                    grid = z_src.window_transform(win)
                    values = z_src.read(1, window=win)
                    selected = mask_src.read(1, window=win).astype(bool)
                    # Centre membership of the original cell union excludes other objects
                    # touching at a corner. Clipped edge cells contribute only positive area.
                    member = geometry_mask([mapping(full_object)], values.shape, grid, invert=True)
                    inside = geometry_mask([mapping(part)], values.shape, grid, invert=True, all_touched=True)
                    inside &= selected & member & np.isfinite(values)
                    centres = geometry_mask([mapping(part)], values.shape, grid, invert=True)
                    rr, cc = np.where(inside & ~centres)
                    if len(rr):
                        xx = grid.c+cc*grid.a; yy = grid.f+rr*grid.e
                        cells = box(xx, yy+grid.e, xx+grid.a, yy)
                        inside[rr, cc] &= geometry_area(intersection(cells, part)) > 0
                    z = values[inside]
                    if z.size:
                        n += int(z.size); total += float(z.sum())
                        minimum = min(minimum, float(z.min())); maximum = max(maximum, float(z.max()))
            if not n: return None
            return dict(elev_min=minimum, elev_max=maximum, elev_mean=total/n, pix_count=n,
                        z_unit=metadata['z_unit'], h_ref=metadata['reference'])

        def records(poly):
            result = []
            for part in polygon_parts(poly.intersection(aoi)):
                for output_part in polygon_parts(project(part, crs, vector_crs).intersection(original_aoi)):
                    grid_part = project(output_part, vector_crs, crs).intersection(part)
                    if grid_part.is_empty or grid_part.area <= 0: continue
                    props = statistics(grid_part, poly)
                    if props is not None:
                        result.append((mapping(output_part), props))
            return result

        schema = {'geometry': 'Polygon', 'properties': {'obj_id': 'int', 'elev_min': 'float:24.8',
                  'elev_max': 'float:24.8', 'elev_mean': 'float:24.8', 'pix_count': 'int:18',
                  'z_unit': 'str:40', 'h_ref': 'str:16'}}
        temp = stage/out.name
        progress(f'Creating connected polygons ({backend}); calculating statistics…')
        count = 0
        with rasterio.open(mask_path) as mask_src, \
             fiona.open(temp, 'w', driver='ESRI Shapefile', crs_wkt=vector_crs.to_wkt(version='WKT1_ESRI'),
                        schema=schema, encoding='UTF-8', **{'2GB_LIMIT': 'YES'}) as dst, \
             ThreadPoolExecutor(max_workers=o.workers) as pool:
            # GDAL polygonizes the complete on-disk mask: tile seams never split objects.
            polygons = (shape(g) for g, _ in shapes(rasterio.band(mask_src, 1),
                        mask=rasterio.band(mask_src, 1), connectivity=4))
            while batch := list(itertools.islice(polygons, max(1, o.workers*2))):
                check_cancel()
                progress(f'Writing polygons: {count} …')
                for items in pool.map(records, batch):
                    for geometry, props in items:
                        count += 1
                        dst.write({'geometry': geometry, 'properties': {'obj_id': count, **props}})
        if not count:
            raise ValueError('No valid raster cells match the area and elevation filter.')
        with fiona.open(temp) as verified:
            if not CRS(verified.crs).equals(vector_crs, ignore_axis_order=True):
                raise RuntimeError('Written Shapefile CRS differs from the requested CRS; output withheld.')
            output_bounds = list(verified.bounds)
            for feature in verified:
                check_cancel()
                geometry = shape(feature['geometry'])
                if not geometry.is_valid or geometry.difference(original_aoi).area > max(original_aoi.area*1e-10, 1e-18):
                    raise RuntimeError('Output escaped the requested polygon; output withheld.')
        metadata.update(output_bounds=output_bounds, threshold_mode=o.mode, minimum=o.minimum, maximum=o.maximum,
                        feature_count=count, output_wkt=vector_crs.to_wkt(), valid_pixels=valid_count,
                        boundary='Exact intersection with AOI; separate 4-connected selected regions',
                        dem_or_raster=o.raster, dsm=o.dsm, processing='tiled; global polygonization',
                        tile_size=o.tile_size)
        temp.with_suffix('.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        check_cancel()
        published = []
        try:
            for file in stage.iterdir():
                if not file.is_file(): continue
                target = out.parent/file.name
                if target.exists():
                    raise ValueError('Output appeared during processing; choose a different name.')
                file.rename(target)
                published.append(target)
        except Exception:
            for file in published: file.unlink()
            raise
    progress(f'Completed: {count} polygons; filter backend: {backend}.')
    return count
