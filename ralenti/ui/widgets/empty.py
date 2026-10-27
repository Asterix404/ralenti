from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from ..icons import icon, pixmap


class EmptyState(QWidget):
    """Icon / title / hint / button, centred with stretches. The hint gets a fixed width and
    the exact height its wrapped text needs, so the layout can never squash it. When space is
    short the icon goes first, then the hint."""
    action = Signal()

    def __init__(self, icon_name: str, title: str, hint: str, action_text: str | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("Empty")
        v = QVBoxLayout(self); v.setContentsMargins(16, 12, 16, 12); v.setSpacing(6)
        self.ic = QLabel(); self.ic.setPixmap(pixmap(icon_name, 40, "faint")); self.ic.setAlignment(Qt.AlignCenter)
        self.title = QLabel(title); self.title.setObjectName("EmptyTitle"); self.title.setAlignment(Qt.AlignCenter)
        self.hint = QLabel(hint); self.hint.setObjectName("Hint"); self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.btn = None
        v.addStretch(1)
        v.addWidget(self.ic, 0, Qt.AlignHCenter)
        v.addWidget(self.title, 0, Qt.AlignHCenter)
        v.addWidget(self.hint, 0, Qt.AlignHCenter)
        if action_text:
            self.btn = QPushButton(action_text); self.btn.setProperty("accent", True)
            self.btn.setIcon(icon("plus", "on_accent", checked=None)); self.btn.setIconSize(QSize(16, 16))
            self.btn.setCursor(Qt.PointingHandCursor); self.btn.clicked.connect(self.action.emit)
            v.addSpacing(6); v.addWidget(self.btn, 0, Qt.AlignHCenter)
        v.addStretch(1)

    def _fit(self) -> None:
        w = max(140, min(420, self.width() - 48))
        self.hint.setFixedWidth(w)
        hh = max(self.hint.fontMetrics().height(), self.hint.heightForWidth(w))
        self.hint.setFixedHeight(hh)
        avail = self.height() - 24
        need = self.title.sizeHint().height() + (self.btn.sizeHint().height() + 12 if self.btn else 0)
        show_hint = need + hh + 6 <= avail
        if show_hint:
            need += hh + 6
        self.hint.setVisible(show_hint)
        self.ic.setVisible(need + 46 <= avail)

    def resizeEvent(self, e):
        super().resizeEvent(e); self._fit()

    def showEvent(self, e):
        super().showEvent(e); self._fit()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() in (QEvent.StyleChange, QEvent.FontChange):
            self._fit()