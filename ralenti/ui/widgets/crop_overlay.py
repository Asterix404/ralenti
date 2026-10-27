"""Crop rectangle drawn over the video. Scene units = source pixels."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsObject

from .. import theme

CURSORS = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
           "bl": Qt.SizeBDiagCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
           "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "move": Qt.SizeAllCursor}
MIN = 32


def _points(r: QRectF) -> dict:
    cx, cy = r.center().x(), r.center().y()
    return {"tl": r.topLeft(), "t": QPointF(cx, r.top()), "tr": r.topRight(),
            "r": QPointF(r.right(), cy), "br": r.bottomRight(), "b": QPointF(cx, r.bottom()),
            "bl": r.bottomLeft(), "l": QPointF(r.left(), cy)}


class CropOverlay(QGraphicsObject):
    changed = Signal(object)   # (w, h, x, y) in source pixels, or None = full frame

    def __init__(self):
        super().__init__()
        self.bounds = QRectF(0, 0, 1920, 1080)
        self.rect = QRectF(self.bounds)
        self.ratio: float | None = None
        self._mode = None
        self._start = QRectF()
        self._anchor = QPointF()
        self._anchor_raw = QPointF()
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self.setVisible(False)

    # ---------- API ----------
    def set_frame(self, w: int, h: int) -> None:
        self.prepareGeometryChange()
        self.bounds = QRectF(0, 0, w, h)
        self.rect = QRectF(self.bounds)
        self.update()

    def set_crop(self, crop) -> None:
        self.rect = QRectF(crop[2], crop[3], crop[0], crop[1]) if crop else QRectF(self.bounds)
        self.update()

    def crop(self):
        r = self.rect
        if r.width() >= self.bounds.width() and r.height() >= self.bounds.height():
            return None
        return int(r.width()), int(r.height()), int(r.x()), int(r.y())

    def set_ratio(self, ratio: float | None) -> None:
        self.ratio = ratio
        if ratio and self.isVisible():
            self.rect = self._normalize(self.rect)
            self.update()
            self.changed.emit(self.crop())

    # ---------- geometry ----------
    def boundingRect(self) -> QRectF:
        return self.bounds.adjusted(-60, -60, 60, 60)

    def _scale(self) -> float:
        views = self.scene().views() if self.scene() else []
        return views[0].transform().m11() if views else 1.0

    def _clamp(self, p: QPointF) -> QPointF:
        return QPointF(min(max(p.x(), 0.0), self.bounds.width()),
                       min(max(p.y(), 0.0), self.bounds.height()))

    def _hit(self, p: QPointF) -> str:
        tol = 10 / self._scale()
        for name, pt in _points(self.rect).items():
            if abs(p.x() - pt.x()) <= tol and abs(p.y() - pt.y()) <= tol:
                return name
        return "move" if self.rect.contains(p) else "new"

    def _normalize(self, r: QRectF) -> QRectF:
        r = r.intersected(self.bounds)
        if r.width() <= 0 or r.height() <= 0:
            return QRectF(self.bounds)
        if self.ratio:
            w, h = r.width(), r.height()
            nw, nh = (h * self.ratio, h) if w / h > self.ratio else (w, w / self.ratio)
            c = r.center()
            r = QRectF(c.x() - nw / 2, c.y() - nh / 2, nw, nh)
        bw, bh = int(self.bounds.width()), int(self.bounds.height())
        x = max(0, round(r.x() / 2) * 2)
        y = max(0, round(r.y() / 2) * 2)
        w = max(MIN, int(r.width()) // 2 * 2)
        h = max(MIN, int(r.height()) // 2 * 2)
        w = min(w, bw - x) // 2 * 2
        h = min(h, bh - y) // 2 * 2
        return QRectF(x, y, w, h)

    def _resize(self, p: QPointF) -> QRectF:
        s, hd = self._start, self._mode
        l, t, r, b = s.left(), s.top(), s.right(), s.bottom()
        if "l" in hd: l = min(p.x(), r - MIN)
        if "r" in hd: r = max(p.x(), l + MIN)
        if "t" in hd: t = min(p.y(), b - MIN)
        if "b" in hd: b = max(p.y(), t + MIN)
        if self.ratio:
            if hd in ("t", "b"):
                w = (b - t) * self.ratio
                cx = (s.left() + s.right()) / 2
                l, r = cx - w / 2, cx + w / 2
            else:
                h = (r - l) / self.ratio
                if "t" in hd:
                    t = b - h
                elif "b" in hd:
                    b = t + h
                else:
                    cy = (s.top() + s.bottom()) / 2
                    t, b = cy - h / 2, cy + h / 2
        return QRectF(QPointF(l, t), QPointF(r, b)).intersected(self.bounds)

    def _new(self, p: QPointF) -> QRectF:
        a = self._anchor
        dx, dy = p.x() - a.x(), p.y() - a.y()
        w, h = abs(dx), abs(dy)
        if self.ratio and h > 0:
            if w / h > self.ratio:
                w = h * self.ratio
            else:
                h = w / self.ratio
        return QRectF(a.x() if dx >= 0 else a.x() - w, a.y() if dy >= 0 else a.y() - h, w, h)

    # ---------- painting ----------
    def paint(self, painter, option, widget=None):
        s = self._scale()
        outer = QPainterPath(); outer.addRect(self.bounds)
        inner = QPainterPath(); inner.addRect(self.rect)
        painter.fillPath(outer.subtracted(inner), QColor(0, 0, 0, 150))

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 60), 1 / s))
        r = self.rect
        for i in (1, 2):
            x = r.left() + r.width() * i / 3
            y = r.top() + r.height() * i / 3
            painter.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            painter.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        painter.setPen(QPen(QColor(theme.ACCENT), 2 / s))
        painter.drawRect(r)

        hs = 9 / s
        painter.setPen(QPen(QColor("#0b0f14"), 1 / s))
        painter.setBrush(QColor(theme.ACCENT))
        for pt in _points(r).values():
            painter.drawRect(QRectF(pt.x() - hs / 2, pt.y() - hs / 2, hs, hs))

        font = painter.font(); font.setPixelSize(max(1, int(13 / s))); painter.setFont(font)
        label = f"{int(r.width())} × {int(r.height())}"
        pos = QPointF(r.left() + 8 / s, r.top() + 20 / s)
        painter.setPen(QColor(0, 0, 0, 200)); painter.drawText(pos + QPointF(1 / s, 1 / s), label)
        painter.setPen(QColor(theme.TEXT)); painter.drawText(pos, label)

    # ---------- mouse ----------
    def hoverMoveEvent(self, e):
        self.setCursor(CURSORS.get(self._hit(e.pos()), Qt.CrossCursor))

    def hoverLeaveEvent(self, e):
        self.unsetCursor()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            e.ignore(); return
        self._mode = self._hit(e.pos())
        self._start = QRectF(self.rect)
        self._anchor_raw = e.pos()
        self._anchor = self._clamp(e.pos())
        e.accept()

    def mouseMoveEvent(self, e):
        if not self._mode:
            return
        p = self._clamp(e.pos())
        if self._mode == "move":
            r = self._start.translated(e.pos() - self._anchor_raw)
            r.moveLeft(max(0.0, min(r.left(), self.bounds.width() - r.width())))
            r.moveTop(max(0.0, min(r.top(), self.bounds.height() - r.height())))
        elif self._mode == "new":
            r = self._new(p)
        else:
            r = self._resize(p)
        self.rect = r
        self.update()

    def mouseReleaseEvent(self, e):
        if not self._mode:
            return
        if self._mode == "new" and (self.rect.width() < MIN or self.rect.height() < MIN):
            self.rect = self._start       # a click, not a drag
        else:
            self.rect = self._normalize(self.rect)
        self._mode = None
        self.update()
        self.changed.emit(self.crop())