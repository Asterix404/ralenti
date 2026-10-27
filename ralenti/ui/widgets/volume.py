"""Mute button that reveals a volume slider on hover (YouTube style). Scroll to adjust."""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QSlider, QWidget

from ..icons import icon


class VolumeControl(QWidget):
    level = 0.8   # shared by every player during this session

    def __init__(self, button):
        super().__init__()
        self.btn, self.audio = button, None
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        self.slider = QSlider(Qt.Horizontal); self.slider.setObjectName("Volume")
        self.slider.setRange(0, 100); self.slider.setValue(int(VolumeControl.level * 100))
        self.slider.setFocusPolicy(Qt.NoFocus); self.slider.setMaximumWidth(0)
        self.slider.setCursor(Qt.PointingHandCursor)
        h.addWidget(self.btn); h.addWidget(self.slider)
        self.btn.setToolTip("Mute (M) · hover or scroll to change volume")
        self.anim = QPropertyAnimation(self.slider, b"maximumWidth", self)
        self.anim.setDuration(170); self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.hide_timer = QTimer(self); self.hide_timer.setSingleShot(True); self.hide_timer.setInterval(700)
        self.hide_timer.timeout.connect(self._collapse)
        self.slider.valueChanged.connect(self._set_volume)

    def bind(self, audio) -> None:
        self.audio = audio
        audio.setVolume(VolumeControl.level)
        audio.mutedChanged.connect(self._icon)
        audio.volumeChanged.connect(self._icon)
        self._icon()

    def _set_volume(self, v: int) -> None:
        VolumeControl.level = v / 100
        self.slider.setToolTip(f"Volume {v}%")
        if self.audio:
            self.audio.setVolume(v / 100)
            if v > 0 and self.audio.isMuted():
                self.audio.setMuted(False)

    def _icon(self, *_):
        if not self.audio:
            return
        v = self.audio.volume()
        muted = self.audio.isMuted() or v <= 0.001
        name = "speaker-slash" if muted else ("speaker-low" if v < 0.5 else "speaker-high")
        self.btn.setIcon(icon(name, "muted", active="text", checked=None))

    def _animate(self, width: int) -> None:
        self.anim.stop()
        self.anim.setStartValue(self.slider.maximumWidth()); self.anim.setEndValue(width)
        self.anim.start()

    def _collapse(self) -> None:
        if self.slider.isSliderDown():
            self.hide_timer.start()
        else:
            self._animate(0)

    def enterEvent(self, e):
        self.hide_timer.stop(); self._animate(96)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hide_timer.start()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        step = 5 if e.angleDelta().y() > 0 else -5
        self.slider.setValue(max(0, min(100, self.slider.value() + step)))
        e.accept()