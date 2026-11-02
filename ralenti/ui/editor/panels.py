"""The six step pages. Each reads from and writes to the shared EditSession. Every page shows its own problems with
fix buttons at the top; Transform and Interpolation are dimmed with a Turn on button while they're off; options that
can't work with the current settings are greyed out with the reason."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)
from shiboken6 import isValid

from ...core.audio_out import CODECS, QUALITIES, auto_codec, bytes_per_second, fits
from ...core.cropdetect import detect_crop
from ...core.edit import VIDEO_CONTAINERS
from ...core.encoders import ENCODERS
from ...core.engines import available_engines, engine_for, engines, resolve_model
from ...core.merge import compat_issues
from ...core.naming import PLACEHOLDERS
from ...core.segments import segments, to_cuts
from ...core.space import BPP
from ...core.transform import output_size
from ..icons import icon
from ..util import fmt_ts, notify
from ..widgets.choice import TAG, NiceCombo, RichCombo
from ..widgets.fields import Field, button
from ..widgets.panel import OutputField
from ..widgets.pickers import QualityPicker, SlowmoPicker, VmafPicker
from ..widgets.segmented import Segmented
from ..widgets.warn import WarnRow
from ..workers import run_async
from .summary import analyse, dur, preview_output
from .widgets import IssueBox

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
    ("copy", "Keep original", "", "Copies the sound untouched. Not possible with slow motion."),
    ("convert", "Convert", "", "Choose the codec and quality yourself."),
    ("remove", "Remove", "", "No sound in the output."),
]
CONTAINERS = [("Auto", "auto", "The best container for the encoder"), ("MP4", "mp4", "Plays everywhere"),
              ("MKV", "mkv", "Holds every codec and several audio tracks"),
              ("MOV", "mov", "For editing apps (ProRes, PCM audio)")]


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


def lock(btn, ok: bool, tip: str, why: str, keep: bool = False) -> None:
    """Greys out an option that can't work, unless it's the one selected (then the conflict explains it)."""
    btn.setEnabled(ok or keep)
    btn.setToolTip(tip if ok else f"Not possible: {why}")


class Panel(QWidget):
    step = ""
    GROUPS = ()          # (label, key, tooltip): shown as tabs above the panel
    OFF_TEXT = ""        # a step that can be off: dimmed with a Turn on button

    def __init__(self, session):
        super().__init__()
        self.s, self._loading, self.groups, self.cur_group = session, False, {}, None
        self.setObjectName("OptionsBody"); self.setAttribute(Qt.WA_StyledBackground)
        self.v = QVBoxLayout(self); self.v.setContentsMargins(18, 12, 18, 16); self.v.setSpacing(14)
        self.issues = IssueBox()
        self.v.addWidget(self.issues)
        self.off = None
        if self.OFF_TEXT:
            self.off = WarnRow(self.OFF_TEXT, "Turn on", lambda: isValid(self.s) and self.s.set(self.step, on=True))
            self.off.setObjectName("OffRow")
            self.v.addWidget(self.off)
            self.off.hide()

    def group(self, key: str) -> QVBoxLayout:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(14)
        self.v.addWidget(w)
        self.groups[key] = w
        return lay

    def show_group(self, key: str) -> None:
        self.cur_group = key
        for k, w in self.groups.items():
            w.setVisible(k == key)

    def tab_dots(self, e) -> dict:
        return {}

    def _set(self, **kw) -> None:
        if not self._loading and isValid(self.s):
            self.s.set(self.step, **kw)

    def load(self, e) -> None:
        self._loading = True
        try:
            self._load(e)
        finally:
            self._loading = False
        if self.off is not None:
            on = bool(getattr(e, self.step).get("on"))
            self.off.setVisible(bool(e.clips) and not on)
            for i in range(self.v.count()):
                w = self.v.itemAt(i).widget()
                if w is not None and w not in (self.issues, self.off):
                    w.setEnabled(on or not e.clips)

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
    GROUPS = (("Crop", "crop", "Crop the picture or remove black bars"),
              ("Rotate & flip", "rotate", "Turn or mirror the picture"),
              ("Size", "size", "Make the output smaller"))
    OFF_TEXT = "Transform is off: the picture is kept as it is."
    draw_toggled = Signal(bool)
    ratio_changed = Signal(object)

    def __init__(self, s):
        super().__init__(s)
        g = self.group("crop")
        self.mode = RichCombo()
        for key, title, tag, blurb in (
                ("auto", "Auto-detect black bars", "Recommended", "Removes letterbox and pillarbox bars."),
                ("none", "Keep full frame", "", "No cropping."),
                ("custom", "Custom", "", "Draw the crop on the video.")):
            self.mode.add(title, key, blurb, tag)
        self.mode.currentIndexChanged.connect(self._mode)
        g.addWidget(Field("Crop", self.mode))
        self.detect = button("Detect bars", self._detect, icon="magic-wand")
        g.addWidget(self._row(self.detect))
        self.ratio = NiceCombo()
        for label, r in RATIOS:
            self.ratio.addItem(label, r)
        self.ratio.currentIndexChanged.connect(lambda *_: self.ratio_changed.emit(self.ratio.currentData()))
        g.addWidget(Field("Crop shape", self.ratio))
        g.addWidget(hint("Draw the crop with Draw crop under the player (D). The picture shows the result live, "
                         "here and on every other step."))
        g = self.group("rotate")
        self.rot = Segmented([("0°", 0, "No rotation"), ("90°", 90, "Clockwise"), ("180°", 180, "Upside down"),
                              ("270°", 270, "Counter-clockwise")])
        self.rot.changed.connect(lambda v: self._set(on=True, rotate=v))
        g.addWidget(Field("Rotate", self.rot, "For phone videos recorded sideways, for example."))
        self.hf, self.vf = QPushButton("Flip horizontally"), QPushButton("Flip vertically")
        for b, key in ((self.hf, "hflip"), (self.vf, "vflip")):
            b.setCheckable(True)
            b.toggled.connect(lambda on, k=key: self._set(on=True, **{k: on}))
        g.addWidget(Field("Flip", self._row(self.hf, self.vf)))
        g.addWidget(hint("Shortcuts: R and Shift+R rotate, H and V flip, B shows the untouched source."))
        g = self.group("size")
        self.size = Segmented(list(SIZES))
        self.size.changed.connect(lambda v: self._set(on=True, scale=v[0], max_h=v[1]))
        g.addWidget(Field("Output size", self.size, "Smaller sizes also make the AI faster."))
        self.result = hint(); self.result.setTextFormat(Qt.RichText)
        self.v.addWidget(self.result); self.v.addStretch(1)
        self.show_group("crop")

    def tab_dots(self, e) -> dict:
        c = e.crop
        on = bool(c.get("on"))
        return {"crop": on and (isinstance(c.get("crop"), list) or c.get("crop") == "auto"),
                "rotate": on and bool(int(c.get("rotate") or 0) % 360 or c.get("hflip") or c.get("vflip")),
                "size": on and (float(c.get("scale") or 1) < 0.999 or bool(c.get("max_h")))}

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
    GROUPS = (("Motion", "motion", "Slow motion, frame rate and scene cuts"),
              ("Engine", "engine", "The AI model and its options"))
    OFF_TEXT = "Interpolation is off: no AI, the video keeps its speed and frame rate."

    def __init__(self, s):
        super().__init__(s)
        g = self.group("motion")
        self.slow = SlowmoPicker(); self.slow.changed.connect(lambda v: self._set(on=True, slowmo=float(v)))
        self.slow_field = Field("Slow motion", self.slow)
        g.addWidget(self.slow_field)
        self.fps = RichCombo()
        for key, (_m, _v, title, blurb) in FPS.items():
            self.fps.add(title, key, blurb)
        self.fps.currentIndexChanged.connect(self._fps)
        g.addWidget(Field("Frame rate", self.fps))
        self.scene = Segmented([("Off", "off", "Interpolate straight through every cut"),
                                ("Normal", "normal", "Detect clear cuts between shots"),
                                ("Sensitive", "sensitive", "Also catch softer cuts")])
        self.scene.changed.connect(lambda v: self._set(on=True, scene=v))
        g.addWidget(Field("Scene cuts", self.scene, "Never blends one shot into the next. Works with every engine."))
        g.addWidget(hint("Preview a few seconds with Preview under the player (P). Previews follow your trim, crop "
                         "and rotation. Compare them in the Previews tab and in the player."))
        g = self.group("engine")
        self.model = RichCombo(); self.model.currentIndexChanged.connect(self._model)
        g.addWidget(Field("AI model", self.model))
        self.engine_warn = WarnRow("No AI engine yet. RIFE is free and open source, and installs in one click.",
                                   "Get RIFE (free)", lambda: self.window().go_downloads())
        g.addWidget(self.engine_warn)
        self.opts = {}
        for eng in engines():
            rows = []
            for opt in eng.options:
                seg = Segmented(list(opt.choices)); seg.set_value(opt.default)
                seg.changed.connect(lambda _v: self._set(on=True, engine_opts=self._engine_opts()))
                f = Field(opt.title, seg, opt.hint)
                g.addWidget(f); rows.append((opt, seg, f))
            self.opts[eng.key] = rows
        self.v.addStretch(1)
        self.show_group("motion")
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
        a, an, tc = e.audio, analyse(self.s), self.s.qm.tc
        mode = a.get("mode") or "auto"
        self.mode.select(mode)
        slow = bool(an) and an["motion"] and an["slow"] != 1
        i = self.mode.findData("copy")
        item = self.mode.model().item(i) if hasattr(self.mode.model(), "item") else None
        if item is not None:
            item.setEnabled(not slow or mode == "copy")
        self.mode.setItemData(i, "Not with slow motion" if slow else "", TAG)
        ext = an["ext"] if an else ".mp4"
        cur = a.get("codec")
        self.codec.clear()
        for key, c in CODECS.items():
            if key == "opus" and not tc.binary_for("libopus") and cur != "opus":
                continue
            ok = fits(key, ext)
            self.codec.add(c["label"], key, c["blurb"], f"Fits {ext}" if ok else f"Not in {ext}")
            if not ok and key != cur:
                it = self.codec.model().item(self.codec.count() - 1) if hasattr(self.codec.model(), "item") else None
                if it is not None:
                    it.setEnabled(False)
        if not self.codec.select(cur):
            self.codec.select(auto_codec(ext, e.encode.get("encoder") or ""))
        self.quality.set_value(a.get("quality") or "high")
        self.slow.set_value(a.get("slow") or "natural")
        convert = mode == "convert"
        self.codec_field.setVisible(convert)
        self.q_field.setVisible(convert and self.codec.currentData() in ("aac", "opus", "pcm"))
        self.slow_field.setVisible(slow and mode != "remove")
        if not an:
            self.plan.setText("Open a video first."); self.warn.hide(); self.size.setText("")
            return
        p = an["aplan"]
        self.plan.setText(f"<b>{p['label']}</b>")
        self.warn.hide()
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
    GROUPS = (("Video", "video", "Copy or re-encode, and the encoder"),
              ("Quality", "quality", "How good the encode looks"))

    def __init__(self, s):
        super().__init__(s)
        g = self.group("video")
        self.video = Segmented([("Re-encode", "encode", "Encode the video with the settings below"),
                                ("Copy", "copy", "No re-encode: instant and lossless")])
        self.video.changed.connect(lambda v: self._set(video=v))
        g.addWidget(Field("Video", self.video))
        self.copy_note = hint()
        g.addWidget(self.copy_note)
        self.encoder = RichCombo()
        self.encoder.currentIndexChanged.connect(
            lambda *_: self.encoder.currentData() and self._set(encoder=self.encoder.currentData()))
        self.enc_field = Field("Encoder", self.encoder)
        g.addWidget(self.enc_field)
        g = self.group("quality")
        self.q_box = QWidget(); qv = QVBoxLayout(self.q_box); qv.setContentsMargins(0, 0, 0, 0); qv.setSpacing(14)
        self.vmaf = VmafPicker(); self.vmaf.changed.connect(lambda _v: self._set(vmaf=self.vmaf.value()))
        qv.addWidget(Field("Quality", self.vmaf))
        self.quality = QualityPicker(); self.quality.changed.connect(lambda v: self._set(q=v))
        self.q_field = Field("Constant quality level", self.quality)
        qv.addWidget(self.q_field)
        g.addWidget(self.q_box)
        self.q_note = hint("Copy keeps the original picture, so there's no quality to set.")
        g.addWidget(self.q_note)
        self.v.addWidget(hint("Estimates, a comparison of the encoders and a quality test are in the workspace next "
                              "to this panel."))
        self.v.addStretch(1)
        self.show_group("video")
        self.reload_options()

    def tab_dots(self, e) -> dict:
        return {"video": e.encode.get("video") == "copy"}

    def reload_options(self) -> None:
        tc = self.s.qm.tc
        self._loading = True
        try:
            self.encoder.clear()
            for key, x in ENCODERS.items():
                if not x.hidden and tc.binary_for(x.codec):
                    self.encoder.add(x.label, key, x.blurb, x.tag)
        finally:
            self._loading = False
        self.load(self.s.edit)

    def _load(self, e) -> None:
        v, tc, an = e.encode, self.s.qm.tc, analyse(self.s)
        chosen = v.get("video") or "encode"
        self.video.set_value(chosen)
        why = []
        if e.motion.get("on"):
            why.append("Interpolation is on")
        if an and an["filters"] and e.crop.get("on"):
            why.append("Transform changes the picture")
        infos = [self.s.info(c) for c in e.clips] if e.clips else []
        if len(infos) > 1 and compat_issues(infos):
            why.append("the clips differ")
        lock(self.video.group.button(1), not why, "No re-encode: instant and lossless", " and ".join(why) + ".",
             keep=chosen == "copy")
        copying = chosen == "copy" and not why
        if copying:
            self.copy_note.setText("The video is copied as is: instant and lossless. Only Trim and Audio apply.")
        elif why and chosen != "copy":
            self.copy_note.setText("Copy isn't available: " + " and ".join(why) + ".")
        else:
            self.copy_note.setText("")
        self.copy_note.setVisible(bool(self.copy_note.text()))
        self.enc_field.setVisible(not copying)
        self.q_box.setVisible(not copying)
        self.q_note.setVisible(copying)
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
        self._auto = ""
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
        self.v.addWidget(Field("File name", name_box, "Click a placeholder to insert it. In an empty field, it "
                                                       "starts from the automatic name. The file is never "
                                                       "overwritten unless you choose so below."))
        self.container = Segmented(CONTAINERS)
        self.container.changed.connect(lambda v: self._set(container=v))
        self.v.addWidget(Field("Container", self.container))
        self.exists = Segmented([("Add a number", "number", "Never replaces a file: adds (1), (2)…"),
                                 ("Overwrite", "overwrite", "Replaces a file with the same name")])
        self.exists.changed.connect(lambda v: self._set(exists=v))
        self.v.addWidget(Field("If the file exists", self.exists))
        self.v.addWidget(hint("The folder view next to this panel shows where the file goes. Double-click a folder "
                              "there to save in it."))
        self.v.addStretch(1)

    def _insert(self, k: str) -> None:
        t = self.name.text() or self._auto
        pos = self.name.cursorPosition() if self.name.text() else len(t)
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
        cur = o.get("container") or "auto"
        enc = an["enc"] if an else None
        for i, (_label, val, tip) in enumerate(CONTAINERS):
            ok = val == "auto" or not an or an["copy"] or (enc is not None and val in VIDEO_CONTAINERS.get(enc.key,
                                                                                                         {"mkv"}))
            lock(self.container.group.button(i), ok, tip,
                 f"{enc.label if enc else 'This encoder'} can't be stored in .{val}.", keep=cur == val)
        if an:
            bits = ["{name}", "ralenti"]
            if an["motion"]:
                bits.append("{model}")
            if an["slow"] != 1:
                bits.append("slow{slowmo}x")
            if an["vmaf"]:
                bits.append("vmaf{vmaf}")
            self._auto = "_".join(bits)
            self.name.setPlaceholderText("Automatic: " + self._auto)
            p = preview_output(self.s, an)
            self.preview.setText(f"{p.name}\n{p.parent}")
        else:
            self._auto = ""
            self.name.setPlaceholderText("Automatic: source name + settings")
            self.preview.setText("Open a video to see the file name.")
