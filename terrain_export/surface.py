"""Read georeferenced height surfaces; use the same spatial pipeline for preview and export."""
from contextlib import ExitStack
from pathlib import Path
import math
import fiona
import numpy as np
import rasterio
from rasterio.features import geometry_mask, geometry_window
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform
from rasterio.enums import Resampling
from affine import Affine
from pyproj import CRS, Transformer
from shapely.geometry import shape, mapping, box
from shapely.ops import transform, unary_union


def project(geometry, source, target):
    if CRS(source) == CRS(target):
        return geometry
    return transform(Transformer.from_crs(source, target, always_xy=True, allow_ballpark=False, only_best=True).transform, geometry)


def read_aoi(path, radius, crs_override=''):
    if not Path(path).is_file():
        raise ValueError('Vector file does not exist.')
    with fiona.open(path) as src:
        if not src.crs and not crs_override:
            raise ValueError('Input vector must have a defined CRS.')
        if src.crs and crs_override and CRS(src.crs) != CRS(crs_override):
            raise ValueError('CRS override conflicts with the defined vector CRS. Reproject the data instead.')
        crs = CRS(src.crs or crs_override)
        geoms = [shape(f['geometry']) for f in src if f['geometry']]
    if not geoms or any(g.is_empty or not g.is_valid for g in geoms):
        raise ValueError('Vector is empty or contains invalid geometry. Repair the source explicitly.')
    types = {g.geom_type for g in geoms}
    aoi = unary_union(geoms)
    if crs.is_geographic:
        x0, y0, x1, y1 = aoi.bounds
        if not (-180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90):
            raise ValueError('Geographic coordinates are outside longitude/latitude limits. Check the source CRS.')
    if types <= {'Point', 'MultiPoint'}:
        if not np.isfinite(radius) or radius <= 0:
            raise ValueError('Buffer radius must be positive (metres).')
        center = project(aoi, crs, 4326).centroid
        local = CRS.from_proj4(f'+proj=aeqd +lat_0={center.y} +lon_0={center.x} +datum=WGS84 +units=m')
        aoi = project(unary_union([project(g, crs, local).buffer(radius) for g in geoms]), local, crs)
    elif not types <= {'Polygon', 'MultiPolygon'}:
        raise ValueError('Use a point layer OR a polygon layer; mixed/line layers are unsupported.')
    return aoi, crs


def factor(dataset, band, fallback, strict):
    unit = (dataset.units[band-1] or '').lower().strip()
    if unit in {'m', 'meter', 'metre', 'meters', 'metres'}:
        return 1., 'm'
    if unit in {'ft', 'foot', 'feet', 'international foot'}:
        return .3048, 'm'
    if unit in {'us_survey_foot', 'us survey foot', 'us-ft'}:
        return 1200/3937, 'm'
    if fallback is not None:
        return fallback, 'm'
    if strict:
        raise ValueError('Vertical units are missing. Select metres or feet for unlabelled height data.')
    return 1., unit or 'unknown'


def load_surface(o, progress=lambda message: None, max_side=None):
    """Reference grid is DEM for DEM mode, DSM for DSM or DSM−DEM mode. No CRS guessing."""
    kind = o.height_kind
    if kind not in {'absolute', 'dem', 'dsm', 'relative'}:
        raise ValueError('Choose an elevation reference: DEM, DSM, or DSM minus DEM.')
    if kind in {'dsm', 'relative'} and not o.dsm:
        raise ValueError('DSM file is required for the selected elevation reference.')
    if o.unit_scale is not None and o.unit_scale not in {1., .3048}:
        raise ValueError('Unsupported vertical unit conversion.')
    original_aoi, vector_crs = read_aoi(o.vector, o.radius, o.vector_crs)
    path = o.dsm if kind in {'dsm', 'relative'} else o.raster
    band = o.dsm_band if kind in {'dsm', 'relative'} else o.band
    with rasterio.Env(GDAL_NUM_THREADS=str(o.workers)):
        with ExitStack() as stack:
            ref = stack.enter_context(rasterio.open(path))
            if not ref.crs:
                raise ValueError('Raster CRS is missing.')
            if not 1 <= band <= ref.count:
                raise ValueError('Selected raster band is unavailable.')
            src = ref
            if ref.transform.b or ref.transform.d or ref.transform.a <= 0 or ref.transform.e >= 0:
                grid, width, height = calculate_default_transform(ref.crs, ref.crs, ref.width, ref.height, *ref.bounds)
                src = stack.enter_context(WarpedVRT(ref, crs=ref.crs, transform=grid, width=width, height=height))
            aoi = project(original_aoi, vector_crs, src.crs)
            if not aoi.intersects(box(*src.bounds)) or aoi.intersection(box(*src.bounds)).area == 0:
                raise ValueError('Vector and raster do not overlap. Check their CRS.')
            window = geometry_window(src, [mapping(aoi)])
            height, width = int(window.height), int(window.width)
            if max_side:
                ratio = min(1., max_side / max(height, width))
                height, width = max(1, math.ceil(height*ratio)), max(1, math.ceil(width*ratio))
            elif height*width > 40_000_000:
                raise ValueError('AOI exceeds the 40 million cell memory limit. Use a smaller AOI or resample the raster.')
            affine = src.window_transform(window) * Affine.scale(window.width/width, window.height/height)
            values = src.read(band, window=window, out_shape=(height, width), masked=True, resampling=Resampling.nearest).astype('float64').filled(np.nan)
            scale, unit = factor(ref, band, o.unit_scale, kind != 'absolute')
            values = (values * ref.scales[band-1] + ref.offsets[band-1]) * scale
            dem_crs = None
            if kind == 'relative':
                dem = stack.enter_context(rasterio.open(o.raster))
                if not dem.crs:
                    raise ValueError('Raster CRS is missing.')
                if not 1 <= o.band <= dem.count:
                    raise ValueError('Selected raster band is unavailable.')
                if not project(aoi, src.crs, dem.crs).intersects(box(*dem.bounds)):
                    raise ValueError('DEM and requested area do not overlap. Check their CRS.')
                # Reproject the DEM to the exact cropped DSM grid, with nodata explicitly propagated.
                aligned = stack.enter_context(WarpedVRT(dem, crs=src.crs, transform=affine,
                    width=width, height=height, dtype='float64', nodata=np.nan, resampling=Resampling.bilinear))
                ground = aligned.read(o.band, masked=True).astype('float64').filled(np.nan)
                ground_factor, _ = factor(dem, o.band, o.unit_scale, True)
                ground = (ground * dem.scales[o.band-1] + dem.offsets[o.band-1]) * ground_factor
                values -= ground
                dem_crs = str(dem.crs)
            inside = geometry_mask([mapping(aoi)], (height, width), affine, invert=True, all_touched=True)
            values[~inside] = np.nan
            metadata = {'reference': {'absolute':'raster', 'dem':'DEM', 'dsm':'DSM', 'relative':'DSM-DEM'}[kind],
                        'raster_crs': str(src.crs), 'dem_crs': dem_crs, 'output_crs': vector_crs.to_string(),
                        'z_unit': unit, 'valid_pixels': int(np.isfinite(values).sum()),
                        'requested_bounds': list(original_aoi.bounds)}
            progress(f"Height: {metadata['reference']} | raster CRS: {src.crs} | output CRS: {vector_crs.to_string()} | {unit}")
            return values, affine, src.crs, original_aoi, vector_crs, aoi, metadata
