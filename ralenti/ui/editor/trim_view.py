"""Segment bar for the Trim workspace: the full source, kept segments in the accent colour, removed ones
hatched. Click a segment to keep/remove it, drag a cut to move it, right-click a cut to delete it."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import theme
from .summary import dur


class SegmentBar(QWidget):
    toggled = Signal(int)
    cut_moved = Signal(int, float)
    cut_removed = Signal(int)

    def __init__(self):
        super().__init__()
        self.duration, self.segs, self.pos, self.view_fn = 0.0, [], 0.0, None
        self._drag, self._press, self._hover = None, None, -1
        self.setMinimumHeight(46)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_data(self, segs, duration) -> None:
        self.segs, self.duration = list(segs), float(duration or 0)
        self.update()

    def set_position(self, t: float) -> None:
        self.pos = t
        self.update()

    def cuts(self) -> list[float]:
        return [b for _a, b, _k in self.segs[:-1]]

    def _view(self):
        if self.view_fn:
            a, b = self.view_fn()
            if b > a:
                return a, b
        return 0.0, max(self.duration, 1e-6)

    def _rect(self) -> QRectF:
        return QRectF(8, 4, max(1, self.width() - 16), self.height() - 8)

    def _x(self, t: float) -> float:
        r, (a, b) = self._rect(), self._view()
        return r.left() + r.width() * (t - a) / (b - a)

    def _t(self, x: float) -> float:
        r, (a, b) = self._rect(), self._view()
        return a + max(0.0, min(1.0, (x - r.left()) / r.width())) * (b - a)

    def _cut_at(self, x: float) -> int:
        return next((k for k, c in enumerate(self.cuts()) if abs(self._x(c) - x) <= 6), -1)

    def _seg_at(self, x: float) -> int:
        t = self._t(x)
        return next((i for i, (a, b, _k) in enumerate(self.segs) if a <= t < b), len(self.segs) - 1)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = self._rect()
        p.setPen(Qt.NoPen); p.setBrush(QColor(theme.SURFACE2)); p.drawRoundedRect(r, 6, 6)
        if self.duration <= 0 or not self.segs:
            return
        p.save(); p.setClipRect(r)
        cuts = self.cuts()
        if self._drag:
            cuts[self._drag[0]] = self._drag[1]
        edges = [0.0, *cuts, self.duration]
        f = QFont(self.font()); f.setPointSizeF(8.5); f.setWeight(QFont.DemiBold); p.setFont(f)
        for i, (_a, _b, keep) in enumerate(self.segs):
            x0, x1 = self._x(edges[i]), self._x(edges[i + 1])
            sr = QRectF(x0 + 1, r.top() + 1, max(1.0, x1 - x0 - 2), r.height() - 2)
            if keep:
                c = QColor(theme.ACCENT); c.setAlpha(150 if i == self._hover else 105)
                p.setBrush(c)
            else:
                p.setBrush(QBrush(QColor(theme.FAINT), Qt.BDiagPattern))
            p.setPen(Qt.NoPen); p.drawRoundedRect(sr, 5, 5)
            if not keep and i == self._hover:
                p.setPen(QPen(QColor(theme.MUTED), 1)); p.setBrush(Qt.NoBrush); p.drawRoundedRect(sr, 5, 5)
            if sr.width() > 80:
                p.setPen(QColor(theme.TEXT if keep else theme.MUTED))
                p.drawText(sr, Qt.AlignCenter, f"{'Kept' if keep else 'Removed'} · {dur(edges[i + 1] - edges[i])}")
        for c in cuts:
            x = self._x(c)
            p.setPen(QPen(QColor(theme.TEXT), 2)); p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(Qt.NoPen); p.setBrush(QColor(theme.TEXT)); p.drawEllipse(QPointF(x, r.top() + 5), 4, 4)
        x = self._x(self.pos)
        p.setPen(QPen(QColor(theme.WARN), 1.5)); p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        p.restore()

    def mousePressEvent(self, e):
        x = e.position().x()
        k = self._cut_at(x)
        if e.button() == Qt.RightButton:
            if k >= 0:
                self.cut_removed.emit(k)
            return
        if e.button() != Qt.LeftButton:
            return
        if k >= 0:
            self._drag = (k, self.cuts()[k])
        else:
            self._press = x

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if self._drag:
            self._drag = (self._drag[0], self._t(x))
            self.update()
            return
        self.setCursor(Qt.SizeHorCursor if self._cut_at(x) >= 0 else Qt.PointingHandCursor)
        h = self._seg_at(x) if self.segs else -1
        if h != self._hover:
            self._hover = h
            self.update()

    def mouseReleaseEvent(self, e):
        x = e.position().x()
        if self._drag:
            k, t = self._drag
            self._drag = None
            self.cut_moved.emit(k, t)
            self.update()
            return
        if self._press is not None and abs(x - self._press) < 4 and self.segs:
            self.toggled.emit(self._seg_at(x))
        self._press = None

    def contextMenuEvent(self, e):
        e.accept()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()
        super().leaveEvent(e)
