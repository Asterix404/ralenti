"""Scrubber with in/out range, keyframe ticks, preview-take bars and a time ruler. Zoomable: the mouse wheel
zooms around the cursor; Shift+wheel (or a horizontal wheel) and middle-drag move the view. When zoomed,
an overview bar shows where you are and the view follows the playhead."""
import math
from bisect import bisect_left, bisect_right

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from .. import theme
from ..icons import icon

STEPS = (0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)
MAX_ZOOM = 400


def _label(t: float, step: float) -> str:
    if step < 1:
        return f"{int(t // 60)}:{t % 60:04.1f}"
    s = int(round(t))
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class Timeline(QWidget):
    seek = Signal(float)
    view_changed = Signal()

    def __init__(self):
        super().__init__()
        self.duration, self.position = 0.0, 0.0
        self.in_s = self.out_s = None
        self.keys: list[float] = []
        self.takes: list[tuple[float, float, bool]] = []
        self.v0, self.v1 = 0.0, None
        self._pan, self._dragging = None, False
        self.setMinimumHeight(58)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Scroll to zoom · right-drag or Shift+scroll to move")

    # ---------- view ----------
    def view(self) -> tuple[float, float]:
        if self.v1 is None or self.duration <= 0:
            return 0.0, max(self.duration, 1e-6)
        return self.v0, self.v1

    def zoom(self) -> float:
        a, b = self.view()
        return self.duration / (b - a) if self.duration > 0 else 1.0

    def zoomed(self) -> bool:
        return self.v1 is not None

    def _set_view(self, a: float, span: float) -> None:
        d = self.duration
        if d <= 0 or span >= d * 0.999:
            self.v0, self.v1 = 0.0, None
        else:
            span = max(span, d / MAX_ZOOM, 0.2)
            a = max(0.0, min(a, d - span))
            self.v0, self.v1 = a, a + span
        self.update()
        self.view_changed.emit()

    def zoom_by(self, f: float, anchor: float | None = None) -> None:
        if self.duration <= 0:
            return
        a, b = self.view()
        t = self.position if anchor is None else anchor
        if not a <= t <= b:
            t = (a + b) / 2
        span = (b - a) / f
        self._set_view(t - (t - a) / (b - a) * span, span)

    def zoom_in(self) -> None:
        self.zoom_by(2.0)

    def zoom_out(self) -> None:
        self.zoom_by(0.5)

    def fit(self) -> None:
        self._set_view(0.0, self.duration)

    def pan_by(self, dt: float) -> None:
        if self.zoomed():
            a, b = self.view()
            self._set_view(a + dt, b - a)

    # ---------- data ----------
    def set_duration(self, d, keep: bool = False) -> None:
        d = max(0.0, float(d or 0))
        changed = abs(d - self.duration) > 1e-3
        self.duration = d
        if changed and not keep:
            self.v0, self.v1 = 0.0, None
            self.view_changed.emit()
        elif self.zoomed():
            self._set_view(self.v0, self.v1 - self.v0)
            return
        self.update()

    def set_position(self, t) -> None:
        self.position = t
        if self.zoomed() and not self._dragging and self._pan is None:
            a, b = self.view()
            if t < a or t > b:
                self._set_view(t - (b - a) * 0.1, b - a)
                return
        self.update()

    def set_range(self, a, b) -> None:
        self.in_s, self.out_s = a, b
        self.update()

    def set_keyframes(self, ks) -> None:
        self.keys = sorted(ks or [])
        self.update()

    def set_takes(self, spans) -> None:
        self.takes = spans or []
        self.update()

    # ---------- geometry ----------
    def _track(self) -> QRectF:
        return QRectF(8, 14, max(1, self.width() - 16), 10)

    def _x(self, t: float) -> float:
        r, (a, b) = self._track(), self.view()
        return r.left() + r.width() * ((t - a) / (b - a))

    def _t(self, x: float) -> float:
        r, (a, b) = self._track(), self.view()
        return a + max(0.0, min(1.0, (x - r.left()) / r.width())) * (b - a)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self._track()
        p.setPen(Qt.NoPen); p.setBrush(QColor(theme.SURFACE2)); p.drawRoundedRect(r, 4, 4)
        if self.duration <= 0:
            return
        acc = QColor(theme.ACCENT)
        a, b = self.view()
        if self.zoomed():
            ov = QRectF(r.left(), 3, r.width(), 3)
            p.setBrush(QColor(theme.SURFACE3)); p.drawRoundedRect(ov, 1.5, 1.5)
            c = QColor(acc); c.setAlpha(170); p.setBrush(c)
            x0 = ov.left() + ov.width() * a / self.duration
            x1 = ov.left() + ov.width() * b / self.duration
            p.drawRoundedRect(QRectF(x0, 3, max(4.0, x1 - x0), 3), 1.5, 1.5)
        p.save()
        p.setClipRect(QRectF(r.left() - 1, 8, r.width() + 2, self.height()))
        played = QColor(acc); played.setAlpha(55); p.setBrush(played)
        xp = min(max(self._x(self.position), r.left()), r.right())
        p.drawRoundedRect(QRectF(r.left(), r.top(), xp - r.left(), r.height()), 4, 4)
        if self.in_s is not None or self.out_s is not None:
            xa = self._x(self.in_s or 0.0)
            xb = self._x(self.out_s if self.out_s is not None else self.duration)
            rng = QColor(acc); rng.setAlpha(150); p.setBrush(rng)
            p.drawRect(QRectF(xa, r.top(), xb - xa, r.height()))
            p.setPen(QPen(acc, 2))
            for t in (self.in_s, self.out_s):
                if t is not None:
                    x = self._x(t)
                    p.drawLine(QPointF(x, r.top() - 4), QPointF(x, r.bottom() + 4))
        if self.keys:
            p.setPen(QPen(QColor(theme.MUTED), 1))
            last, y0 = None, int(r.bottom()) + 4
            for k in self.keys[bisect_left(self.keys, a):bisect_right(self.keys, b)]:
                x = round(self._x(k))
                if x != last:
                    p.drawLine(x, y0, x, y0 + 5)
                    last = x
        p.setPen(Qt.NoPen)
        for ta, tb, active in self.takes:
            c = QColor(acc); c.setAlpha(255 if active else 120); p.setBrush(c)
            x0, x1 = self._x(ta), max(self._x(tb), self._x(ta) + 4)
            p.drawRoundedRect(QRectF(x0, r.bottom() + 13, x1 - x0, 5), 2.5, 2.5)
        # ruler
        f = QFont(self.font()); f.setPointSizeF(7.5); p.setFont(f)
        px = r.width() / (b - a)
        step = next((s for s in STEPS if s * px >= 70), STEPS[-1])
        y0 = r.bottom() + 22
        t = math.ceil(a / step) * step
        while t <= b + 1e-9:
            x = self._x(t)
            p.setPen(QPen(QColor(theme.FAINT), 1))
            p.drawLine(QPointF(x, y0 - 3), QPointF(x, y0))
            p.setPen(QColor(theme.FAINT))
            p.drawText(QPointF(x + 3, y0 + 9), _label(t, step))
            t += step
        x = self._x(self.position)
        p.setPen(QPen(QColor(theme.TEXT), 2))
        p.drawLine(QPointF(x, r.top() - 6), QPointF(x, r.bottom() + 2))
        p.setPen(Qt.NoPen); p.setBrush(QColor(theme.TEXT))
        p.drawEllipse(QPointF(x, r.top() - 6), 4, 4)
        p.restore()

    # ---------- input ----------
    def mousePressEvent(self, e):
        if e.button() in (Qt.MiddleButton, Qt.RightButton) and self.zoomed():
            self._pan = (e.position().x(), self.view()[0])
            self.setCursor(Qt.ClosedHandCursor)
        elif e.button() == Qt.LeftButton and self.duration:
            self._dragging = True
            self.seek.emit(self._t(e.position().x()))

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            x0, a0 = self._pan
            a, b = self.view()
            self._set_view(a0 + (x0 - e.position().x()) / self._track().width() * (b - a), b - a)
        elif self._dragging and e.buttons() & Qt.LeftButton:
            self.seek.emit(self._t(e.position().x()))

    def mouseReleaseEvent(self, e):
        self._dragging = False
        if self._pan is not None:
            self._pan = None
            self.setCursor(Qt.PointingHandCursor)

    def contextMenuEvent(self, e):
        e.accept()          # right button is for dragging, no menu

    def wheelEvent(self, e):
        if self.duration <= 0:
            return
        d = e.angleDelta()
        a, b = self.view()
        if abs(d.x()) > abs(d.y()) or e.modifiers() & Qt.ShiftModifier:
            v = d.x() or d.y()
            self.pan_by(-v / 120 * (b - a) * 0.15)
        elif d.y():
            self.zoom_by(1.25 ** (d.y() / 120), self._t(e.position().x()))
        e.accept()


