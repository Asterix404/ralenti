from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..util import muted


class PlaceholderPage(QWidget):
    def __init__(self, title: str, text: str):
        super().__init__()
        lay = QVBoxLayout(self); lay.setContentsMargins(28, 24, 28, 24)
        t = QLabel(title); t.setObjectName("PageTitle")
        lay.addWidget(t); lay.addWidget(muted(QLabel(text))); lay.addStretch(1)
        lay.setAlignment(Qt.AlignTop)