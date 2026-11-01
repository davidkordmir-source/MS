"""Controls deliberately keep scrolling separate from changing analysis settings."""
from PySide6.QtCore import Property, QPropertyAnimation, QEasingCurve, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QComboBox, QDoubleSpinBox, QPushButton, QSpinBox


class NoWheel:
    def wheelEvent(self, event):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                target = parent.viewport()
                local = target.mapFromGlobal(event.globalPosition().toPoint())
                forwarded = QWheelEvent(QPointF(local), event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                                        event.buttons(), event.modifiers(), event.phase(), event.inverted())
                QApplication.sendEvent(target, forwarded)
                event.accept()
                return
            parent = parent.parentWidget()
        event.ignore()


class ParameterCombo(NoWheel, QComboBox):
    pass


class ParameterInteger(NoWheel, QSpinBox):
    pass


class ParameterNumber(NoWheel, QDoubleSpinBox):
    pass


class MotionButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hover = 0.
        self.motion = QPropertyAnimation(self, b'hoverAmount', self)
        self.motion.setDuration(150)
        self.motion.setEasingCurve(QEasingCurve.OutCubic)

    def get_hover(self):
        return self._hover

    def set_hover(self, value):
        self._hover = value
        self.update()

    hoverAmount = Property(float, get_hover, set_hover)

    def animate_hover(self, value):
        self.motion.stop()
        self.motion.setStartValue(self._hover)
        self.motion.setEndValue(value)
        self.motion.start()

    def enterEvent(self, event):
        self.animate_hover(1.)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animate_hover(0.)
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.isEnabled() and self._hover > .01:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor(20, 184, 166, int(150*self._hover)), 1.3))
            painter.setBrush(QColor(20, 184, 166, int(10*self._hover)))
            painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 9, 9)
