from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QPushButton, QSizePolicy

from ..icons import icon


class Segmented(QFrame):
    """Row of chips; exactly one selected. options: [(label, value, tooltip[, icon_name])].
    Chips share the width equally and never force the panel wider."""
    changed = Signal(object)

    def __init__(self, options):
        super().__init__()
        self.setObjectName("Seg")
        h = QHBoxLayout(self); h.setContentsMargins(3, 3, 3, 3); h.setSpacing(2)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        self._values = []
        for i, opt in enumerate(options):
            label, value, tip = opt[:3]
            ic = opt[3] if len(opt) > 3 else None
            b = QPushButton(label); b.setObjectName("SegBtn"); b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor); b.setFocusPolicy(Qt.NoFocus)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            if ic:
                b.setIcon(icon(ic, "warn", active="warn", checked="on_accent"))
                b.setIconSize(QSize(13, 13))
            if tip:
                b.setToolTip(tip)
            self.group.addButton(b, i); h.addWidget(b, 1)
            self._values.append(value)
        self.group.idClicked.connect(lambda i: self.changed.emit(self._values[i]))

    def value(self):
        i = self.group.checkedId()
        return self._values[i] if i >= 0 else None

    def set_value(self, v) -> bool:
        for i, val in enumerate(self._values):
            if val == v:
                self.group.button(i).setChecked(True)
                return True
        return False