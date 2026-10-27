"""VmafPicker and SlowmoPicker: recommended values as chips, custom on demand."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from .. import theme
from ..icons import icon, pixmap
from .segmented import Segmented
from .stepper import Stepper

VMAF_LEVELS = [  # label, value, description, icon
    ("Off", None, "No quality test: uses the encoder's standard setting. Fastest, but file size "
                  "and quality vary from video to video.", None),
    ("90", 90.0, "Good. Small files for phones and social media; slight softness on big screens.", None),
    ("93", 93.0, "High. Very hard to tell apart from the original at normal viewing distance.", None),
    ("95", 95.0, "Recommended. The usual industry target: differences are rarely visible, "
                 "even side by side.", "star-fill"),
    ("97", 97.0, "Near-lossless. For archiving or further editing. Noticeably larger files.", None),
    ("Custom", "custom", "", None),
]


def _explainer() -> str:
    b = f"color:{theme.TEXT}"
    return (f"<p style='margin:0'><b style='{b}'>VMAF</b> is a score from 0 to 100, made by Netflix, "
            f"that predicts how closely a video matches the original <i>to a human viewer</i>. "
            f"100 means indistinguishable.</p>"
            f"<p><b style='{b}'>Why use it</b><br>Instead of guessing a quality number, Ralenti "
            f"encodes a few short samples, measures them, and keeps the smallest file that still "
            f"reaches your target. Every video ends up at the same visual quality, with no wasted space.</p>"
            f"<p style='margin-bottom:0'><b style='{b}'>Trade-off</b><br>The sample tests take extra "
            f"time. On interpolation jobs that's small next to the AI step; on Encode jobs it can add "
            f"up to about half the encode time. Interpolation jobs also need temporary disk space. "
            f"Pick <b style='{b}'>Off</b> when speed matters more than file size.</p>")


def _custom_desc(v: float) -> str:
    if v < 90:
        return "Visible quality loss is likely. Smallest files."
    if v < 93:
        return "Good quality, small files."
    if v < 95:
        return "High quality, hard to tell apart from the original."
    if v < 97:
        return "Practically transparent. Close to the recommended 95."
    return "Archival quality. Large files with little visible gain."


class VmafPicker(QWidget):
    changed = Signal(object)   # float or None

    def __init__(self, default=95.0):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        self.seg = Segmented([(label, val, "", ic) for label, val, _d, ic in VMAF_LEVELS])

        self.slider = QSlider(Qt.Horizontal); self.slider.setRange(170, 198); self.slider.setValue(190)
        self.slider_val = QLabel(); self.slider_val.setObjectName("Muted"); self.slider_val.setMinimumWidth(72)
        self.custom_row = QWidget()
        h = QHBoxLayout(self.custom_row); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.slider, 1); h.addWidget(self.slider_val)

        self.desc = QLabel(); self.desc.setObjectName("Hint"); self.desc.setWordWrap(True)

        self.cost = QWidget()
        ch = QHBoxLayout(self.cost); ch.setContentsMargins(0, 0, 0, 0); ch.setSpacing(6)
        ci = QLabel(); ci.setPixmap(pixmap("timer", 14, "faint"))
        ct = QLabel("Adds a quick sample test before the final encode, for smaller files at "
                    "a guaranteed quality.")
        ct.setObjectName("Hint"); ct.setWordWrap(True)
        ch.addWidget(ci, 0, Qt.AlignTop); ch.addWidget(ct, 1)

        self.more = QPushButton("What is VMAF?"); self.more.setProperty("ghost", True)
        self.more.setIcon(icon("info", "muted", active="text", checked=None)); self.more.setIconSize(QSize(15, 15))
        self.more.setCursor(Qt.PointingHandCursor); self.more.clicked.connect(self._toggle_explainer)
        mrow = QHBoxLayout(); mrow.setContentsMargins(0, 0, 0, 0); mrow.addWidget(self.more); mrow.addStretch(1)

        self.explainer = QLabel(_explainer()); self.explainer.setWordWrap(True)
        self.explainer.setTextFormat(Qt.RichText)
        self.explainer.setStyleSheet(f"background: {theme.SURFACE2}; border: 1px solid {theme.BORDER}; "
                                     f"border-radius: 10px; padding: 12px; color: {theme.MUTED};")
        self.explainer.hide()

        v.addWidget(self.seg); v.addWidget(self.custom_row); v.addWidget(self.desc)
        v.addWidget(self.cost); v.addLayout(mrow); v.addWidget(self.explainer)
        self._ok, self._reason = True, ""
        self.seg.changed.connect(self._emit)
        self.slider.valueChanged.connect(self._emit)
        self.set_value(default)

    def _toggle_explainer(self):
        show = not self.explainer.isVisible()
        self.explainer.setVisible(show)
        self.more.setText("Hide explanation" if show else "What is VMAF?")

    def value(self):
        v = self.seg.value()
        return self.slider.value() / 2 if v == "custom" else v

    def effective(self):
        """None when VMAF can't apply (e.g. ProRes)."""
        return self.value() if self._ok else None

    def set_value(self, v) -> None:
        v = float(v) if v else None
        if not self.seg.set_value(v):
            self.seg.set_value("custom")
            self.slider.setValue(int(round(v * 2)))
        self._sync()

    def set_available(self, ok: bool, reason: str = "") -> None:
        self._ok, self._reason = ok, reason
        self.seg.setEnabled(ok); self.slider.setEnabled(ok)
        self._sync()

    def _emit(self, *_):
        self._sync()
        self.changed.emit(self.effective())

    def _sync(self):
        v = self.seg.value()
        cv = self.slider.value() / 2
        self.custom_row.setVisible(v == "custom" and self._ok)
        self.slider_val.setText(f"VMAF {cv:g}")
        self.cost.setVisible(self._ok and v is not None)
        if not self._ok:
            self.desc.setText(self._reason)
        elif v == "custom":
            self.desc.setText(_custom_desc(cv))
        else:
            self.desc.setText(next((d for _l, val, d, _i in VMAF_LEVELS if val == v), ""))


