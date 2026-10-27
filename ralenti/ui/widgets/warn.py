from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from ..icons import pixmap


class WarnRow(QFrame):
    """Inline yellow warning with an optional quick-fix button. Hidden by default."""

    def __init__(self, text: str, action: str | None = None, fn=None):
        super().__init__()
        self.setObjectName("WarnRow")
        h = QHBoxLayout(self); h.setContentsMargins(12, 10, 10, 10); h.setSpacing(10)
        ic = QLabel(); ic.setPixmap(pixmap("warning", 18, "warn"))
        self.label = QLabel(text); self.label.setWordWrap(True)
        h.addWidget(ic, 0, Qt.AlignTop); h.addWidget(self.label, 1)
        if action:
            b = QPushButton(action); b.setProperty("ghost", True); b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            h.addWidget(b, 0, Qt.AlignVCenter)
        self.hide()