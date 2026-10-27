from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import theme


class Timeline(QWidget):
    """Scrubber with in/out range, keyframe ticks and preview-take bars."""
    seek = Signal(float)

    def __init__(self):
        super().__init__()
        self.duration = 0.0
        self.position = 0.0
        self.in_s = self.out_s = None
        self.keys: list[float] = []
        self.takes: list[tuple[float, float, bool]] = []
        self.setMinimumHeight(46)
        self.setCursor(Qt.PointingHandCursor)

    def set_duration(self, d):
        self.duration = max(0.0, float(d or 0)); self.update()

    def set_position(self, t):
        self.position = t; self.update()

    def set_range(self, a, b):
        self.in_s, self.out_s = a, b; self.update()

    def set_keyframes(self, ks):
        self.keys = ks or []; self.update()

    def set_takes(self, spans):
        """[(start, end, active)] in source seconds."""
        self.takes = spans or []; self.update()

    def _track(self) -> QRectF:
        return QRectF(8, 12, max(1, self.width() - 16), 10)

    def _x(self, t: float) -> float:
        r = self._track()
        return r.left() + r.width() * (t / self.duration if self.duration else 0)

    def _t(self, x: float) -> float:
        r = self._track()
        return max(0.0, min(1.0, (x - r.left()) / r.width())) * self.duration

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._track()
        p.setPen(Qt.NoPen); p.setBrush(QColor(theme.SURFACE2)); p.drawRoundedRect(r, 4, 4)
        if self.duration <= 0:
            return
        acc = QColor(theme.ACCENT)
        played = QColor(acc); played.setAlpha(55); p.setBrush(played)
        p.drawRoundedRect(QRectF(r.left(), r.top(), self._x(self.position) - r.left(), r.height()), 4, 4)

        if self.in_s is not None or self.out_s is not None:
            a = self._x(self.in_s or 0.0)
            b = self._x(self.out_s if self.out_s is not None else self.duration)
            rng = QColor(acc); rng.setAlpha(150); p.setBrush(rng)
            p.drawRect(QRectF(a, r.top(), b - a, r.height()))
            p.setPen(QPen(acc, 2))
            for t in (self.in_s, self.out_s):
                if t is not None:
                    x = self._x(t)
                    p.drawLine(QPointF(x, r.top() - 4), QPointF(x, r.bottom() + 4))

        if self.keys:
            p.setPen(QPen(QColor(theme.MUTED), 1))
            last, y0 = None, int(r.bottom()) + 4
            for k in self.keys:
                x = round(self._x(k))
                if x != last:
                    p.drawLine(x, y0, x, y0 + 5)
                    last = x

        p.setPen(Qt.NoPen)
        for a, b, active in self.takes:
            c = QColor(acc); c.setAlpha(255 if active else 120)
            p.setBrush(c)
            x0, x1 = self._x(a), max(self._x(b), self._x(a) + 4)
            p.drawRoundedRect(QRectF(x0, r.bottom() + 13, x1 - x0, 5), 2.5, 2.5)

        x = self._x(self.position)
        p.setPen(QPen(QColor(theme.TEXT), 2))
        p.drawLine(QPointF(x, r.top() - 6), QPointF(x, r.bottom() + 2))
        p.setPen(Qt.NoPen); p.setBrush(QColor(theme.TEXT))
        p.drawEllipse(QPointF(x, r.top() - 6), 4, 4)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.duration:
            self.seek.emit(self._t(e.position().x()))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and self.duration:
            self.seek.emit(self._t(e.position().x()))