SLOW_LEVELS = [("Off", 1.0, "Real-time speed"), ("2×", 2.0, "Half speed"),
               ("4×", 4.0, "Quarter speed"), ("8×", 8.0, "Super slow motion"),
               ("Custom", "custom", "Any factor from 1× to 16×")]


class SlowmoPicker(QWidget):
    changed = Signal(float)

    def __init__(self, default=2.0):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        self.seg = Segmented(SLOW_LEVELS)
        self.custom = Stepper(1.0, 16.0, 3.0, step=0.25, decimals=2, suffix=" ×")
        v.addWidget(self.seg); v.addWidget(self.custom)
        self.seg.changed.connect(self._on_seg)
        self.custom.valueChanged.connect(lambda _: self.changed.emit(self.value()))
        self.set_value(default)

    def value(self) -> float:
        v = self.seg.value()
        return float(self.custom.value()) if v == "custom" else float(v or 1.0)

    def set_value(self, v) -> None:
        v = float(v)
        if not self.seg.set_value(v):
            self.seg.set_value("custom")
            self.custom.setValue(v)
        self.custom.setVisible(self.seg.value() == "custom")

    def _on_seg(self, val):
        self.custom.setVisible(val == "custom")
        self.changed.emit(self.value())


from ...core.commands import scaled_size  # noqa: E402

SCALE_LEVELS = [("100%", 1.0, "Full resolution"), ("75%", 0.75, "Light reduction"),
                ("50%", 0.5, "Half width and height, e.g. 4K → 1080p"),
                ("Custom", "custom", "Any size from 25% to 95%")]


