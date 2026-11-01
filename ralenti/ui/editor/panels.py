"""The six step pages. Each reads from and writes to the shared EditSession."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)
from shiboken6 import isValid

from ...core.audio_out import CODECS, QUALITIES, auto_codec, bytes_per_second, fits
from ...core.cropdetect import detect_crop
from ...core.encoders import ENCODERS
from ...core.engines import available_engines, engine_for, engines, resolve_model
from ...core.naming import PLACEHOLDERS
from ...core.segments import segments, to_cuts
from ...core.space import BPP
from ...core.transform import output_size
from ..icons import icon
from ..util import fmt_ts, notify
from ..widgets.choice import NiceCombo, RichCombo
from ..widgets.fields import Field, button
from ..widgets.panel import OutputField
from ..widgets.pickers import QualityPicker, SlowmoPicker, VmafPicker
from ..widgets.segmented import Segmented
from ..widgets.warn import WarnRow
from ..workers import run_async
from .summary import analyse, dur, preview_output

FPS = {
    "keep": ("keep", 0.0, "Keep source frame rate", "All new frames go into slow motion. The natural choice with slow-mo."),
    "x2": ("multiply", 2.0, "Double (2×)", "Smoother motion at the same speed: 29.97 → 59.94, 24 → 48."),
    "x2.5": ("multiply", 2.5, "Film to 60 (2.5×)", "23.976 → 59.94 without judder."),
    "x4": ("multiply", 4.0, "Quadruple (4×)", "For 120–144 Hz displays: 30 → 120."),
    "f60": ("fixed", 60.0, "Exactly 60 fps", "Forces 60.000. For 29.97 sources, Double gives evener frames."),
    "f120": ("fixed", 120.0, "Exactly 120 fps", "Forces 120.000 fps."),
}
RATIOS = [("Free", None), ("Same as source", "source"), ("16:9", 16 / 9), ("4:3", 4 / 3), ("1:1", 1.0),
          ("2.39:1 (cinema)", 2.39), ("1.85:1", 1.85), ("9:16 (vertical)", 9 / 16)]
SIZES = [("Original", (1.0, 0), "Keep the size"), ("2160p", (1.0, 2160), "Fit within 4K height"),
         ("1440p", (1.0, 1440), ""), ("1080p", (1.0, 1080), ""), ("720p", (1.0, 720), ""),
         ("50%", (0.5, 0), "Half width and height: about 4× faster AI")]
AUDIO_MODES = [
    ("auto", "Auto", "Recommended", "Copies the original when nothing changes it; otherwise converts it to a codec "
                                    "that suits the output. Audio is a tiny part of the file, so quality comes first."),
    ("copy", "Keep original", "", "Copies the sound untouched whenever possible (not with slow motion)."),
    ("convert", "Convert", "", "Choose the codec and quality yourself."),
    ("remove", "Remove", "", "No sound in the output."),
]


def apply_q(qp, enc, q) -> None:
    if q is None or not enc or not enc.has_quality:
        return
    d = int(q) - enc.q_default
    if d in (-4, 0, 4, 8):
        qp.seg.set_value(d)
    else:
        qp.seg.set_value("custom")
        qp.custom.setValue(q)
    qp._sync()


def hint(text: str = "") -> QLabel:
    lb = QLabel(text); lb.setObjectName("Hint"); lb.setWordWrap(True)
    return lb


class Panel(QWidget):
    step = ""

    def __init__(self, session):
        super().__init__()
        self.s, self._loading = session, False
        self.setObjectName("OptionsBody"); self.setAttribute(Qt.WA_StyledBackground)
        self.v = QVBoxLayout(self); self.v.setContentsMargins(18, 12, 18, 16); self.v.setSpacing(14)

    def _set(self, **kw) -> None:
        if not self._loading and isValid(self.s):
            self.s.set(self.step, **kw)

    def load(self, e) -> None:
        self._loading = True
        try:
            self._load(e)
        finally:
            self._loading = False

    def _load(self, e) -> None:
        pass

    def reload_options(self) -> None:
        pass

    def _row(self, *ws) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        for x in ws:
            h.addWidget(x, 1 if isinstance(x, QLineEdit) else 0)
        return w


# ============================== Trim ==============================
class TrimPanel(Panel):
    step = "trim"
    add_cut = Signal()
    goto = Signal(float)

    def __init__(self, s):
        super().__init__(s)
        self.v.addWidget(hint("Split the video with cuts, then choose which segments to keep. Kept segments are "
                              "joined in order. The tools under the player add cuts (C), jump between them (, and .) "
                              "and keep or remove the segment under the playhead (X). On the bar: click a segment, "
                              "drag a cut, right-click a cut to delete it."))
        self.rows_box = QWidget()
        self.rows = QVBoxLayout(self.rows_box); self.rows.setContentsMargins(0, 0, 0, 0); self.rows.setSpacing(6)
        self.v.addWidget(Field("Segments", self.rows_box))
        self.v.addWidget(self._row(button("Keep all", self._keep_all, "ghost", icon="check"),
                                   button("Remove all cuts", self._clear, "ghost", icon="eraser")))
        self.info = hint()
        self.v.addWidget(self.info)
        self.v.addStretch(1)

    def _trim(self, tr: dict) -> None:
        tr = dict(tr)
        if not tr.get("cuts") and not tr.get("drop"):
            tr["on"] = False
        self.s.set("trim", on=bool(tr.get("on", True)), cuts=[float(c) for c in tr.get("cuts") or []],
                   drop=[int(d) for d in tr.get("drop") or []], start=None, end=None)

    def _keep_all(self) -> None:
        m = self.s.media()
        if m:
            tr = to_cuts(self.s.edit.trim, m.duration)
            tr["drop"] = []
            self._trim(tr)

    def _clear(self) -> None:
        self._trim({"on": False, "cuts": [], "drop": []})

    def _toggle(self, i: int, keep: bool) -> None:
        m = self.s.media()
        if self._loading or not m:
            return
        tr = to_cuts(self.s.edit.trim, m.duration)
        tr["on"] = True
        drop = {int(d) for d in tr.get("drop") or []}
        (drop.discard if keep else drop.add)(i)
        tr["drop"] = sorted(drop)
        self._trim(tr)

    def _load(self, e) -> None:
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        m = self.s.media()
        if m is None:
            self.info.setText("Open a video first.")
            return
        tr = to_cuts(e.trim, m.duration) if e.trim.get("on") else {"cuts": [], "drop": []}
        segs = segments(tr, m.duration)
        for i, (a, b, keep) in enumerate(segs):
            f = QFrame(); f.setObjectName("SegRow"); f.setProperty("removed", not keep)
            h = QHBoxLayout(f); h.setContentsMargins(10, 4, 4, 4)
            cb = QCheckBox(f"{i + 1}  ·  {fmt_ts(a, False)} → {fmt_ts(b, False)}  ·  {dur(b - a)}")
            cb.setChecked(keep)
            cb.toggled.connect(lambda on, i=i: self._toggle(i, on))
            go = QPushButton(); go.setProperty("ghost", True); go.setToolTip("Go to this segment")
            go.setIcon(icon("play-fill", "muted", active="text", checked=None)); go.setCursor(Qt.PointingHandCursor)
            go.clicked.connect(lambda _=False, a=a: self.goto.emit(a + 0.01))
            h.addWidget(cb, 1); h.addWidget(go)
            self.rows.addWidget(f)
        kept = sum(b - a for a, b, k in segs if k)
        n = sum(1 for *_x, k in segs if k)
        text = (f"Keeps {dur(kept)} of {dur(m.duration)} in {n} segment{'s' if n != 1 else ''}."
                if e.trim.get("on") else "The whole video is kept. Add cuts to split it.")
        if len(e.clips) > 1:
            text += " With several clips, times count on the joined video."
        if e.encode.get("video") == "copy":
            text += " With Encode › Copy, segments start on keyframes: keep snapping on for exact cuts."
        self.info.setText(text)


# ============================== Transform ==============================
class CropPanel(Panel):
    step = "crop"
    draw_toggled = Signal(bool)
    ratio_changed = Signal(object)

    def __init__(self, s):
        super().__init__(s)
        self.mode = RichCombo()
        for key, title, tag, blurb in (
                ("auto", "Auto-detect black bars", "Recommended", "Removes letterbox and pillarbox bars."),
                ("none", "Keep full frame", "", "No cropping."),
                ("custom", "Custom", "", "Draw the crop on the video.")):
            self.mode.add(title, key, blurb, tag)
        self.mode.currentIndexChanged.connect(self._mode)
        self.v.addWidget(Field("Crop", self.mode))
        self.detect = button("Detect bars", self._detect, icon="magic-wand")
        self.v.addWidget(self._row(self.detect))
        self.v.addWidget(hint("Rotate, flip and draw the crop with the tools under the player. The picture shows "
                              "the result live, here and on every step after this one."))
        self.ratio = NiceCombo()
        for label, r in RATIOS:
            self.ratio.addItem(label, r)
        self.ratio.currentIndexChanged.connect(lambda *_: self.ratio_changed.emit(self.ratio.currentData()))
        self.v.addWidget(Field("Crop shape", self.ratio))
        self.rot = Segmented([("0°", 0, "No rotation"), ("90°", 90, "Clockwise"), ("180°", 180, "Upside down"),
                              ("270°", 270, "Counter-clockwise")])
        self.rot.changed.connect(lambda v: self._set(on=True, rotate=v))
        self.v.addWidget(Field("Rotate", self.rot, "For phone videos recorded sideways, for example."))
        self.hf, self.vf = QPushButton("Flip horizontally"), QPushButton("Flip vertically")
        for b, key in ((self.hf, "hflip"), (self.vf, "vflip")):
            b.setCheckable(True)
            b.toggled.connect(lambda on, k=key: self._set(on=True, **{k: on}))
        self.v.addWidget(Field("Flip", self._row(self.hf, self.vf)))
        self.size = Segmented(list(SIZES))
        self.size.changed.connect(lambda v: self._set(on=True, scale=v[0], max_h=v[1]))
        self.v.addWidget(Field("Output size", self.size, "Smaller sizes also make the AI faster."))
        self.result = hint(); self.result.setTextFormat(Qt.RichText)
        self.v.addWidget(self.result); self.v.addStretch(1)

    def _mode(self, *_) -> None:
        m = self.mode.currentData()
        if m == "custom":
            if not isinstance(self.s.edit.crop.get("crop"), list):
                self.draw_toggled.emit(True)
            return
        self._set(on=True, crop=m)

    def _detect(self) -> None:
        tc, info = self.s.qm.tc, self.s.info()
        if not info or not tc.has_cropdetect:
            return
        self.detect.setEnabled(False); self.detect.setText("Scanning…")

        def done(c):
            self.detect.setEnabled(True); self.detect.setText("Detect bars")
            self.s.set("crop", on=True, crop=[c.w, c.h, c.x, c.y] if c else "none")
            notify(self, f"Found bars · cropping to {c.w}×{c.h}" if c else "No black bars found", "ok" if c else "info")

        def fail(msg):
            self.detect.setEnabled(True); self.detect.setText("Detect bars")
            notify(self, msg, "error")

        run_async(lambda: detect_crop(tc.ffmpeg, info), done, fail)

    def _load(self, e) -> None:
        c, info = e.crop, self.s.media()
        crop = c.get("crop")
        self.mode.select("custom" if isinstance(crop, list) else (crop or "none"))
        self.rot.set_value(int(c.get("rotate") or 0))
        self.hf.setChecked(bool(c.get("hflip"))); self.vf.setChecked(bool(c.get("vflip")))
        self.size.set_value((float(c.get("scale") or 1.0), int(c.get("max_h") or 0))) or self.size.set_value((1.0, 0))
        self.detect.setEnabled(info is not None and self.s.qm.tc.has_cropdetect)
        if info is None:
            self.result.setText("Open a video first.")
            return
        on = c.get("on")
        bw, bh = (crop[0], crop[1]) if isinstance(crop, list) and on else (info.width, info.height)
        w, h = output_size(bw, bh, c.get("rotate") if on else 0, c.get("scale") if on else 1.0,
                           c.get("max_h") if on else 0)
        shape = "portrait" if h > w else ("square" if h == w else "landscape")
        note = " (before removing any black bars)" if on and crop == "auto" else ""
        self.result.setText(f"Output: <b>{w}×{h}</b>, {shape}{note}.")


# ============================== Interpolation ==============================
class MotionPanel(Panel):
    step = "motion"

    def __init__(self, s):
        super().__init__(s)
        self.model = RichCombo(); self.model.currentIndexChanged.connect(self._model)
        self.v.addWidget(Field("AI model", self.model))
        self.engine_warn = WarnRow("No AI engine yet. RIFE is free and open source, and installs in one click.",
                                   "Get RIFE (free)", lambda: self.window().go_downloads())
        self.v.addWidget(self.engine_warn)
        self.slow = SlowmoPicker(); self.slow.changed.connect(lambda v: self._set(on=True, slowmo=float(v)))
        self.slow_field = Field("Slow motion", self.slow)
        self.v.addWidget(self.slow_field)
        self.fps = RichCombo()
        for key, (_m, _v, title, blurb) in FPS.items():
            self.fps.add(title, key, blurb)
        self.fps.currentIndexChanged.connect(self._fps)
        self.v.addWidget(Field("Frame rate", self.fps))
        self.scene = Segmented([("Off", "off", "Interpolate straight through every cut"),
                                ("Normal", "normal", "Detect clear cuts between shots"),
                                ("Sensitive", "sensitive", "Also catch softer cuts")])
        self.scene.changed.connect(lambda v: self._set(on=True, scene=v))
        self.v.addWidget(Field("Scene cuts", self.scene, "Never blends one shot into the next. Works with every engine."))
        self.opts = {}
        for eng in engines():
            rows = []
            for opt in eng.options:
                seg = Segmented(list(opt.choices)); seg.set_value(opt.default)
                seg.changed.connect(lambda _v: self._set(on=True, engine_opts=self._engine_opts()))
                f = Field(opt.title, seg, opt.hint)
                self.v.addWidget(f); rows.append((opt, seg, f))
            self.opts[eng.key] = rows
        self.v.addWidget(hint("Preview a few seconds with Preview under the player (P). Previews follow your "
                              "trim, crop and rotation. Compare them in the Previews tab and in the player."))
        self.v.addStretch(1)
        self.reload_options()

    def reload_options(self) -> None:
        tc = self.s.qm.tc
        self._loading = True
        try:
            self.model.clear()
            found = False
            for eng in available_engines(tc):
                ms = eng.models(tc)
                if ms:
                    self.model.add_header(eng.group or eng.name)
                    for m in ms:
                        self.model.add(m.label, m.code, m.blurb, m.tag)
                        found = True
            if not found:
                self.model.add("No AI engine installed", None, "Install RIFE (free) from Downloads.")
            self.engine_warn.setVisible(not found)
        finally:
            self._loading = False
        self.load(self.s.edit)

    def _engine_opts(self) -> dict:
        eng = engine_for(self.model.currentData())
        return {o.key: seg.value() for o, seg, _f in self.opts.get(eng.key, [])} if eng else {}

    def _model(self, *_) -> None:
        self._sync_opts()
        if self.model.currentData():
            self._set(on=True, model=self.model.currentData(), engine_opts=self._engine_opts())

    def _fps(self, *_) -> None:
        mode, val, *_r = FPS.get(self.fps.currentData() or "keep", FPS["keep"])
        self._set(on=True, fps_mode=mode, fps_value=val)

    def _sync_opts(self) -> None:
        eng = engine_for(self.model.currentData())
        for key, rows in self.opts.items():
            for _o, _s, f in rows:
                f.setVisible(bool(eng) and key == eng.key)

    def _load(self, e) -> None:
        m, tc = e.motion, self.s.qm.tc
        code = resolve_model(tc, m.get("model"))
        if not (code and self.model.select(code)):
            self.model.setCurrentIndex(max(0, self.model.count() - 1))
        self._sync_opts()
        if eng := engine_for(self.model.currentData()):
            vals = {**eng.defaults(), **(m.get("engine_opts") or {})}
            for o, seg, _f in self.opts.get(eng.key, []):
                seg.set_value(vals.get(o.key, o.default)) or seg.set_value(o.default)
        self.slow.set_value(float(m.get("slowmo") or 1))
        key = next((k for k, (mo, va, *_r) in FPS.items() if mo == (m.get("fps_mode") or "keep")
                    and (mo == "keep" or abs(va - float(m.get("fps_value") or 0)) < 1e-6)), "keep")
        self.fps.select(key)
        self.scene.set_value(m.get("scene") or "normal")
        an = analyse(self.s)
        sl = float(m.get("slowmo") or 1)
        if an:
            self.slow_field.set_hint(f"{sl:g}× turns this {dur(an['dur'])} video into {dur(an['dur'] * sl)}."
                                     if sl != 1 else f"Real-time: the video stays {dur(an['dur'])} long.")
        else:
            self.slow_field.set_hint("How much slower the video plays. 2× turns 10 s into 20 s.")


# ============================== Audio ==============================
class AudioPanel(Panel):
    step = "audio"

    def __init__(self, s):
        super().__init__(s)
        self.mode = RichCombo()
        for key, title, tag, blurb in AUDIO_MODES:
            self.mode.add(title, key, blurb, tag)
        self.mode.currentIndexChanged.connect(lambda *_: self._set(mode=self.mode.currentData()))
        self.v.addWidget(Field("Sound", self.mode))
        self.codec = RichCombo()
        self.codec.currentIndexChanged.connect(
            lambda *_: self.codec.currentData() and self._set(mode="convert", codec=self.codec.currentData()))
        self.codec_field = Field("Codec", self.codec)
        self.quality = Segmented([(v, k, "") for k, v in QUALITIES.items()])
        self.quality.changed.connect(lambda v: self._set(mode="convert", quality=v))
        self.q_field = Field("Quality", self.quality, "Transparent: indistinguishable from the original. "
                                                      "High: recommended. Small: still good, for speech.")
        self.slow = Segmented([("Natural pitch", "natural", "Slower and deeper, like slowed-down film"),
                               ("Keep pitch", "keep_pitch", "Voices keep their normal pitch")])
        self.slow.changed.connect(lambda v: self._set(slow=v))
        self.slow_field = Field("Slow motion", self.slow, "How the sound follows the slow-motion video.")
        for w in (self.codec_field, self.q_field, self.slow_field):
            self.v.addWidget(w)
        self.plan = QLabel(); self.plan.setWordWrap(True); self.plan.setTextFormat(Qt.RichText)
        self.warn = WarnRow("")
        self.size = hint()
        self.v.addWidget(Field("Result", self.plan)); self.v.addWidget(self.warn); self.v.addWidget(self.size)
        self.v.addStretch(1)

    def _load(self, e) -> None:
        a, an = e.audio, analyse(self.s)
        self.mode.select(a.get("mode") or "auto")
        ext = an["ext"] if an else ".mp4"
        self.codec.clear()
        for key, c in CODECS.items():
            self.codec.add(c["label"], key, c["blurb"], f"Fits {ext}" if fits(key, ext) else f"Not in {ext}")
        if not self.codec.select(a.get("codec")):
            self.codec.select(auto_codec(ext, e.encode.get("encoder") or ""))
        self.quality.set_value(a.get("quality") or "high")
        self.slow.set_value(a.get("slow") or "natural")
        convert = a.get("mode") == "convert"
        self.codec_field.setVisible(convert)
        self.q_field.setVisible(convert and self.codec.currentData() in ("aac", "opus", "pcm"))
        self.slow_field.setVisible(bool(an) and an["motion"] and an["slow"] != 1 and a.get("mode") != "remove")
        if not an:
            self.plan.setText("Open a video first."); self.warn.hide(); self.size.setText("")
            return
        p = an["aplan"]
        self.plan.setText(f"<b>{p['label']}</b>")
        self.warn.label.setText(p.get("warning") or ""); self.warn.setVisible(bool(p.get("warning")))
        bps, enc = bytes_per_second(p, an["info"]), an["enc"]
        if bps is None or an["copy"] or not enc:
            self.size.setText("Audio is a tiny part of a video file, so it's kept at high quality.")
            return
        w, h = an["size"]
        video = w * h * float(an["fps"] or 30) * an["out_dur"] * BPP.get(enc.key, 0.03)
        audio = bps * an["out_dur"]
        share = audio / (audio + video) * 100 if audio + video else 0
        self.size.setText(f"Audio ≈ {audio / 1e6:.1f} MB of about {(audio + video) / 1e6:.0f} MB ({share:.1f}%). "
                          "It barely affects the file size, so quality comes first.")


# ============================== Encode ==============================
class EncodePanel(Panel):
    step = "encode"

    def __init__(self, s):
        super().__init__(s)
        self.video = Segmented([("Re-encode", "encode", "Encode the video with the settings below"),
                                ("Copy", "copy", "No re-encode: instant and lossless, when nothing changes the picture")])
        self.video.changed.connect(lambda v: self._set(video=v))
        self.v.addWidget(Field("Video", self.video))
        self.copy_note = hint()
        self.v.addWidget(self.copy_note)
        self.enc_box = QWidget(); ev = QVBoxLayout(self.enc_box); ev.setContentsMargins(0, 0, 0, 0); ev.setSpacing(14)
        self.encoder = RichCombo()
        self.encoder.currentIndexChanged.connect(
            lambda *_: self.encoder.currentData() and self._set(encoder=self.encoder.currentData()))
        ev.addWidget(Field("Encoder", self.encoder))
        self.vmaf = VmafPicker(); self.vmaf.changed.connect(lambda _v: self._set(vmaf=self.vmaf.value()))
        ev.addWidget(Field("Quality", self.vmaf))
        self.quality = QualityPicker(); self.quality.changed.connect(lambda v: self._set(q=v))
        self.q_field = Field("Constant quality level", self.quality)
        ev.addWidget(self.q_field)
        self.v.addWidget(self.enc_box)
        self.v.addWidget(hint("A quality test with a side-by-side still comes to this page in a later update."))
        self.v.addStretch(1)
        self.reload_options()

    def reload_options(self) -> None:
        tc = self.s.qm.tc
        self._loading = True
        try:
            self.encoder.clear()
            for key, e in ENCODERS.items():
                if not e.hidden and tc.binary_for(e.codec):
                    self.encoder.add(e.label, key, e.blurb, e.tag)
        finally:
            self._loading = False
        self.load(self.s.edit)

    def _load(self, e) -> None:
        v, tc, an = e.encode, self.s.qm.tc, analyse(self.s)
        self.video.set_value(v.get("video") or "encode")
        possible = not e.motion.get("on") and not (an and an["filters"] and e.crop.get("on"))
        copying = v.get("video") == "copy" and possible
        if v.get("video") == "copy" and not possible:
            self.copy_note.setText("Copy isn't possible with Interpolation or Transform on: the video is re-encoded "
                                   "with the encoder below.")
        elif copying:
            self.copy_note.setText("The video is copied as is: instant and lossless. Only Trim and Audio apply.")
        else:
            self.copy_note.setText("")
        self.copy_note.setVisible(bool(self.copy_note.text()))
        self.enc_box.setVisible(not copying)
        if not self.encoder.select(v.get("encoder")):
            self.encoder.setCurrentIndex(0)
        enc = ENCODERS.get(self.encoder.currentData() or "")
        self.quality.set_encoder(enc)
        if enc and not enc.has_quality:
            self.vmaf.set_available(False, f"{enc.label} uses a fixed quality profile.")
        elif not tc.has_vmaf:
            self.vmaf.set_available(False, "VMAF needs the full FFmpeg build (see Downloads).")
        else:
            self.vmaf.set_available(True)
        self.vmaf.set_value(v.get("vmaf") or None)
        apply_q(self.quality, enc, v.get("q"))
        self.q_field.setVisible(bool(enc and enc.has_quality) and self.vmaf.effective() is None)


# ============================== Output ==============================
class OutputPanel(Panel):
    step = "output"

    def __init__(self, s):
        super().__init__(s)
        self.folder = OutputField("dir"); self.folder.changed.connect(lambda t: self._set(out_dir=t.strip()))
        self.v.addWidget(Field("Save to", self.folder, "Empty: next to the source file."))
        self.name = QLineEdit(); self.name.setPlaceholderText("Automatic: source name + settings")
        self.name.editingFinished.connect(lambda: self._set(name=self.name.text().strip()))
        chips = QWidget(); g = QGridLayout(chips); g.setContentsMargins(0, 0, 0, 0); g.setSpacing(4)
        for i, (k, desc) in enumerate(PLACEHOLDERS.items()):
            b = QPushButton("{" + k + "}"); b.setObjectName("Chip"); b.setToolTip(desc)
            b.setCursor(Qt.PointingHandCursor); b.clicked.connect(lambda _=False, k=k: self._insert(k))
            g.addWidget(b, i // 5, i % 5)
        self.preview = QLabel(); self.preview.setObjectName("NamePreview"); self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        name_box = QWidget(); nv = QVBoxLayout(name_box); nv.setContentsMargins(0, 0, 0, 0); nv.setSpacing(8)
        nv.addWidget(self.name); nv.addWidget(chips); nv.addWidget(self.preview)
        nv.addWidget(button("Use as default for new videos", self._default, "ghost", icon="star"))
        self.v.addWidget(Field("File name", name_box, "Click a placeholder to insert it. The file is never "
                                                       "overwritten unless you choose so below."))
        self.container = Segmented([("Auto", "auto", "The best container for the encoder"),
                                    ("MP4", "mp4", "Plays everywhere"),
                                    ("MKV", "mkv", "Holds every codec and several audio tracks"),
                                    ("MOV", "mov", "For editing apps (ProRes, PCM audio)")])
        self.container.changed.connect(lambda v: self._set(container=v))
        self.cont_note = hint()
        self.v.addWidget(Field("Container", self.container)); self.v.addWidget(self.cont_note)
        self.exists = Segmented([("Add a number", "number", "Never replaces a file: adds (1), (2)…"),
                                 ("Overwrite", "overwrite", "Replaces a file with the same name")])
        self.exists.changed.connect(lambda v: self._set(exists=v))
        self.v.addWidget(Field("If the file exists", self.exists))
        self.v.addWidget(hint("A view of the destination folder comes to this page in a later update."))
        self.v.addStretch(1)

    def _insert(self, k: str) -> None:
        pos, t = self.name.cursorPosition(), self.name.text()
        new = t[:pos] + "{" + k + "}" + t[pos:]
        self.name.setText(new)
        self.name.setCursorPosition(pos + len(k) + 2)
        self.name.setFocus()
        self._set(name=new.strip())

    def _default(self) -> None:
        st = self.s.qm.settings
        st.name_template = self.name.text().strip()
        st.save()
        notify(self, "New videos will use this file name" if st.name_template else
               "New videos will use the automatic file name", "ok")

    def _load(self, e) -> None:
        o = e.output
        if self.folder.text() != (o.get("out_dir") or ""):
            self.folder.setText(o.get("out_dir") or "")
        if self.name.text() != (o.get("name") or ""):
            self.name.setText(o.get("name") or "")
        self.container.set_value(o.get("container") or "auto")
        self.exists.set_value(o.get("exists") or "number")
        an = analyse(self.s)
        if an:
            p = preview_output(self.s, an)
            self.preview.setText(f"{p.name}\n{p.parent}")
            self.cont_note.setText(an.get("ext_warn") or "")
        else:
            self.preview.setText("Open a video to see the file name.")
            self.cont_note.setText("")
        self.cont_note.setVisible(bool(self.cont_note.text()))
