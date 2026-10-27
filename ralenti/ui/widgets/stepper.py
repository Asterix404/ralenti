from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractSpinBox, QDoubleSpinBox, QFrame, QHBoxLayout, QPushButton,
                               QSizePolicy)


class Stepper(QFrame):
    """Big, obvious − value + control. Fixed heights so the number is never clipped."""
    valueChanged = Signal(float)

    def __init__(self, lo, hi, value=0, step=1, decimals=0, suffix=""):
        super().__init__()
        self.setObjectName("Stepper")
        self._dec = decimals
        self.setFixedHeight(40)
        h = QHBoxLayout(self); h.setContentsMargins(3, 3, 3, 3); h.setSpacing(0)
        self.minus, self.plus = QPushButton("−"), QPushButton("+")
        for b in (self.minus, self.plus):
            b.setObjectName("StepBtn"); b.setAutoRepeat(True); b.setFixedSize(36, 32)
            b.setFocusPolicy(Qt.NoFocus); b.setCursor(Qt.PointingHandCursor)
        self.spin = QDoubleSpinBox(); self.spin.setObjectName("StepSpin")
        self.spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignCenter)
        self.spin.setFixedHeight(32); self.spin.setMinimumWidth(72)
        self.spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.spin.setDecimals(decimals); self.spin.setRange(lo, hi)
        self.spin.setSingleStep(step); self.spin.setSuffix(suffix); self.spin.setValue(value)
        self.minus.clicked.connect(self.spin.stepDown)
        self.plus.clicked.connect(self.spin.stepUp)
        self.spin.valueChanged.connect(self.valueChanged.emit)
        h.addWidget(self.minus); h.addWidget(self.spin, 1); h.addWidget(self.plus)

    def value(self):
        v = self.spin.value()
        return int(round(v)) if self._dec == 0 else v

    def setValue(self, v) -> None:
        self.spin.setValue(float(v))

    def setRange(self, lo, hi) -> None:
        self.spin.setRange(lo, hi)