def _scale_explainer() -> str:
    b = f"color:{theme.TEXT}"
    return (f"<p style='margin:0'><b style='{b}'>Why scale down?</b><br>The AI's work grows with the "
            f"number of pixels. At 50%, width and height are halved, leaving a quarter of the pixels: "
            f"a 4K clip is processed as 1080p and typically finishes several times sooner, with less "
            f"GPU memory and smaller files.</p>"
            f"<p><b style='{b}'>When it makes sense</b><br>Watching on a phone, laptop or 1080p TV, "
            f"sharing online, or testing settings quickly.</p>"
            f"<p style='margin-bottom:0'><b style='{b}'>Trade-off</b><br>The result keeps the lower "
            f"resolution, so fine texture, hair and small text get softer. Keep 100% for archiving and "
            f"big screens. Scaling happens after cropping, before the AI step.</p>")


class ScalePicker(QWidget):
    changed = Signal(float)

    def __init__(self, default: float = 1.0):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        self.seg = Segmented(SCALE_LEVELS)
        self.slider = QSlider(Qt.Horizontal); self.slider.setRange(5, 19); self.slider.setValue(13)
        self.slider_val = QLabel(); self.slider_val.setObjectName("Muted"); self.slider_val.setMinimumWidth(48)
        self.custom_row = QWidget()
        h = QHBoxLayout(self.custom_row); h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.slider, 1); h.addWidget(self.slider_val)
        self.desc = QLabel(); self.desc.setObjectName("Hint"); self.desc.setWordWrap(True)
        self.more = QPushButton("Why scale down?"); self.more.setProperty("ghost", True)
        self.more.setIcon(icon("info", "muted", active="text", checked=None)); self.more.setIconSize(QSize(15, 15))
        self.more.setCursor(Qt.PointingHandCursor); self.more.clicked.connect(self._toggle_explainer)
        mrow = QHBoxLayout(); mrow.setContentsMargins(0, 0, 0, 0); mrow.addWidget(self.more); mrow.addStretch(1)
        self.explainer = QLabel(_scale_explainer()); self.explainer.setWordWrap(True)
        self.explainer.setTextFormat(Qt.RichText)
        self.explainer.setStyleSheet(f"background: {theme.SURFACE2}; border: 1px solid {theme.BORDER}; "
                                     f"border-radius: 10px; padding: 12px; color: {theme.MUTED};")
        self.explainer.hide()
        v.addWidget(self.seg); v.addWidget(self.custom_row); v.addWidget(self.desc)
        v.addLayout(mrow); v.addWidget(self.explainer)
        self._src = None
        self.seg.changed.connect(self._emit); self.slider.valueChanged.connect(self._emit)
        self.set_value(default)

    def _toggle_explainer(self):
        show = not self.explainer.isVisible()
        self.explainer.setVisible(show)
        self.more.setText("Hide explanation" if show else "Why scale down?")

    def value(self) -> float:
        v = self.seg.value()
        return self.slider.value() * 5 / 100 if v == "custom" else float(v or 1.0)

    def set_value(self, v) -> None:
        v = float(v or 1.0)
        if not self.seg.set_value(v):
            self.seg.set_value("custom")
            self.slider.setValue(int(round(v * 20)))
        self._sync()

    def set_source(self, size) -> None:
        if size != self._src:
            self._src = size
            self._sync()

    def _emit(self, *_):
        self._sync()
        self.changed.emit(self.value())

    def _sync(self):
        s = self.value()
        self.custom_row.setVisible(self.seg.value() == "custom")
        self.slider_val.setText(f"{round(s * 100)}%")
        parts = []
        if self._src:
            w, h = self._src
            tw, th = scaled_size(w, h, s)
            parts.append(f"This file: {w}×{h} → {tw}×{th}." if s < 0.999 else f"This file: {w}×{h}, unchanged.")
        parts.append("Full detail, slowest processing." if s >= 0.999 else
                     f"About {1 / (s * s):.1f}× fewer pixels for the AI: much faster, less GPU memory "
                     f"and smaller files, but less fine detail.")
        self.desc.setText(" ".join(parts))


QUALITY_LEVELS = [
    ("Max", -4, "Visually lossless. Large files, for archiving or editing later.", None),
    ("High", 0, "Recommended. Hard to tell apart from the source, at a sensible size.", "star-fill"),
    ("Balanced", 4, "Good quality with noticeably smaller files.", None),
    ("Small", 8, "Smallest files. Softer fine detail; fine for sharing online.", None),
    ("Custom", "custom", "Your own value.", None),
]