def zoom_controls(tl: Timeline) -> QWidget:
    """− zoom + fit, for placing next to a timeline."""
    w = QWidget()
    h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)

    def tb(name, tip, fn):
        b = QToolButton(); b.setIcon(icon(name, "muted", active="text", checked=None)); b.setIconSize(QSize(16, 16))
        b.setToolTip(tip); b.setFocusPolicy(Qt.NoFocus); b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    out_b = tb("zoom-out", "Zoom out the timeline", tl.zoom_out)
    lbl = QLabel("1×"); lbl.setObjectName("Muted"); lbl.setFixedWidth(38); lbl.setAlignment(Qt.AlignCenter)
    lbl.setToolTip("Timeline zoom · scroll on the timeline to zoom")
    in_b = tb("zoom-in", "Zoom in the timeline (or scroll on it)", tl.zoom_in)
    fit_b = tb("zoom-fit", "Show the whole timeline", tl.fit)
    for x in (out_b, lbl, in_b, fit_b):
        h.addWidget(x)

    def upd():
        z = tl.zoom()
        lbl.setText(f"{z:.0f}×" if z >= 10 or abs(z - round(z)) < 0.05 else f"{z:.1f}×")
        out_b.setEnabled(tl.zoomed()); fit_b.setEnabled(tl.zoomed())
        in_b.setEnabled(tl.duration > 0)

    tl.view_changed.connect(upd)
    upd()
    return w
