"""Small editor widgets: an animated on/off switch and the unified step summary."""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

from .. import theme
from ..icons import icon, pixmap


class ToggleSwitch(QAbstractButton):
    def __init__(self):
        super().__init__()
        self.setCheckable(True); self.setCursor(Qt.PointingHandCursor); self.setFixedSize(44, 24)
        self._pos = 0.0
        self.anim = QVariantAnimation(self); self.anim.setDuration(140)
        self.anim.valueChanged.connect(self._move)
        self.toggled.connect(self._animate)

    def _move(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _animate(self, on: bool) -> None:
        self.anim.stop(); self.anim.setStartValue(self._pos); self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.start()

    def set_state(self, on: bool) -> None:
        self.blockSignals(True); self.setChecked(on); self.blockSignals(False)
        self.anim.stop(); self._pos = 1.0 if on else 0.0
        self.update()

    def sizeHint(self):
        return QSize(44, 24)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        off, on = QColor(theme.SURFACE3), QColor(theme.ACCENT)
        c = QColor(int(off.red() + (on.red() - off.red()) * self._pos),
                   int(off.green() + (on.green() - off.green()) * self._pos),
                   int(off.blue() + (on.blue() - off.blue()) * self._pos))
        r = QRectF(1, 1, self.width() - 2, self.height() - 2)
        p.setPen(Qt.NoPen); p.setBrush(c); p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * self._pos
        p.setBrush(QColor(theme.ON_ACCENT if self._pos > 0.5 else theme.TEXT))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))


class _Row(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, ic: str, title: str):
        super().__init__()
        self.key = key
        self.setObjectName("SumStep"); self.setAttribute(Qt.WA_Hover)
        if key != "clips":
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(f"Go to {title}")
        h = QHBoxLayout(self); h.setContentsMargins(6, 4, 6, 4); h.setSpacing(10)
        self.ic = QLabel(); self.ic.setPixmap(pixmap(ic, 15, "muted")); self.ic.setFixedWidth(18)
        self.title = QLabel(title.upper()); self.title.setObjectName("SumLabel"); self.title.setFixedWidth(92)
        self.value = QLabel(); self.value.setObjectName("SumValue"); self.value.setWordWrap(True)
        self.value.setTextFormat(Qt.RichText)
        h.addWidget(self.ic, 0, Qt.AlignTop); h.addWidget(self.title, 0, Qt.AlignTop); h.addWidget(self.value, 1)

    def set(self, text: str, on: bool) -> None:
        self.value.setText(text)
        if self.property("off") != (not on):
            self.setProperty("off", not on)
            for w in (self, self.value, self.title):
                w.style().unpolish(w); w.style().polish(w)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.key != "clips":
            self.clicked.emit(self.key)


ROWS = [("clips", "film-strip", "Video"), ("trim", "scissors", "Trim"), ("crop", "crop", "Transform"),
        ("motion", "sparkle", "Interpolation"), ("audio", "speaker-high", "Audio"), ("encode", "gauge", "Encode"),
        ("output", "export", "Output")]


class StepSummary(QFrame):
    step_clicked = Signal(str)
    add_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Summary")
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 14, 14); v.setSpacing(4)
        lab = QLabel("SUMMARY"); lab.setObjectName("FieldLabel")
        v.addWidget(lab); v.addSpacing(4)
        self.rows = {}
        for key, ic, title in ROWS:
            r = _Row(key, ic, title); r.clicked.connect(self.step_clicked.emit)
            v.addWidget(r); self.rows[key] = r
        self.est = _Row("clips", "hourglass", "Estimate")
        v.addWidget(self.est)
        self.msgs = QLabel(); self.msgs.setWordWrap(True); self.msgs.setTextFormat(Qt.RichText)
        v.addSpacing(6); v.addWidget(self.msgs)
        self.autostart = QCheckBox("Start the queue right away"); self.autostart.setChecked(True)
        self.btn = QPushButton("Add to queue"); self.btn.setProperty("accent", True); self.btn.setMinimumHeight(42)
        self.btn.setIcon(icon("plus-circle", "on_accent", checked=None)); self.btn.setIconSize(QSize(17, 17))
        self.btn.setCursor(Qt.PointingHandCursor); self.btn.clicked.connect(self.add_clicked.emit)
        v.addSpacing(6); v.addWidget(self.autostart); v.addWidget(self.btn)

    def update_rows(self, rows: dict, msgs: list, est, can_add: bool) -> None:
        for key, r in self.rows.items():
            text, on = rows.get(key, ("–", False))
            r.set(text, on)
            r.setVisible(bool(rows) or key == "clips")
        if not rows:
            self.rows["clips"].set("No video open yet", False)
        self.est.set(est or "", True)
        self.est.setVisible(bool(est))
        colors = {"error": theme.DANGER, "warning": theme.WARN}
        self.msgs.setText("<br>".join(f"<span style='color:{colors.get(lvl, theme.MUTED)}'>{m}</span>"
                                      for lvl, m in msgs))
        self.msgs.setVisible(bool(msgs))
        self.btn.setEnabled(can_add)
