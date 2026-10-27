"""Step chips for job cards: Analyze ✓ — Interpolate (pulsing) — VMAF — Encode — Save."""
from PySide6.QtCore import QAbstractAnimation, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .. import theme

SHORT = {"analyze": "Analyze", "interpolate": "Interpolate", "audio": "Audio", "vmaf_search": "VMAF",
         "encode": "Encode", "mux": "Mux", "cut": "Cut", "merge": "Merge", "preview": "Render", "finalize": "Save"}


def _color(status: str) -> QColor:
    return QColor({"done": theme.OK, "running": theme.ACCENT, "failed": theme.DANGER}.get(status, theme.FAINT))


class StepTrack(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(24)
        self._steps: list[tuple[str, str]] = []
        self._phase = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0); self._anim.setEndValue(1.0)
        self._anim.setDuration(1400); self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._tick)

    def set_steps(self, steps) -> None:
        new = [(SHORT.get(s.kind, s.label), s.status.value) for s in steps]
        if new != self._steps:
            self._steps = new
            self.update()
        running = any(st == "running" for _, st in new)
        active = self._anim.state() == QAbstractAnimation.Running
        if running and not active:
            self._anim.start()
        elif not running and active:
            self._anim.stop()

    def _tick(self, v) -> None:
        self._phase = float(v)
        self.update()

    def paintEvent(self, _):
        if not self._steps:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        f = QFont(self.font()); f.setPointSizeF(max(7.5, f.pointSizeF() * 0.85)); f.setWeight(QFont.DemiBold)
        p.setFont(f)
        fm = p.fontMetrics()
        h, y, x = self.height() - 4, 2.0, 0.5
        cy = y + h / 2
        prev_done = False
        for i, (label, st) in enumerate(self._steps):
            w = fm.horizontalAdvance(label) + 32
            if i:
                p.setPen(QPen(QColor(theme.OK if prev_done else theme.BORDER), 1.5))
                p.drawLine(QPointF(x, cy), QPointF(x + 10, cy))
                x += 12
            if x + w > self.width():
                p.setPen(QColor(theme.FAINT))
                p.drawText(QRectF(x, y, 30, h), Qt.AlignVCenter, "…")
                break
            col = _color(st)
            if st == "queued":
                p.setPen(QPen(QColor(theme.BORDER), 1)); p.setBrush(Qt.NoBrush)
            else:
                bg = QColor(col); bg.setAlpha(36)
                p.setPen(Qt.NoPen); p.setBrush(bg)
            p.drawRoundedRect(QRectF(x, y, w, h), h / 2, h / 2)
            self._marker(p, st, QPointF(x + 12, cy), col)
            p.setPen(QColor(theme.MUTED) if st == "queued" else col)
            p.drawText(QRectF(x + 21, y, w - 24, h), Qt.AlignVCenter | Qt.AlignLeft, label)
            x += w
            prev_done = st == "done"

    def _marker(self, p: QPainter, st: str, c: QPointF, col: QColor) -> None:
        if st == "done":
            path = QPainterPath(QPointF(c.x() - 3.2, c.y() + 0.2))
            path.lineTo(c.x() - 0.8, c.y() + 2.6); path.lineTo(c.x() + 3.4, c.y() - 2.6)
            p.setPen(QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)); p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        elif st == "running":
            ring = QColor(col); ring.setAlphaF(max(0.0, 1 - self._phase) * 0.8)
            r = 3.5 + 4 * self._phase
            p.setPen(QPen(ring, 1.4)); p.setBrush(Qt.NoBrush); p.drawEllipse(c, r, r)
            p.setPen(Qt.NoPen); p.setBrush(col); p.drawEllipse(c, 3.5, 3.5)
        elif st == "failed":
            p.setPen(QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(c.x() - 2.6, c.y() - 2.6), QPointF(c.x() + 2.6, c.y() + 2.6))
            p.drawLine(QPointF(c.x() - 2.6, c.y() + 2.6), QPointF(c.x() + 2.6, c.y() - 2.6))
        else:
            p.setPen(QPen(QColor(theme.FAINT), 1.3)); p.setBrush(Qt.NoBrush); p.drawEllipse(c, 3.2, 3.2)