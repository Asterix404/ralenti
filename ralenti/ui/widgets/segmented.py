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



class Tabs(Segmented):
    """Segmented used as tabs: labels can change, and a dot shows when something in a tab is set."""

    def __init__(self, options):
        super().__init__(options)
        self._labels = {opt[1]: opt[0] for opt in options}
        self._dots = {}

    def _paint(self, key) -> None:
        if key in self._values:
            b = self.group.button(self._values.index(key))
            b.setText(self._labels[key] + ("  •" if self._dots.get(key) else ""))

    def set_label(self, key, text: str) -> None:
        self._labels[key] = text
        self._paint(key)

    def set_dot(self, key, on: bool) -> None:
        if self._dots.get(key) != bool(on):
            self._dots[key] = bool(on)
            self._paint(key)
