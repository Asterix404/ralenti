"""Shared page building blocks: collapsible sections, the options column, the summary card
and the output field. Every page uses these so they look and behave the same."""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import theme
from ..icons import icon, pixmap
from ..thumbs import rounded, thumbs
from ..util import clip
from .fields import button, v_scroll

TONES = {"warn": theme.WARN, "danger": theme.DANGER, "ok": theme.OK}


def _collapsed(settings) -> set[str]:
    return {k for k in (getattr(settings, "collapsed_sections", "") or "").split(",") if k}


def _remember(settings, key: str, collapsed: bool) -> None:
    keys = _collapsed(settings)
    (keys.add if collapsed else keys.discard)(key)
    settings.collapsed_sections = ",".join(sorted(keys))
    settings.save()


class _Header(QFrame):
    clicked = Signal()

    def __init__(self, icon_name: str, title: str):
        super().__init__()
        self.setObjectName("SectionHeader")
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self); h.setContentsMargins(6, 6, 8, 6); h.setSpacing(10)
        ic = QLabel(); ic.setPixmap(pixmap(icon_name, 18, "accent"))
        t = QLabel(title); t.setObjectName("SectionTitle")
        self.caret = QLabel()
        h.addWidget(ic); h.addWidget(t, 1); h.addWidget(self.caret)

    def set_open(self, on: bool) -> None:
        self.caret.setPixmap(pixmap("caret-down" if on else "caret-right", 14, "muted"))
        self.setToolTip("Click to collapse" if on else "Click to expand")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class Section(QWidget):
    """Icon + title header that collapses its content with an animation. The open/closed
    state is remembered per key. Repaints the scroll viewport on every frame (no ghosting)."""

    def __init__(self, key: str, title: str, icon_name: str, content: QWidget,
                 settings=None, divider: bool = True, expanded: bool = True):
        super().__init__()
        self.key, self.settings = key, settings
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        if divider:
            line = QFrame(); line.setObjectName("Divider"); v.addWidget(line)
        self.header = _Header(icon_name, title)
        self.body = QWidget()
        bl = QVBoxLayout(self.body); bl.setContentsMargins(4, 4, 2, 8); bl.addWidget(content)
        v.addWidget(self.header); v.addWidget(self.body)
        self._open = expanded and not (settings and key in _collapsed(settings))
        self.body.setMaximumHeight(16777215 if self._open else 0)
        self.body.setVisible(self._open)
        self.header.set_open(self._open)
        self.anim = QPropertyAnimation(self.body, b"maximumHeight", self)
        self.anim.setDuration(200); self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._repaint)
        self.anim.finished.connect(self._done)
        self.header.clicked.connect(self.toggle)

    def _repaint(self, *_) -> None:
        w = self.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        (w.viewport() if w is not None else self.window()).update()

    def toggle(self) -> None:
        self._open = not self._open
        self.header.set_open(self._open)
        self.anim.stop()
        if self._open:
            self.body.setVisible(True)
            self.anim.setStartValue(0); self.anim.setEndValue(max(1, self.body.sizeHint().height()))
        else:
            self.anim.setStartValue(self.body.height()); self.anim.setEndValue(0)
        self.anim.start()
        if self.settings:
            _remember(self.settings, self.key, not self._open)

    def _done(self) -> None:
        if self._open:
            self.body.setMaximumHeight(16777215)
        else:
            self.body.setVisible(False)
        self._repaint()


class OutputField(QWidget):
    changed = Signal(str)

    def __init__(self, kind: str = "dir", placeholder: str = "Next to the source file", filter_: str = ""):
        super().__init__()
        self.kind, self.filter = kind, filter_
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        self.edit = QLineEdit(); self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed.emit)
        h.addWidget(self.edit, 1)
        h.addWidget(button("Browse", self._browse, icon="folder-open"))

    def _browse(self) -> None:
        if self.kind == "dir":
            p = QFileDialog.getExistingDirectory(self, "Choose output folder", self.text())
        else:
            p, _ = QFileDialog.getSaveFileName(self, "Save as", self.text(), self.filter)
        if p:
            self.edit.setText(p)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, t: str) -> None:
        self.edit.setText(t)


def _dot(color: str) -> QPixmap:
    pm = QPixmap(36, 36); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen); p.setBrush(QColor(color)); p.drawEllipse(12, 12, 12, 12); p.end()
    pm.setDevicePixelRatio(2)
    return pm


class _SumRow(QWidget):
    def __init__(self):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10)
        self.ic = QLabel(); self.ic.setFixedSize(18, 18); self.ic.setAlignment(Qt.AlignCenter)
        self.label = QLabel(); self.label.setObjectName("SumLabel"); self.label.setFixedWidth(66)
        self.value = QLabel(); self.value.setObjectName("SumValue")
        self.value.setWordWrap(True); self.value.setTextFormat(Qt.RichText)
        h.addWidget(self.ic, 0, Qt.AlignTop); h.addWidget(self.label, 0, Qt.AlignTop); h.addWidget(self.value, 1)

    def set(self, ic, label, value, tone=None) -> None:
        color = TONES.get(tone)
        self.ic.setPixmap(pixmap(ic, 15, tone or "muted") if ic else _dot(color or theme.ACCENT))
        self.label.setText((label or "").upper()); self.label.setVisible(bool(label))
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {color};" if color else "")


