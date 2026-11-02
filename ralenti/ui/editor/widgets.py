"""Small editor widgets: an animated on/off switch, the list of problems with one-click fixes and the step summary
(collapsible)."""
from PySide6.QtCore import QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from ...core.edit import LABELS
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


class IssueBox(QFrame):
    """Problems, each with its fix buttons. A fix is (label, step, changes) or (label, 'go:<page>', None)."""
    fix = Signal(object)

    def __init__(self):
        super().__init__()
        self.setObjectName("IssueBox")
        self.v = QVBoxLayout(self); self.v.setContentsMargins(0, 0, 0, 0); self.v.setSpacing(6)
        self._rows = []
        self.hide()

    def set_issues(self, issues, go: bool = False) -> None:
        for w in self._rows:
            self.v.removeWidget(w); w.deleteLater()
        self._rows = []
        for it in issues:
            err = it["level"] == "error"
            f = QFrame(); f.setObjectName("Issue"); f.setProperty("level", "error" if err else "warning")
            fv = QVBoxLayout(f); fv.setContentsMargins(10, 8, 10, 8); fv.setSpacing(6)
            top = QHBoxLayout(); top.setSpacing(8)
            ic = QLabel(); ic.setPixmap(pixmap("x-circle" if err else "warning", 15, "danger" if err else "warn"))
            ic.setFixedWidth(16)
            t = QLabel(it["text"]); t.setWordWrap(True)
            top.addWidget(ic, 0, Qt.AlignTop); top.addWidget(t, 1)
            fv.addLayout(top)
            fixes = list(it.get("fixes") or [])
            if go and it.get("step") in LABELS:
                fixes.append((f"Go to {LABELS[it['step']]}", "go:" + it["step"], None))
            if fixes:
                row = QHBoxLayout(); row.setSpacing(4); row.addSpacing(24)
                for fx in fixes:
                    b = QPushButton(fx[0]); b.setObjectName("FixBtn"); b.setCursor(Qt.PointingHandCursor)
                    b.clicked.connect(lambda _=False, fx=fx: self.fix.emit(fx))
                    row.addWidget(b)
                row.addStretch(1)
                fv.addLayout(row)
            self.v.addWidget(f)
            self._rows.append(f)
        self.setVisible(bool(issues))


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
    fix = Signal(object)
    collapsed_changed = Signal(bool)

    def __init__(self, collapsed: bool = False):
        super().__init__()
        self.setObjectName("Summary")
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 14, 14); v.setSpacing(4)
        self.head = QPushButton("SUMMARY"); self.head.setObjectName("SumHead")
        self.head.setCursor(Qt.PointingHandCursor); self.head.setIconSize(QSize(12, 12))
        self.head.clicked.connect(self._toggle)
        self.compact = QLabel(); self.compact.setObjectName("SumValue"); self.compact.setWordWrap(True)
        self.compact.setTextFormat(Qt.RichText)
        self.body = QWidget()
        bv = QVBoxLayout(self.body); bv.setContentsMargins(0, 4, 0, 0); bv.setSpacing(4)
        self.rows = {}
        for key, ic, title in ROWS:
            r = _Row(key, ic, title); r.clicked.connect(self.step_clicked.emit)
            bv.addWidget(r); self.rows[key] = r
        self.est = _Row("clips", "hourglass", "Estimate")
        bv.addWidget(self.est)
        self.issues = IssueBox(); self.issues.fix.connect(self.fix.emit)
        self.autostart = QCheckBox("Start the queue right away"); self.autostart.setChecked(True)
        self.btn = QPushButton("Add to queue"); self.btn.setProperty("accent", True); self.btn.setMinimumHeight(42)
        self.btn.setIcon(icon("plus-circle", "on_accent", checked=None)); self.btn.setIconSize(QSize(17, 17))
        self.btn.setCursor(Qt.PointingHandCursor); self.btn.clicked.connect(self.add_clicked.emit)
        v.addWidget(self.head, 0, Qt.AlignLeft); v.addWidget(self.compact); v.addWidget(self.body)
        v.addSpacing(6); v.addWidget(self.issues); v.addSpacing(6)
        v.addWidget(self.autostart); v.addWidget(self.btn)
        self._collapsed, self._compact_text = False, ""
        self.set_collapsed(collapsed)

    def set_collapsed(self, on: bool) -> None:
        self._collapsed = bool(on)
        self.body.setVisible(not on)
        self.compact.setVisible(on and bool(self._compact_text))
        self.head.setIcon(icon("caret-right" if on else "caret-down", "muted", active="text", checked=None))
        self.head.setToolTip("Show the whole summary" if on else
                             "Collapse the summary: more room for the options above")

    def _toggle(self) -> None:
        self.set_collapsed(not self._collapsed)
        self.collapsed_changed.emit(self._collapsed)

    def update_rows(self, rows: dict, issues: list, est, can_add: bool, errors: int = 0) -> None:
        for key, r in self.rows.items():
            text, on = rows.get(key, ("–", False))
            r.set(text, on)
            r.setVisible(bool(rows) or key == "clips")
        if not rows:
            self.rows["clips"].set("No video open yet", False)
        self.est.set(est or "", True)
        self.est.setVisible(bool(est))
        out = rows.get("output", ("", False))[0] if rows else ""
        self._compact_text = " · ".join(x for x in (out, est or "") if x)
        self.compact.setText(self._compact_text)
        self.compact.setVisible(self._collapsed and bool(self._compact_text))
        self.issues.set_issues(issues, go=True)
        self.btn.setEnabled(can_add)
        self.btn.setText(f"Fix {errors} problem{'s' if errors != 1 else ''} first" if errors else "Add to queue")
