"""Tool rows under the player (one per step) and the 'applied before this step' chips."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QToolButton, QWidget

from ...core.edit import LABELS
from ..icons import icon


def tool(text: str, ic, tip: str, fn=None, checkable: bool = False, accent: bool = False) -> QToolButton:
    b = QToolButton(); b.setObjectName("ToolBtn"); b.setText(text); b.setToolTip(tip)
    b.setCheckable(checkable); b.setCursor(Qt.PointingHandCursor); b.setFocusPolicy(Qt.NoFocus)
    if accent:
        b.setProperty("accent", True)
    if ic:
        b.setIcon(icon(ic, "on_accent" if accent else "muted", active=None if accent else "text",
                       checked=None if accent else "accent"))
        b.setIconSize(QSize(16, 16))
    b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon if text and ic else
                         (Qt.ToolButtonTextOnly if text else Qt.ToolButtonIconOnly))
    if fn:
        (b.toggled if checkable else b.clicked).connect(fn)
    return b


class ToolRow(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("ToolRow")
        self.h = QHBoxLayout(self); self.h.setContentsMargins(6, 4, 8, 4); self.h.setSpacing(2)
        self.note = QLabel(); self.note.setObjectName("Hint")
        self.note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.note.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def add(self, *ws) -> None:
        for w in ws:
            self.h.addWidget(w)

    def sep(self) -> None:
        line = QFrame(); line.setObjectName("ToolSep"); line.setFixedSize(1, 18)
        self.h.addSpacing(4); self.h.addWidget(line); self.h.addSpacing(4)

    def finish(self) -> None:
        self.h.addWidget(self.note, 1)


class AppliedChips(QWidget):
    clicked = Signal(str)

    def __init__(self):
        super().__init__()
        self.h = QHBoxLayout(self); self.h.setContentsMargins(2, 0, 0, 0); self.h.setSpacing(6)
        lab = QLabel("APPLIED"); lab.setObjectName("FieldLabel")
        lab.setToolTip("What the steps before this one already do. The picture shows it.")
        self.h.addWidget(lab); self.h.addStretch(1)
        self._btns = []
        self.hide()

    def set_items(self, items) -> None:
        for b in self._btns:
            self.h.removeWidget(b); b.deleteLater()
        self._btns = []
        for key, ic, text in items:
            b = QPushButton(text); b.setObjectName("AppliedChip"); b.setCursor(Qt.PointingHandCursor)
            b.setIcon(icon(ic, "accent", checked=None)); b.setIconSize(QSize(14, 14))
            b.setToolTip(f"Go to {LABELS.get(key, key)}")
            b.clicked.connect(lambda _=False, k=key: self.clicked.emit(k))
            self.h.insertWidget(self.h.count() - 1, b)
            self._btns.append(b)
