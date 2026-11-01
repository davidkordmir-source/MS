"""Offline raster canvas. Drawing coordinates always use the raster's affine grid."""
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView
import numpy as np
from shapely.geometry import Polygon, mapping


def height_image(values):
    valid = np.isfinite(values)
    rgb = np.zeros((*values.shape, 3), dtype='uint8')
    rgb[:] = (16, 44, 64)
    if valid.any():
        low, high = np.percentile(values[valid], [2, 98])
        norm = np.clip((np.nan_to_num(values, nan=low) - low) / max(high-low, .001), 0, 1)
        rgb[valid] = np.stack([35+norm*173, 61+norm*162, 73+norm*130], axis=-1)[valid].astype('uint8')
    rgb = np.ascontiguousarray(rgb)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, w*3, QImage.Format_RGB888).copy()


class MapCanvas(QGraphicsView):
    area_changed = Signal()
    notice = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setLayoutDirection(Qt.LeftToRight)
        self.setRenderHint(QPainter.Antialiasing)
        self.setBackgroundBrush(QColor('#102c40'))
        self.setMinimumSize(300, 160)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.affine = None
        self.crs = None
        self.points = []
        self.drawing = False
        self.polygon = None
        self.outline = None
        self.raster_item = None
        self.raster_key = None

    def reset_raster(self):
        self.scene().clear()
        self.raster_item = self.outline = None
        self.affine = self.crs = self.raster_key = None
        self.points = []
        self.polygon = None
        self.drawing = False
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.unsetCursor()
        self.area_changed.emit()

    def set_raster(self, values, affine, crs, key):
        changed = key != self.raster_key
        if changed:
            self.scene().clear()
            self.raster_item = None
            self.outline = None
            self.points = []
            self.polygon = None
            self.drawing = False
        self.affine, self.crs, self.raster_key = affine, crs, key
        pixmap = QPixmap.fromImage(height_image(values))
        if self.raster_item is None:
            self.raster_item = self.scene().addPixmap(pixmap)
            self.raster_item.setZValue(-1)
        else:
            self.raster_item.setPixmap(pixmap)
        self.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        if changed:
            self.fit_map()
            self.area_changed.emit()

    def fit_map(self):
        if self.raster_item:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def begin_area(self):
        if self.affine is None:
            self.notice.emit('ابتدا فایل DEM یا DSM را انتخاب کنید.')
            return
        self.clear_area()
        self.drawing = True
        self.setDragMode(QGraphicsView.NoDrag)
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.notice.emit('برای رأس‌ها کلیک کنید؛ با دکمه «پایان رسم» یا Enter محدوده را ببندید. Backspace: حذف آخرین رأس؛ Esc: لغو رسم.')

    def clear_area(self):
        self.points = []
        self.polygon = None
        self.drawing = False
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.unsetCursor()
        self.draw_outline()
        self.area_changed.emit()

    def finish_area(self):
        if not self.drawing:
            return False
        if len(self.points) < 3:
            self.notice.emit('برای محدوده دست‌کم سه رأس انتخاب کنید.')
            return False
        polygon = Polygon([self.affine * (p.x(), p.y()) for p in self.points])
        if not polygon.is_valid or polygon.area <= 0:
            self.notice.emit('محدوده معتبر نیست؛ ضلع‌ها نباید همدیگر را قطع کنند. با Backspace رأس آخر را اصلاح کنید.')
            return False
        self.polygon = polygon
        self.drawing = False
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.unsetCursor()
        self.draw_outline()
        self.area_changed.emit()
        self.notice.emit('محدوده ثبت شد؛ حد ارتفاع را تعیین و استخراج را شروع کنید.')
        return True

    def area_geometry(self):
        return mapping(self.polygon) if self.polygon is not None else None

    def draw_outline(self):
        if self.outline is not None:
            self.scene().removeItem(self.outline)
            self.outline = None
        if self.points:
            pen = QPen(QColor('#ffe59b'), 2)
            pen.setCosmetic(True)
            self.outline = self.scene().addPolygon(QPolygonF(self.points), pen, QColor(255, 229, 155, 35))

    def mousePressEvent(self, event):
        if self.drawing:
            if event.button() == Qt.LeftButton:
                point = self.mapToScene(event.position().toPoint())
                if self.sceneRect().contains(point):
                    if not self.points or (point-self.points[-1]).manhattanLength() > 1e-6:
                        self.points.append(QPointF(point))
                        self.draw_outline()
                event.accept()
                return
            if event.button() == Qt.RightButton:
                self.finish_area()
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if self.drawing:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.finish_area(); return
            if event.key() == Qt.Key_Backspace:
                if self.points: self.points.pop()
                self.draw_outline(); return
            if event.key() == Qt.Key_Escape:
                self.clear_area(); return
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else .8
        scale = self.transform().m11()
        if .005 < scale * factor < 1000:
            self.scale(factor, factor)
        event.accept()
