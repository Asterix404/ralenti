"""Compare canvas. Frame A is a stacked take (original on top, AI below). Against 'original' it
shows A's halves; against 'take' it shows A's AI half next to B's. Views: before (left only),
split, side, after (right only). Labels are cut exactly at the split line; full settings on hover."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QToolTip, QWidget

from .. import theme


class CompareCanvas(QWidget):
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.before, self.after = "Original", "AI"
        self.details, self.details_left = "", ""
        self.a = self.b = None
        self.against = "original"
        self.split, self.mode = 0.5, "split"
        self.audio_side = None
        self._drag = False
        self._hits = []
        self.setMinimumSize(320, 180)
        self.setMouseTracking(True)
        self.setCursor(Qt.SplitHCursor)

    def set_frame(self, f) -> None:
        self._store(f, "a")

    def set_frame_b(self, f) -> None:
        self._store(f, "b")

    def _store(self, f, key: str) -> None:
        if f.isValid():
            img = f.toImage()
            if not img.isNull():
                setattr(self, key, img)
                self.update()

    def reset(self) -> None:
        self.a = self.b = None
        self.update()

    def set_audio(self, side) -> None:
        self.audio_side = side   # shown on the transport row, not on the video

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.setCursor(Qt.SplitHCursor if mode == "split" else Qt.PointingHandCursor)
        self.update()

    @staticmethod
    def _fit(w: float, h: float, area: QRectF) -> QRectF:
        s = min(area.width() / w, area.height() / h)
        return QRectF(area.center().x() - w * s / 2, area.center().y() - h * s / 2, w * s, h * s)

    def _sources(self):
        A = self.a
        W, H = A.width(), A.height() / 2
        if self.against == "take":
            B = self.b
            right = (B, QRectF(0, B.height() / 2, B.width(), B.height() / 2)) if B is not None else (None, None)
            return (A, QRectF(0, H, W, H)), right, W, H
        return (A, QRectF(0, 0, W, H)), (A, QRectF(0, H, W, H)), W, H

    def _target(self) -> QRectF:
        if self.a is None:
            return QRectF()
        return self._fit(self.a.width(), self.a.height() / 2, QRectF(self.rect()).adjusted(8, 8, -8, -8))

    def _draw(self, p: QPainter, target: QRectF, src) -> None:
        img, rect = src
        if img is None:
            p.fillRect(target, QColor("#000000"))
            p.setPen(QColor(theme.MUTED)); p.drawText(target, Qt.AlignCenter, "Loading…")
        else:
            p.drawImage(target, img, rect)

    def _pill(self, p, pos, title, accent, right=False, details="", clip=None):
        f = QFont(self.font()); f.setPointSizeF(9); f.setWeight(QFont.DemiBold)
        fm = QFontMetrics(f)
        w, h = fm.horizontalAdvance(title) + 26, fm.height() + 10
        rect = QRectF(pos.x() - w if right else pos.x(), pos.y(), w, h)
        if details:
            self._hits.append((rect, details))
        p.save()
        if clip is not None:
            p.setClipRect(clip)
        p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 185)); p.drawRoundedRect(rect, h / 2, h / 2)
        p.setFont(f); p.setPen(QColor(theme.ACCENT if accent else theme.TEXT))
        p.drawText(rect, Qt.AlignCenter, title)
        p.restore()

    def paintEvent(self, _):
        self._hits = []
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        if self.a is None:
            p.setPen(QColor(theme.MUTED)); p.drawText(self.rect(), Qt.AlignCenter, "Loading…")
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform); p.setRenderHint(QPainter.Antialiasing)
        left, right, W, H = self._sources()
        off = QPointF(10, 10)
        vs = self.against == "take"

        if self.mode == "side":
            area = QRectF(self.rect()).adjusted(8, 8, -8, -8)
            half = area.width() / 2 - 4
            l = self._fit(W, H, QRectF(area.left(), area.top(), half, area.height()))
            r = self._fit(W, H, QRectF(area.left() + half + 8, area.top(), half, area.height()))
            self._draw(p, l, left); self._draw(p, r, right)
            self._pill(p, l.topLeft() + off, self.before, vs, False, self.details_left)
            self._pill(p, QPointF(r.right() - 10, r.top() + 10), self.after, True, True, self.details)
            return

        t = self._target()
        if self.mode == "before":
            self._draw(p, t, left)
            self._pill(p, t.topLeft() + off, self.before, vs, False, self.details_left)
            return
        if self.mode == "after":
            self._draw(p, t, right)
            self._pill(p, QPointF(t.right() - 10, t.top() + 10), self.after, True, True, self.details)
            return

        x = t.left() + t.width() * self.split
        self._draw(p, t, left)
        p.save(); p.setClipRect(QRectF(x, t.top(), t.right() - x, t.height()))
        self._draw(p, t, right); p.restore()
        full = QRectF(self.rect())
        self._pill(p, t.topLeft() + off, self.before, vs, False, self.details_left,
                   clip=QRectF(0, 0, x, full.height()))
        self._pill(p, QPointF(t.right() - 10, t.top() + 10), self.after, True, True, self.details,
                   clip=QRectF(x, 0, full.width() - x, full.height()))
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.drawLine(QPointF(x, t.top()), QPointF(x, t.bottom()))
        c = QPointF(x, t.center().y())
        p.setPen(Qt.NoPen); p.setBrush(QColor(theme.ACCENT)); p.drawEllipse(c, 15, 15)
        p.setBrush(QColor(theme.ON_ACCENT))
        for d in (-1, 1):
            p.drawPolygon(QPolygonF([QPointF(c.x() + d * 3, c.y() - 5), QPointF(c.x() + d * 9, c.y()),
                                     QPointF(c.x() + d * 3, c.y() + 5)]))

    def _move(self, x: float) -> None:
        t = self._target()
        if t.width() > 0:
            self.split = max(0.0, min(1.0, (x - t.left()) / t.width()))
            self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.mode == "split":
            self._drag = True
            self._move(e.position().x())
        else:
            self.clicked.emit()

    def mouseMoveEvent(self, e):
        if self._drag:
            self._move(e.position().x())
            return
        pt = e.position()
        for r, d in self._hits:
            if r.contains(pt):
                QToolTip.showText(e.globalPosition().toPoint(), d, self)
                return
        QToolTip.hideText()

    def leaveEvent(self, e):
        QToolTip.hideText()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag = False