class SummaryPanel(QFrame):
    """Thumbnail + title, labeled rows with icons, auto-start and the main button."""
    add_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("Summary")
        self._src = None
        self._rows: list[_SumRow] = []
        v = QVBoxLayout(self); v.setContentsMargins(16, 14, 16, 16); v.setSpacing(12)
        lab = QLabel("SUMMARY"); lab.setObjectName("FieldLabel")
        self.head = QWidget()
        hh = QHBoxLayout(self.head); hh.setContentsMargins(0, 0, 0, 0); hh.setSpacing(12)
        self.thumb = QLabel(); self.thumb.setFixedSize(112, 63)
        texts = QVBoxLayout(); texts.setSpacing(2)
        self.title = QLabel(); self.title.setObjectName("SummaryTitle")
        self.sub = QLabel(); self.sub.setObjectName("Hint"); self.sub.setWordWrap(True)
        texts.addStretch(1); texts.addWidget(self.title); texts.addWidget(self.sub); texts.addStretch(1)
        hh.addWidget(self.thumb); hh.addLayout(texts, 1)
        self.head.hide()
        self.rows_box = QWidget(); self.rows_box.setObjectName("SumRows")
        self.rows_l = QVBoxLayout(self.rows_box); self.rows_l.setContentsMargins(0, 0, 0, 0); self.rows_l.setSpacing(9)
        self.autostart = QCheckBox("Start the queue right away"); self.autostart.setChecked(True)
        self.btn = QPushButton(); self.btn.setProperty("accent", True); self.btn.setMinimumHeight(42)
        self.btn.setIcon(icon("plus-circle", "on_accent", checked=None)); self.btn.setIconSize(QSize(17, 17))
        self.btn.setCursor(Qt.PointingHandCursor); self.btn.clicked.connect(self.add_clicked.emit)
        for w in (lab, self.head, self.rows_box, self.autostart, self.btn):
            v.addWidget(w)
        thumbs().ready.connect(lambda p: p == self._src and self._paint_thumb())

    def set_media(self, path: str | None, title: str = "", sub: str = "") -> None:
        self._src = path
        self.head.setVisible(bool(path))
        if path:
            self.title.setText(clip(title, 40)); self.title.setToolTip(title)
            self.sub.setText(sub)
            self._paint_thumb()

    def _paint_thumb(self) -> None:
        self.thumb.setPixmap(rounded(thumbs().get(self._src), 112, 63, 8, self.devicePixelRatioF()))

    def set_rows(self, rows, warn: str | None = None) -> None:
        """rows: [(icon, label, value[, tone])]. Empty values are skipped."""
        rows = [r for r in rows if r and r[2]]
        if warn:
            rows.append(("warning", "", warn, "warn"))
        while len(self._rows) < len(rows):
            r = _SumRow(); self.rows_l.addWidget(r); self._rows.append(r)
        for w, r in zip(self._rows, rows):
            w.set(*r); w.show()
        for w in self._rows[len(rows):]:
            w.hide()
        self.rows_box.setVisible(bool(rows))

    def set_lines(self, lines, warn: str | None = None) -> None:
        """Accepts plain strings or (icon, label, value) tuples."""
        self.set_rows([l if isinstance(l, tuple) else (None, "", l) for l in lines if l], warn)

    def set_action(self, text: str, enabled: bool) -> None:
        self.btn.setText(text)
        self.btn.setEnabled(enabled)


def side_column(content: QWidget, summary: QWidget, width: int = 430) -> QWidget:
    """Options card (solid background, scrolls vertically, ends cleanly) with the summary below."""
    col = QWidget(); col.setFixedWidth(width)
    v = QVBoxLayout(col); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
    card = QFrame(); card.setObjectName("Card")
    cl = QVBoxLayout(card); cl.setContentsMargins(1, 1, 1, 1)
    content.setObjectName("OptionsBody"); content.setAttribute(Qt.WA_StyledBackground)
    sc = options_scroll(content)
    cl.addWidget(sc)
    v.addWidget(card, 1); v.addWidget(summary)
    return col


def options_scroll(content: QWidget):
    """Vertical scroll area that paints a solid background (prevents ghosting while collapsing)."""
    sc = v_scroll(content); sc.setFrameShape(QFrame.NoFrame)
    sc.setObjectName("OptionsScroll")
    sc.viewport().setObjectName("OptionsViewport")
    sc.viewport().setAutoFillBackground(True)
    return sc


def _instant_toggle(self) -> None:
    """Collapse/expand without animation: the animation left stale frames behind on some setups."""
    self._open = not self._open
    self.header.set_open(self._open)
    self.anim.stop()
    self.body.setMaximumHeight(16777215)
    self.body.setVisible(self._open)
    if self.settings:
        _remember(self.settings, self.key, not self._open)
    self._repaint()


Section.toggle = _instant_toggle
