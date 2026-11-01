"""Standalone raster masking, connected polygon extraction and elevation statistics."""
from dataclasses import dataclass
import os


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
    aoi_geometry: dict | None = None
    aoi_crs: str = ""
    tile_size: int = 1024


class CancelledError(Exception):
    pass


from terrain_export.surface import read_aoi


def area_of_interest(path, radius, crs_override=""):
    return read_aoi(path, radius, crs_override)


def extract(options, progress=lambda message: None, cancelled=lambda: False):
    from terrain_export.processing import extract as process
    return process(options, progress, cancelled)