class QualityPicker(QWidget):
    """Fixed-quality chips relative to the encoder's default (same style as the VMAF picker)."""
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        self.seg = Segmented([(label, val, "", ic) for label, val, _d, ic in QUALITY_LEVELS])
        self.custom = Stepper(0, 51, 22, decimals=0)
        self.desc = QLabel(); self.desc.setObjectName("Hint"); self.desc.setWordWrap(True)
        v.addWidget(self.seg); v.addWidget(self.custom); v.addWidget(self.desc)
        self.enc, self._ok, self._reason = None, True, ""
        self.seg.changed.connect(self._emit)
        self.custom.valueChanged.connect(self._emit)
        self.seg.set_value(0)
        self._sync()

    def _max(self) -> int:
        return 63 if self.enc and self.enc.codec == "libsvtav1" else 51

    def set_encoder(self, enc) -> None:
        self.enc = enc
        ok = bool(enc and enc.has_quality)
        if ok:
            self.custom.setRange(0, self._max())
            if self.seg.value() != "custom":
                self.custom.setValue(enc.q_default)
        self.set_available(ok, f"{enc.label} uses a fixed quality profile, so there's nothing to tune."
                           if enc else "")

    def set_available(self, ok: bool, reason: str = "") -> None:
        self._ok, self._reason = ok, reason
        self.seg.setEnabled(ok); self.custom.setEnabled(ok)
        self._sync()

    def value(self):
        if not self._ok or not self.enc:
            return None
        v = self.seg.value()
        if v == "custom":
            return int(self.custom.value())
        return max(0, min(self._max(), self.enc.q_default + int(v or 0)))

    def _emit(self, *_):
        self._sync()
        self.changed.emit(self.value())

    def _sync(self):
        v = self.seg.value()
        self.custom.setVisible(v == "custom" and self._ok)
        if not self._ok or not self.enc:
            self.desc.setText(self._reason)
            return
        text = next((d for _l, val, d, _i in QUALITY_LEVELS if val == v), "")
        self.desc.setText(f"{self.enc.q_flag.upper()} {self.value()} · {text} "
                          f"Lower numbers mean better quality and bigger files.")


# ===================== v0.8.2: quality method (VMAF target / constant quality) =====================
_vmaf_init_v81 = VmafPicker.__init__
_vmaf_sync_v81 = VmafPicker._sync


def _vmaf_init_v82(self, default=95.0):
    self.method = None
    self._last = 95.0
    _vmaf_init_v81(self, default)
    self.method = Segmented([
        ("VMAF target", "vmaf", "Ralenti measures the quality and keeps the smallest file that reaches your score"),
        ("Constant quality", "cq", "One fixed encoder setting, no measuring: faster, but file size and quality vary"),
    ])
    self.layout().insertWidget(0, self.method)
    self.seg.group.button(0).hide()     # the old 'Off' chip: replaced by the method switch
    self.method.changed.connect(self._method)
    self._sync()


def _vmaf_method(self, m) -> None:
    if m == "cq":
        if self.seg.value() is not None:
            self._last = self.value()
        self.seg.set_value(None)
    else:
        self.set_value(self._last or 95.0)
    self._emit()


def _vmaf_sync_v82(self) -> None:
    _vmaf_sync_v81(self)
    if getattr(self, "method", None) is None:
        return
    cq = self.seg.value() is None
    self.method.set_value("cq" if cq else "vmaf")
    self.method.setEnabled(self._ok)
    self.seg.setVisible(not cq)
    self.more.setVisible(self._ok and not cq)
    if cq:
        self.explainer.hide()
        if self._ok:
            self.desc.setText("One fixed encoder setting, chosen below. No measuring: faster, but file size "
                              "and quality vary from video to video.")


VmafPicker.__init__ = _vmaf_init_v82
VmafPicker._method = _vmaf_method
VmafPicker._sync = _vmaf_sync_v82
