from PySide6.QtCore import QSize
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ..icons import icon as make_icon
from .choice import RichCombo


def page_header(title: str, sub: str) -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 8); v.setSpacing(3)
    t = QLabel(title); t.setObjectName("PageTitle")
    s = QLabel(sub); s.setObjectName("PageSub"); s.setWordWrap(True)
    v.addWidget(t); v.addWidget(s)
    return w


def section(title: str, divider: bool = True) -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 2, 0, 0); v.setSpacing(10)
    if divider:
        line = QFrame(); line.setObjectName("Divider"); v.addWidget(line)
    if title:
        t = QLabel(title); t.setObjectName("Section"); v.addWidget(t)
    return w


def button(text: str, fn, kind: str | None = None, icon: str | None = None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty(kind, True)
    if icon:
        tone = "on_accent" if kind == "accent" else ("muted" if kind == "ghost" else "text")
        b.setIcon(make_icon(icon, tone, active=None if kind == "accent" else "text"))
        b.setIconSize(QSize(16, 16))
    b.clicked.connect(fn)
    return b


class Field(QWidget):
    """LABEL / control / hint. RichCombo hints follow the selected item's description."""

    def __init__(self, title: str, widget: QWidget, hint: str = ""):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        self.label = QLabel(title.upper()); self.label.setObjectName("FieldLabel")
        self.hint = QLabel(); self.hint.setObjectName("Hint"); self.hint.setWordWrap(True)
        v.addWidget(self.label); v.addWidget(widget); v.addWidget(self.hint)
        self.set_hint(hint)
        if isinstance(widget, RichCombo):
            widget.currentIndexChanged.connect(lambda *_: self.set_hint(widget.blurb()))
            self.set_hint(widget.blurb() or hint)

    def set_hint(self, text: str) -> None:
        self.hint.setText(text or "")
        self.hint.setVisible(bool(text))


def v_scroll(widget: QWidget):
    """Vertical-only scroll area: content always fits the width, no sideways scrollbar."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea
    s = QScrollArea(); s.setWidgetResizable(True); s.setWidget(widget)
    s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return s
