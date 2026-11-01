"""Zoomable threshold preview with the same height pipeline used for extraction."""
import math
import numpy as np
from PySide6.QtCore import QRunnable, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen, QPixmap
from rasterio.windows import Window
from terrain_export.acceleration import cpu_threshold
from terrain_export.map_canvas import MapCanvas
from terrain_export.raster_view import DetailSignals, height_image
from terrain_export.surface import load_surface


def surface_image(values, limits, options):
    image = height_image(values, limits)
    # QImage is an owned copy; editing this view does not modify height data.
    rgb = np.frombuffer(image.bits(), dtype='uint8').reshape(image.height(), image.bytesPerLine())
    pixels = rgb[:, :image.width()*3].reshape(image.height(), image.width(), 3)
    selected = cpu_threshold(values, options)
    pixels[selected] = (pixels[selected]*.32+np.array([34, 226, 182])*.68).astype('uint8')
    return image


class SurfaceJob(QRunnable):
    def __init__(self, generation, request, options, source_window, overview_size, region, display, limits):
        super().__init__()
        self.generation, self.request, self.options = generation, request, options
        self.source_window, self.overview_size = source_window, overview_size
        self.region, self.display, self.limits = region, display, limits
        self.signals = DetailSignals()

    def run(self):
        try:
            left, top, width, height = self.source_window
            sx, sy = width/self.overview_size[0], height/self.overview_size[1]
            x, y, w, h = self.region
            col, row = max(left, math.floor(left+x*sx)), max(top, math.floor(top+y*sy))
            end_col, end_row = min(left+width, math.ceil(left+(x+w)*sx)), min(top+height, math.ceil(top+(y+h)*sy))
            win = Window(col, row, end_col-col, end_row-row)
            values, *_ = load_surface(self.options, max_side=min(1600, max(self.display)), tile_window=win)
            image = surface_image(values, self.limits, self.options)
            rect = ((col-left)/sx, (row-top)/sy, win.width/sx, win.height/sy)
            result = dict(generation=self.generation, request=self.request, image=image, rect=rect,
                          pixels=(int(win.width), int(win.height)))
        except Exception as exc:
            result = dict(generation=self.generation, request=self.request, error=str(exc))
        self.signals.ready.emit(result)


class SurfacePreview(MapCanvas):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._message = ''
        self.options = None
        self.source_window = None
        self.boundary_item = None

    def clear(self):
        self.reset_raster()
        self.boundary_item = None

    def setText(self, message):
        self.clear()
        self._message = message
        item = self.scene().addText(message)
        item.setDefaultTextColor(QColor('#9ebfca'))
        item.setTextWidth(320)
        self.resetTransform()
        self.setSceneRect(0, 0, max(360, self.viewport().width()), max(180, self.viewport().height()))
        item.setPos((self.sceneRect().width()-320)/2, (self.sceneRect().height()-item.boundingRect().height())/2)

    def text(self):
        return self._message

    def pixmap(self):
        return self.raster_item.pixmap() if self.raster_item is not None else QPixmap()

    def set_surface(self, values, affine, crs, geometry, options, metadata):
        self.options, self.source_window = options, metadata['processing_window']
        key = (tuple(affine), values.shape, geometry.wkb, options.mode, options.minimum, options.maximum,
               options.height_kind, options.band, options.raster, options.unit_scale)
        keep_view = self.raster_item is not None and self.affine == affine and self.sceneRect().size().width() == values.shape[1]
        transform, centre = self.transform(), self.mapToScene(self.viewport().rect().center())
        was_zoomed = self._user_zoom
        if self.boundary_item is not None:
            self.scene().removeItem(self.boundary_item)
        self.boundary_item = None
        self.set_raster(values, affine, crs, key, source_path='filtered', source_size=tuple(self.source_window[2:]))
        self.raster_item.setPixmap(QPixmap.fromImage(surface_image(values, self.limits, options)))
        path = QPainterPath(); inverse = ~affine
        parts = [geometry] if geometry.geom_type == 'Polygon' else geometry.geoms
        for polygon in parts:
            if polygon.geom_type != 'Polygon': continue
            for ring in [polygon.exterior, *polygon.interiors]:
                for i, (x, y) in enumerate(ring.coords):
                    col, row = inverse*(x, y)
                    if i == 0: path.moveTo(col, row)
                    else: path.lineTo(col, row)
                path.closeSubpath()
        pen = QPen(QColor('#ffe59b'), 2); pen.setCosmetic(True)
        self.boundary_item = self.scene().addPath(path, pen); self.boundary_item.setZValue(20)
        if keep_view:
            self.setTransform(transform); self.centerOn(centre); self._zoom_target = transform.m11()
            self._user_zoom = was_zoomed
        self._message = ''
        self.schedule_detail()

    def request_detail(self):
        if self._job is not None or self.options is None or not self.source_path or self.transform().m11() <= 1.:
            return
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        rect.adjust(-rect.width()*.12, -rect.height()*.12, rect.width()*.12, rect.height()*.12)
        rect = rect.intersected(self.sceneRect())
        if rect.isEmpty(): return
        region = tuple(round(v, 3) for v in (rect.x(), rect.y(), rect.width(), rect.height()))
        display = (max(1, int(self.viewport().width()*1.3)), max(1, int(self.viewport().height()*1.3)))
        request = (self._generation, region, display)
        if request == self._last_detail: return
        self._job = SurfaceJob(self._generation, request, self.options, self.source_window,
                              (self.sceneRect().width(), self.sceneRect().height()), region, display, self.limits)
        self._job.signals.ready.connect(self.accept_detail)
        self.detail_pool.start(self._job)
