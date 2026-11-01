"""Read just the visible raster window, with a bounded display resolution."""
import math
import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.enums import Resampling
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage


def height_image(values, limits=None):
    valid = np.isfinite(values)
    rgb = np.zeros((*values.shape, 3), dtype='uint8')
    rgb[:] = (12, 27, 43)
    if valid.any():
        low, high = limits if limits is not None else np.percentile(values[valid], [2, 98])
        norm = np.clip((np.nan_to_num(values, nan=low)-low)/max(high-low, .001), 0, 1)
        stops = np.array([0., .24, .51, .76, 1.])
        colours = np.array([[22, 54, 77], [27, 105, 123], [51, 160, 153], [148, 195, 151], [246, 231, 182]])
        for channel in range(3):
            rgb[..., channel][valid] = np.interp(norm[valid], stops, colours[:, channel]).astype('uint8')
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, w*3, QImage.Format_RGB888).copy()


def read_detail(path, band, source_size, overview_size, scene_rect, display_size, limits):
    source_width, source_height = source_size
    sx, sy = source_width/overview_size[0], source_height/overview_size[1]
    x, y, width, height = scene_rect
    col, row = max(0, math.floor(x*sx)), max(0, math.floor(y*sy))
    end_col = min(source_width, math.ceil((x+width)*sx))
    end_row = min(source_height, math.ceil((y+height)*sy))
    if end_col <= col or end_row <= row:
        raise ValueError('Empty visible raster window')
    window = Window(col, row, end_col-col, end_row-row)
    # Native pixels are used once zoomed in; zooming out never allocates the full raster.
    ratio = min(1., 1600/max(window.width, window.height),
                max(display_size)/max(window.width, window.height))
    out_width, out_height = max(1, math.ceil(window.width*ratio)), max(1, math.ceil(window.height*ratio))
    with rasterio.Env(GDAL_CACHEMAX=32*1024*1024), rasterio.open(path) as src:
        values = src.read(band, window=window, out_shape=(out_height, out_width),
                          masked=True, resampling=Resampling.nearest).astype('float64').filled(np.nan)
    image = height_image(values, limits)
    return image, (col/sx, row/sy, window.width/sx, window.height/sy), (int(window.width), int(window.height))


class DetailSignals(QObject):
    ready = Signal(object)


class DetailJob(QRunnable):
    def __init__(self, generation, request, arguments):
        super().__init__()
        self.generation, self.request, self.arguments = generation, request, arguments
        self.signals = DetailSignals()

    def run(self):
        try:
            image, rect, pixels = read_detail(*self.arguments)
            result = dict(generation=self.generation, request=self.request, image=image, rect=rect, pixels=pixels)
        except Exception as exc:
            result = dict(generation=self.generation, request=self.request, error=str(exc))
        self.signals.ready.emit(result)
