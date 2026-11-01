"""Offline raster canvas. Drawing coordinates always use the raster's affine grid."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal, Slot, QTimer, QThreadPool, QPropertyAnimation, QVariantAnimation, QEasingCurve, Property
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView
import numpy as np
from shapely.geometry import Polygon, mapping
from terrain_export.raster_view import height_image, DetailJob


def usable_frame(image):
    if image.isNull() or image.width() < 4 or image.height() < 4: return False
    colours = [image.pixelColor(int(image.width()*x), int(image.height()*y))
               for x in (.15, .5, .85) for y in (.15, .5, .85)]
    black = all(max(c.red(), c.green(), c.blue()) < 4 for c in colours)
    white = all(min(c.red(), c.green(), c.blue()) > 251 for c in colours)
    return not black and not white




class MapCanvas(QGraphicsView):
    area_changed = Signal()
    notice = Signal(str)
    zoom_changed = Signal(float)
    detail_ready = Signal(object)
    render_backend = Signal(str)

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
        self._generation = 0
        self.source_path = None
        self.source_size = None
        self.source_band = 1
        self.limits = None
        self.detail_item = None
        self._job = None
        self._last_detail = None
        self._zoom_target = 1.
        self.fit_scale = 1.
        self._user_zoom = False
        self.rendering_ready = False
        self._zoom_anchor = QPointF()
        self._anchor_view = QPointF()
        self.zoom_motion = QPropertyAnimation(self, b'zoomFactor', self)
        self.zoom_motion.setDuration(170)
        self.zoom_motion.setEasingCurve(QEasingCurve.OutCubic)
        self.detail_motion = QVariantAnimation(self)
        self.detail_motion.setDuration(110)
        self.detail_motion.valueChanged.connect(self.fade_detail)
        self.detail_timer = QTimer(self); self.detail_timer.setSingleShot(True)
        self.detail_timer.setInterval(90); self.detail_timer.timeout.connect(self.request_detail)
        self.detail_pool = QThreadPool(self); self.detail_pool.setMaxThreadCount(1)
        self.horizontalScrollBar().valueChanged.connect(self.schedule_detail)
        self.verticalScrollBar().valueChanged.connect(self.schedule_detail)

    @Slot()
    def enable_hardware_rendering(self):
        from PySide6.QtWidgets import QApplication, QWidget
        if QApplication.platformName() in {'offscreen', 'minimal'}:
            self.rendering_ready = True
            self.render_backend.emit('رندر سازگار CPU')
            return
        try:
            from PySide6.QtOpenGLWidgets import QOpenGLWidget
            self.setViewport(QOpenGLWidget())
            self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
            def verify():
                viewport = self.viewport()
                working = isinstance(viewport, QOpenGLWidget) and viewport.isValid()
                if working:
                    working = usable_frame(viewport.grabFramebuffer())
                    viewport.doneCurrent()
                if working:
                    self.render_backend.emit('رندر GPU / OpenGL')
                else:
                    self.setViewport(QWidget())
                    self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
                    self.viewport().update(); self.scene().update()
                    self.render_backend.emit('رندر سازگار CPU')
                self.rendering_ready = True
            QTimer.singleShot(900, self, verify)
        except (ImportError, RuntimeError):
            self.rendering_ready = True
            self.render_backend.emit('رندر سازگار CPU')

    def reset_raster(self):
        self._generation += 1
        self.zoom_motion.stop(); self.detail_motion.stop(); self.detail_timer.stop()
        self.scene().clear()
        self.raster_item = self.outline = None
        self.detail_item = None
        self.source_path = self.source_size = self._last_detail = None
        self.affine = self.crs = self.raster_key = None
        self.points = []
        self.polygon = None
        self.drawing = False
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.unsetCursor()
        self.area_changed.emit()

    def set_raster(self, values, affine, crs, key, source_path=None, band=1, source_size=None):
        changed = key != self.raster_key
        if changed:
            self._generation += 1
            self.zoom_motion.stop(); self.detail_motion.stop()
            self.scene().clear()
            self.raster_item = None
            self.outline = None
            self.detail_item = None
            self._last_detail = None
            self.points = []
            self.polygon = None
            self.drawing = False
        self.affine, self.crs, self.raster_key = affine, crs, key
        self.source_path, self.source_band = source_path, band
        self.source_size = source_size or (values.shape[1], values.shape[0])
        valid = np.isfinite(values)
        self.limits = tuple(np.percentile(values[valid], [2, 98])) if valid.any() else (0., 1.)
        pixmap = QPixmap.fromImage(height_image(values, self.limits))
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
            self._user_zoom = False
            self.zoom_motion.stop()
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
            self._zoom_target = self.transform().m11()
            self.fit_scale = self._zoom_target
            self.zoom_changed.emit(self._zoom_target)
            self.schedule_detail()

    def get_zoom(self):
        return self.transform().m11()

    def set_zoom(self, value):
        self.setTransform(QTransform.fromScale(value, value))
        middle = QPointF(self.viewport().rect().center())
        self.centerOn(self._zoom_anchor+(middle-self._anchor_view)/value)
        self.zoom_changed.emit(value)
        self.schedule_detail()

    zoomFactor = Property(float, get_zoom, set_zoom)

    def zoom_by(self, factor, anchor=None, animated=True):
        if self.raster_item is None: return
        self._user_zoom = True
        self._anchor_view = QPointF(anchor if anchor is not None else self.viewport().rect().center())
        self._zoom_anchor = self.mapToScene(self._anchor_view.toPoint())
        current = self.transform().m11()
        self._zoom_target = max(.005, min(1000., self._zoom_target*factor))
        self.zoom_motion.stop()
        if animated:
            self.zoom_motion.setStartValue(current); self.zoom_motion.setEndValue(self._zoom_target); self.zoom_motion.start()
        else:
            self.set_zoom(self._zoom_target)

    def schedule_detail(self, *args):
        if self.source_path: self.detail_timer.start()

    def request_detail(self):
        if self._job is not None or not self.source_path or self.transform().m11() <= 1.:
            return
        rect = self.mapToScene(self.viewport().rect()).boundingRect()
        rect.adjust(-rect.width()*.12, -rect.height()*.12, rect.width()*.12, rect.height()*.12)
        rect = rect.intersected(self.sceneRect())
        if rect.isEmpty(): return
        region = tuple(round(n, 3) for n in (rect.x(), rect.y(), rect.width(), rect.height()))
        display = (max(1, int(self.viewport().width()*1.3)), max(1, int(self.viewport().height()*1.3)))
        request = (self._generation, region, display)
        if request == self._last_detail: return
        args = (self.source_path, self.source_band, self.source_size,
                (self.sceneRect().width(), self.sceneRect().height()), region, display, self.limits)
        self._job = DetailJob(self._generation, request, args)
        self._job.signals.ready.connect(self.accept_detail)
        self.detail_pool.start(self._job)

    @Slot(object)
    def accept_detail(self, result):
        self._job = None
        if result['generation'] != self._generation:
            self.schedule_detail(); return
        self._last_detail = result['request']
        if 'error' in result:
            self.notice.emit('خواندن جزئیات نقشه ممکن نشد؛ نمای کلی در دسترس است.')
            return
        self.detail_motion.stop()
        if self.detail_item is not None:
            self.scene().removeItem(self.detail_item)
        image = result['image']; x, y, width, height = result['rect']
        self.detail_item = self.scene().addPixmap(QPixmap.fromImage(image))
        self.detail_item.setZValue(1)
        self.detail_item.setPos(x, y)
        self.detail_item.setTransform(QTransform.fromScale(width/image.width(), height/image.height()))
        self.detail_item.setOpacity(0.)
        self.detail_motion.setStartValue(0.); self.detail_motion.setEndValue(1.); self.detail_motion.start()
        self.detail_ready.emit(result)
        self.schedule_detail()

    def fade_detail(self, value):
        if self.detail_item is not None: self.detail_item.setOpacity(value)

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
            self.outline.setZValue(20)

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
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(1.25**(delta/120.), event.position())
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, 'raster_item', None) is not None and not self._user_zoom:
            self.fit_map()
        self.schedule_detail()

    def showEvent(self, event):
        super().showEvent(event)
        if self.raster_item is not None and not self._user_zoom:
            self.fit_map()

    def closeEvent(self, event):
        self.detail_timer.stop(); self.detail_pool.clear()
        super().closeEvent(event)
