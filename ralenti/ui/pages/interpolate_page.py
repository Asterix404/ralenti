"""Interpolate: one video at a time. Engine-agnostic: models and engine options come from the
registered engines (RIFE built in, more through extensions)."""
import json
import os
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
                               QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from ...core import pipeline, presets
from ...core.commands import scaled_size
from ...core.cropdetect import detect_crop
from ...core.encoders import ENCODERS
from ...core.engines import available_engines, engine_for, engines, resolve_model
from ...core.fps import target_fps
from ...core.jobs import Status
from ...core.probe import probe
from .. import theme
from ..bridge import QueueBridge
from ..icons import icon, pixmap
from ..space_check import confirm_space
from ..util import notify
from ..widgets.choice import NiceCombo, RichCombo
from ..widgets.drop_zone import FILTER, paths_from_mime
from ..widgets.fields import Field, button, page_header
from ..widgets.panel import OutputField, Section, SummaryPanel, options_scroll
from ..widgets.pickers import QualityPicker, ScalePicker, SlowmoPicker, VmafPicker
from ..widgets.player import PlayerPanel
from ..widgets.segmented import Segmented
from ..widgets.takes import TakePanel
from ..widgets.warn import WarnRow
from ..workers import run_async

FPS = {
    "keep": ("keep", 0.0, "Keep source frame rate",
             "All new frames go into slow motion. The natural choice when slow-mo is on."),
    "x2": ("multiply", 2.0, "Double (2×)", "Smoother motion at the same speed: 29.97 → 59.94, 24 → 48."),
    "x2.5": ("multiply", 2.5, "Film to 60 (2.5×)", "23.976 → 59.94 without judder."),
    "x4": ("multiply", 4.0, "Quadruple (4×)", "For 120–144 Hz displays: 30 → 120."),
    "f60": ("fixed", 60.0, "Exactly 60 fps", "Forces 60.000. For 29.97 sources, Double gives evener frames."),
    "f120": ("fixed", 120.0, "Exactly 120 fps", "Forces 120.000 fps."),
}
AUDIO = [
    ("natural", "Slow down · natural pitch", "Recommended",
     "Audio slows with the picture and drops in pitch, like slowed-down film. Sounds cinematic."),
    ("keep_pitch", "Slow down · keep pitch", "", "Voices keep their normal pitch (Premiere's “Maintain pitch”). {engine}"),
    ("copy", "Original audio", "", "Untouched copy. With slow motion it ends early and drifts out of sync."),
    ("drop", "No audio", "", "Silent output."),
]
CROP_DEFAULTS = [
    ("auto", "Auto-detect black bars", "Recommended",
     "Scans the clip and removes letterbox/pillarbox bars. Fewer pixels also means faster processing."),
    ("none", "Keep full frame", "", "No cropping unless you draw one."),
]
RATIOS = [("Free", None), ("Same as source", "source"), ("16:9", 16 / 9), ("4:3", 4 / 3), ("1:1", 1.0),
          ("2.39:1 (cinema)", 2.39), ("1.85:1", 1.85), ("9:16 (vertical)", 9 / 16)]
AUDIO_SUMMARY = {"natural": "Slowed · natural pitch", "keep_pitch": "Slowed · pitch kept", "copy": "Original",
                 "drop": "None (silent)"}
AUDIO_NAMES = {"copy": "Original audio", "natural": "Natural pitch", "keep_pitch": "Pitch kept", "drop": "Silent"}
SIG_KEYS = ("model", "fps_mode", "fps_value", "slowmo", "audio", "scale", "engine_opts")


def _dur(s: float) -> str:
    if s < 60:
        return f"{s:.1f}".rstrip("0").rstrip(".") + " s"
    m, sec = divmod(int(round(s)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _clock(t: float) -> str:
    m, s = divmod(int(max(0.0, t)), 60)
    return f"{m}:{s:02d}"


def _covers(job, t: float) -> bool:
    s = job.spec.get("start") or 0.0
    e = job.spec.get("end") or s
    return s - 1e-3 <= t < e - 0.02


class FileBar(QFrame):
    def __init__(self, on_replace, on_close):
        super().__init__()
        self.setObjectName("FileBar")
        h = QHBoxLayout(self); h.setContentsMargins(14, 8, 8, 8); h.setSpacing(10)
        ic = QLabel(); ic.setPixmap(pixmap("film-strip", 18, "accent"))
        texts = QVBoxLayout(); texts.setSpacing(0)
        self.name = QLabel(); self.name.setObjectName("SummaryTitle")
        self.sub = QLabel(); self.sub.setObjectName("Hint")
        for w in (self.name, self.sub):
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            texts.addWidget(w)
        h.addWidget(ic); h.addLayout(texts, 1)
        h.addWidget(button("Open another", on_replace, "ghost", icon="folder-open"))
        h.addWidget(button("Close", on_close, "ghost", icon="x"))
        self.hide()

    def set(self, path: str, info) -> None:
        self.name.setText(Path(path).name); self.name.setToolTip(path)
        audio = f" · {info.acodec}" if info.has_audio else " · no audio"
        self.sub.setText(f"{info.width}×{info.height} · {info.fps_float:.3f} fps · {_dur(info.duration)} · "
                         f"{info.vcodec}{audio}")
        self.show()


class InterpolatePage(QWidget):
    def __init__(self, qm, settings):
        super().__init__()
        self.qm, self.settings = qm, settings
        self.path = self.info = self.st = None
        self.fps_map = dict(FPS)
        self._applying = False
        self.take_ids: list[str] = []
        self._tinfo: dict = {}
        self._discard: set[str] = set()
        self._want = None
        self._left, self._right = None, "off"
        self._refreshing = False
        self._eng_fields: dict[str, list] = {}
        self.setAcceptDrops(True)

        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(8)
        self.header = page_header("Interpolate", "Smooth slow motion and higher frame rates with AI frame "
                                  "interpolation. One video at a time: preview, crop and trim it, then queue it.")
        root.addWidget(self.header)
        body = QHBoxLayout(); body.setSpacing(18); root.addLayout(body, 1)

        # ---------- left: file bar + player ----------
        left = QVBoxLayout(); left.setSpacing(8)
        self.filebar = FileBar(self.browse, self.close_video)
        p = self.player = PlayerPanel(qm, takes=True, maximizable=True)
        p.empty_text = "Open a video to get started. You can also drop one anywhere in the window."
        p._set_placeholder("film-strip", p.empty_text)
        p.ph_btn.setVisible(True)
        p.resolve, p.on_uncovered = self._resolve, self._uncovered
        p.describe_take, p.render_info = self._take_sub, self._render_desc
        p.open_requested.connect(self.browse)
        p.range_changed.connect(self._on_range)
        p.view.crop.changed.connect(self._on_crop_drawn)
        p.maximize_toggled.connect(self._set_maximized)
        p.cta_primary.connect(lambda: self._render())
        p.render_here.connect(lambda: self._render())
        p.compare_requested.connect(self._compare_requested)
        p.shown_changed.connect(self._refresh_takes)
        left.addWidget(self.filebar); left.addWidget(p, 1)
        body.addLayout(left, 1)

        # ---------- right: options ----------
        content = QWidget(); cv = QVBoxLayout(content); cv.setContentsMargins(18, 10, 18, 16); cv.setSpacing(12)
        content.setObjectName("OptionsBody"); content.setAttribute(Qt.WA_StyledBackground)
        self.preset = RichCombo(); self.preset.activated.connect(self._on_preset)
        pw = QWidget(); pl = QHBoxLayout(pw); pl.setContentsMargins(0, 0, 0, 0); pl.setSpacing(6)
        pl.addWidget(self.preset, 1)
        self.default_btn = button("", self._default_preset, "ghost", icon="star")
        self.default_btn.setToolTip("Make this preset the default: used every time Ralenti starts")
        pl.addWidget(self.default_btn)
        pl.addWidget(button("Save", self._save_preset, "ghost", icon="floppy-disk"))
        pl.addWidget(button("Delete", self._delete_preset, "ghost", icon="trash"))
        self.preset_field = Field("Preset", pw)
        self.preset.currentIndexChanged.connect(lambda *_: self.preset_field.set_hint(self.preset.blurb()))
        cv.addWidget(self.preset_field)

        motion = QWidget(); mv = QVBoxLayout(motion); mv.setContentsMargins(0, 0, 0, 0); mv.setSpacing(14)
        self.model = RichCombo(); mv.addWidget(Field("AI model", self.model))
        self.engine_warn = WarnRow("No AI engine yet. RIFE is free and open source, and installs in one click.",
                                   "Get RIFE (free)", lambda: self.window().go_downloads())
        mv.addWidget(self.engine_warn)
        self.slowmo = SlowmoPicker(); self.slow_field = Field("Slow motion", self.slowmo)
        mv.addWidget(self.slow_field)
        self.fps = RichCombo()
        for key, (_m, _v, title, blurb) in FPS.items():
            self.fps.add(title, key, blurb)
        mv.addWidget(Field("Frame rate", self.fps))
        cv.addWidget(Section("interp.motion", "Motion", "sparkle", motion, settings))

        quality = QWidget(); qv = QVBoxLayout(quality); qv.setContentsMargins(0, 0, 0, 0); qv.setSpacing(14)
        self.scale = ScalePicker(); qv.addWidget(Field("Processing resolution", self.scale))
        self.encoder = RichCombo(); qv.addWidget(Field("Encoder", self.encoder))
        self.vmaf = VmafPicker(); qv.addWidget(Field("Quality", self.vmaf))
        self.quality = QualityPicker(); self.q_field = Field("Constant quality level", self.quality)
        qv.addWidget(self.q_field)
        cv.addWidget(Section("interp.quality", "Quality", "gauge", quality, settings))

        aud = QWidget(); av = QVBoxLayout(aud); av.setContentsMargins(0, 0, 0, 0); av.setSpacing(10)
        self.audio = RichCombo(); av.addWidget(Field("Audio", self.audio))
        self.audio_warn = WarnRow("Original audio isn't slowed down, so it ends early and drifts out of sync "
                                  "with the slow-motion video.", "Use natural pitch",
                                  lambda: self.audio.select("natural"))
        av.addWidget(self.audio_warn)
        cv.addWidget(Section("interp.audio", "Audio", "speaker-high", aud, settings))

        crop = QWidget(); crv = QVBoxLayout(crop); crv.setContentsMargins(0, 0, 0, 0); crv.setSpacing(12)
        self.crop_default = RichCombo()
        for key, title, tag, blurb in CROP_DEFAULTS:
            self.crop_default.add(title, key, blurb, tag)
        crv.addWidget(Field("Black bars", self.crop_default))
        self.ratio = NiceCombo()
        for label, r in RATIOS:
            self.ratio.addItem(label, r)
        self.ratio.currentIndexChanged.connect(self._set_ratio)
        crv.addWidget(Field("Crop shape", self.ratio))
        crow = QHBoxLayout(); crow.setSpacing(6)
        self.edit_crop = QPushButton("Draw crop"); self.edit_crop.setCheckable(True)
        self.edit_crop.setIcon(icon("crop", "text")); self.edit_crop.toggled.connect(self._toggle_crop)
        self.detect_btn = button("Detect bars", self._detect, icon="magic-wand")
        self.reset_btn = button("Reset", self._reset_crop, "ghost", icon="arrow-counter-clockwise")
        for b in (self.edit_crop, self.detect_btn, self.reset_btn):
            crow.addWidget(b)
        crv.addLayout(crow)
        self.crop_info = QLabel(); self.crop_info.setObjectName("Hint"); self.crop_info.setWordWrap(True)
        self.range_info = QLabel(); self.range_info.setObjectName("Hint"); self.range_info.setWordWrap(True)
        crv.addWidget(self.crop_info); crv.addWidget(self.range_info)
        cv.addWidget(Section("interp.crop", "Crop & trim", "crop", crop, settings))

        self.out_dir = OutputField("dir")
        self.out_dir.changed.connect(lambda *_: self._update_summary())
        cv.addWidget(Section("interp.output", "Output", "export", Field("Save to", self.out_dir), settings))

        adv = QWidget(); al = QVBoxLayout(adv); al.setContentsMargins(0, 0, 0, 0); al.setSpacing(14)
        self.adv_note = QLabel(); self.adv_note.setObjectName("Hint"); self.adv_note.setWordWrap(True)
        al.addWidget(self.adv_note)
        for eng in engines():
            rows = []
            for opt in eng.options:
                seg = Segmented(list(opt.choices)); seg.set_value(opt.default)
                seg.changed.connect(self._changed)
                f = Field(opt.title, seg, opt.hint)
                al.addWidget(f)
                rows.append((opt, seg, f))
            self._eng_fields[eng.key] = rows
        cv.addWidget(Section("interp.advanced", "Advanced", "sliders", adv, settings, expanded=False))
        cv.addStretch(1)

        # ---------- previews tab ----------
        self.take_panel = TakePanel(float(getattr(settings, "preview_length", 3.0)))
        self.take_panel.render_requested.connect(self._render)
        self.take_panel.length_changed.connect(self._set_preview_length)
        self.take_panel.take_clicked.connect(self._take_clicked)
        self.take_panel.take_deleted.connect(self._drop_take)
        self.take_panel.take_rerender.connect(self._rerender)
        self.take_panel.take_versus.connect(self._take_versus)

        self.side = QWidget(); self.side.setFixedWidth(440)
        sv = QVBoxLayout(self.side); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(12)
        card = QFrame(); card.setObjectName("Card")
        cl = QVBoxLayout(card); cl.setContentsMargins(1, 12, 1, 1); cl.setSpacing(8)
        self.tabs = Segmented([("Settings", "settings", "How this video will be processed"),
                               ("Previews", "previews", "Rendered test takes of this video")])
        self.tabs.set_value("settings"); self.tabs.changed.connect(self._tab)
        tw = QWidget(); tl = QHBoxLayout(tw); tl.setContentsMargins(14, 0, 14, 0); tl.addWidget(self.tabs)
        self.pages = QStackedWidget()
        self.pages.addWidget(options_scroll(content)); self.pages.addWidget(self.take_panel)
        cl.addWidget(tw); cl.addWidget(self.pages, 1)
        self.summary = SummaryPanel(); self.summary.add_clicked.connect(self.queue_files)
        sv.addWidget(card, 1); sv.addWidget(self.summary)
        body.addWidget(self.side)

        for sig in (self.model.currentIndexChanged, self.fps.currentIndexChanged, self.audio.currentIndexChanged,
                    self.crop_default.currentIndexChanged, self.slowmo.changed, self.vmaf.changed,
                    self.scale.changed, self.quality.changed):
            sig.connect(self._changed)
        self.model.currentIndexChanged.connect(self._sync_engine)
        self.encoder.currentIndexChanged.connect(self._enc_changed)
        self.crop_default.currentIndexChanged.connect(self._update_side)
        self._bridge = QueueBridge(qm)
        self._bridge.event.connect(self._queue_event)

        self.reload_options()
        self._fill_presets()
        if (dname := presets.get_default()) and (dopts := presets.get(dname)):
            self.apply_options(dopts)
            self.preset.select(dname)
        elif last := presets.load_last():
            self.apply_options(last)
        else:
            self.apply_options(presets.DEFAULT)
            self.preset.select(presets.DEFAULT_NAME)
        self._update_side(); self._refresh_takes()

    # =================== options ===================
    def reload_options(self) -> None:
        tc = self.qm.tc
        for e in engines():
            e.refresh()
        self._applying = True
        try:
            self._fill_models(self.model.currentData() or "auto")
            cur = self.encoder.currentData() or "av1_nvenc"
            self.encoder.clear()
            for key, e in ENCODERS.items():
                if not e.hidden and tc.binary_for(e.codec):
                    self.encoder.add(e.label, key, e.blurb, e.tag)
            if not self.encoder.select(cur):
                self.encoder.setCurrentIndex(0)
            cur = self.audio.currentData() or "natural"
            self.audio.clear()
            stretch = ("Uses Rubber Band for clean results." if tc.has_rubberband
                       else "Can sound slightly phasey at 4× and above.")
            for key, title, tag, blurb in AUDIO:
                self.audio.add(title, key, blurb.format(engine=stretch), tag)
            self.audio.select(cur)
        finally:
            self._applying = False
        self._enc_changed()

    def _fill_models(self, cur) -> None:
        tc = self.qm.tc
        self.model.clear()
        found = False
        for eng in available_engines(tc):
            models = eng.models(tc)
            if not models:
                continue
            self.model.add_header(eng.group or eng.name)
            for m in models:
                self.model.add(m.label, m.code, m.blurb, m.tag)
                found = True
        if not found:
            self.model.add("No AI engine installed", None, "Install RIFE (free) from Downloads.")
        self._select_model(cur)
        self.engine_warn.setVisible(not found)
        self._sync_engine()

    def _select_model(self, code) -> None:
        c = resolve_model(self.qm.tc, code)
        if c and self.model.select(c):
            return
        self.model.setCurrentIndex(max(0, self.model.count() - 1))

    def _cur_engine(self):
        return engine_for(self.model.currentData())

    def _engine_opts(self) -> dict:
        eng = self._cur_engine()
        return {o.key: seg.value() for o, seg, _f in self._eng_fields.get(eng.key, [])} if eng else {}

    def _sync_engine(self, *_) -> None:
        eng = self._cur_engine()
        rows = self._eng_fields.get(eng.key, []) if eng else []
        for key, rs in self._eng_fields.items():
            for _o, _s, f in rs:
                f.setVisible(bool(eng) and key == eng.key)
        self.adv_note.setText(f"{eng.name} has no extra settings." if eng else
                              "Install an AI engine to see its settings.")
        self.adv_note.setVisible(not rows)

    def _model_name(self, code) -> str:
        if not code:
            return "No engine"
        i = self.model.findData(code)
        return self.model.itemText(i) if i >= 0 else str(code)

    def _enc_changed(self, *_) -> None:
        e = ENCODERS.get(self.encoder.currentData() or "")
        if e is None:
            return
        self.quality.set_encoder(e)
        if not e.has_quality:
            self.vmaf.set_available(False, f"{e.label} uses a fixed quality profile, so a VMAF target doesn't apply.")
        elif not self.qm.tc.has_vmaf:
            self.vmaf.set_available(False, "VMAF needs the full FFmpeg build (see Downloads).")
        else:
            self.vmaf.set_available(True)
        self._changed()

    def _changed(self, *_) -> None:
        if not self._applying and self.preset.count():
            self.preset.setCurrentIndex(0)
        self._update_summary()
        self._refresh_takes()

    def _cq_text(self) -> str:
        e = ENCODERS.get(self.encoder.currentData() or "")
        q = self.quality.value()
        return f"constant quality, {e.q_flag.upper()} {q}" if e and q is not None else "fixed profile"

    def _update_summary(self, *_) -> None:
        o = self.current_options()
        slow, mode, val = o["slowmo"], o["fps_mode"], o["fps_value"]
        st, info = self.st, self.info
        self.audio_warn.setVisible(o["audio"] == "copy" and slow != 1)
        self.scale.set_source((info.width, info.height) if info else None)
        enc = ENCODERS.get(o["encoder"] or "")
        self.q_field.setVisible(bool(enc and enc.has_quality) and self.vmaf.effective() is None)
        warn = None
        if not o["model"]:
            warn = "No AI engine yet: install RIFE (free) from Downloads."
        elif slow == 1 and mode == "keep":
            warn = "Nothing to interpolate: turn on slow motion or pick a higher frame rate."
        rows = []
        if info:
            a = st["start"] or 0.0
            b = st["end"] if st["end"] is not None else info.duration
            d = max(0.0, b - a)
            tgt = target_fps(info.fps, mode, val or 2.0) or info.fps
            self.slow_field.set_hint(f"{slow:g}× turns this {_dur(d)} clip into {_dur(d * slow)}." if slow != 1
                                     else f"Real-time: the clip stays {_dur(d)} long.")
            trimmed = " (trimmed)" if d < info.duration - 0.05 else ""
            rows.append(("timer", "Timing", f"{info.fps_float:.3f} → <b>{float(tgt or 0):.3f} fps</b><br>"
                                            f"{_dur(d)} → <b>{_dur(d * slow)}</b>{trimmed}"))
            w, h = (st["crop"][0], st["crop"][1]) if st["manual"] and st["crop"] else (info.width, info.height)
            if o["scale"] < 1:
                tw, th = scaled_size(w, h, o["scale"])
                size = f"<b>{tw}×{th}</b> · {round(o['scale'] * 100)}% of {w}×{h}"
            else:
                size = f"<b>{w}×{h}</b> · full resolution"
            if st["manual"] and st["crop"]:
                size += " · cropped"
            elif not st["manual"] and o["crop"] == "auto":
                size += " · auto crop"
            rows.append(("arrows-out", "Size", size))
            self.summary.set_media(self.path, Path(self.path).name,
                                   f"{info.width}×{info.height} · {info.fps_float:.3f} fps · {_dur(info.duration)}")
        else:
            self.slow_field.set_hint("How much slower the clip plays. 2× turns 10 s into 20 s.")
            self.summary.set_media(None)
        motion = f"{slow:g}× slower" if slow != 1 else "real-time"
        if mode != "keep":
            motion += f" · {self.fps.currentText()}"
        rows.append(("sparkle", "Motion", f"<b>{self._model_name(o['model'])}</b> · {motion}"))
        if enc:
            v = self.vmaf.effective()
            rows.append(("gauge", "Quality", f"<b>{enc.label}</b> · " +
                         (f"VMAF {v:g}, sample tested" if v else self._cq_text())))
        a_key = o["audio"] or ""
        if slow == 1 and a_key in ("natural", "keep_pitch"):
            rows.append(("speaker-high", "Audio", "Original (nothing to slow down)"))
        elif slow != 1 and a_key == "copy":
            rows.append(("speaker-high", "Audio", "Original · ends early, out of sync", "warn"))
        else:
            rows.append(("speaker-high", "Audio", AUDIO_SUMMARY.get(a_key, "")))
        if info and o["model"]:
            from ...core.estimate import describe, estimate
            w0, h0 = (st["crop"][0], st["crop"][1]) if st["manual"] and st["crop"] else (info.width, info.height)
            est = describe(estimate(info, {**o, "vmaf": self.vmaf.effective()}, scaled_size(w0, h0, o["scale"]),
                                    st["start"], st["end"], self.qm.rates))
            rows.append(("hourglass", "Estimate", est or "Run the benchmark (Settings › Performance) to see one"))
        rows.append(("export", "Save to", self.out_dir.text() or "Next to the source file"))
        self.summary.set_rows(rows, warn)
        self.summary.set_action("Add to queue" if info else "Open a video to get started", bool(info) and not warn)

    # =================== presets ===================
    def current_options(self) -> dict:
        mode, val, *_ = self.fps_map.get(self.fps.currentData() or "keep", FPS["keep"])
        cq = self.vmaf.effective() is None
        return dict(model=self.model.currentData(), fps_mode=mode, fps_value=val, slowmo=self.slowmo.value(),
                    audio=self.audio.currentData(), encoder=self.encoder.currentData(),
                    vmaf=self.vmaf.value() or 0.0, q=self.quality.value() if cq else None,
                    crop=self.crop_default.currentData(), scale=self.scale.value(),
                    engine_opts=self._engine_opts())

    def _fps_key(self, mode: str, val: float) -> str:
        for k, (m, v, *_) in self.fps_map.items():
            if m == mode and (mode == "keep" or abs(v - val) < 1e-6):
                return k
        k = f"{mode}:{val:g}"
        title = f"{val:g}×" if mode == "multiply" else f"Exactly {val:g} fps"
        self.fps_map[k] = (mode, val, title, "From a saved preset.")
        self.fps.add(title, k, "From a saved preset.")
        return k

    def apply_options(self, o: dict) -> None:
        self._applying = True
        try:
            self._select_model(o.get("model", "auto"))
            self.slowmo.set_value(float(o.get("slowmo", 1)))
            self.scale.set_value(float(o.get("scale", 1.0) or 1.0))
            self.fps.select(self._fps_key(o.get("fps_mode", "keep"), float(o.get("fps_value") or 0)))
            self.encoder.select(o.get("encoder", "av1_nvenc"))
            self.vmaf.set_value(o.get("vmaf") or None)
            self.audio.select(o.get("audio", "natural"))
            self.crop_default.select(o.get("crop", "auto"))
            self._sync_engine()
            if eng := self._cur_engine():
                opts = {**eng.defaults(), **(o.get("engine_opts") or {})}
                for opt, seg, _f in self._eng_fields.get(eng.key, []):
                    seg.set_value(opts.get(opt.key, opt.default)) or seg.set_value(opt.default)
        finally:
            self._applying = False
        self._enc_changed()
        self._apply_q(o.get("q"))
        self._update_side()

    def _apply_q(self, q) -> None:
        e = ENCODERS.get(self.encoder.currentData() or "")
        if q is None or not e or not e.has_quality:
            return
        self._applying = True
        try:
            d = int(q) - e.q_default
            if d in (-4, 0, 4, 8):
                self.quality.seg.set_value(d)
            else:
                self.quality.seg.set_value("custom")
                self.quality.custom.setValue(q)
            self.quality._sync()
        finally:
            self._applying = False
        self._update_summary()

    def _fill_presets(self, select: str | None = None) -> None:
        self._applying = True
        self.preset.clear()
        self.preset.add("Custom", None, "Your current settings. Save them as a preset to reuse later.")
        default = presets.get_default()
        groups = presets.all_presets()
        for title, builtin in (("Built-in", True), ("Your presets", False)):
            rows = [g for g in groups if g[2] == builtin]
            if not rows:
                continue
            self.preset.add_header(title)
            for name, _o, _b, tag, blurb in rows:
                self.preset.add(name, name, blurb, "Default" if name == default else tag)
        if select:
            self.preset.select(select)
        self._applying = False

    def _on_preset(self, idx: int) -> None:
        name = self.preset.itemData(idx)
        if name and (o := presets.get(name)):
            self.apply_options(o)
            self.preset.setCurrentIndex(idx)

    def _save_preset(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Name your preset:")
        name = name.strip()
        if not ok or not name:
            return
        if name in presets.BUILTIN:
            QMessageBox.information(self, "Ralenti", "A built-in preset already uses that name.")
            return
        presets.save_user(name, self.current_options())
        self._fill_presets(select=name)
        notify(self, f"Saved preset “{name}”", "ok")

    def _delete_preset(self) -> None:
        name = self.preset.currentData()
        if not name:
            return
        if name in presets.BUILTIN:
            QMessageBox.information(self, "Ralenti", "Built-in presets can't be deleted.")
            return
        presets.delete_user(name)
        self._fill_presets()
        notify(self, f"Deleted preset “{name}”", "info")

    def _default_preset(self) -> None:
        name = self.preset.currentData()
        if not name:
            notify(self, "Pick or save a preset first, then make it the default.", "info")
            return
        if presets.get_default() == name:
            presets.set_default(None)
            notify(self, f"“{name}” is no longer the default", "info")
        else:
            presets.set_default(name)
            notify(self, f"“{name}” will be used every time Ralenti starts", "ok")
        self._fill_presets(select=name)

    # =================== the video ===================
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if paths := paths_from_mime(e.mimeData()):
            self.add_files(paths)

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open a video", "", FILTER)
        if path:
            self.open(path)

    def add_files(self, paths: list[str]) -> None:
        if not paths:
            return
        self.open(paths[0])
        if len(paths) > 1:
            notify(self, "Interpolate works on one video at a time, so the first one was opened. "
                         "For batches, use Encode or queue them one by one.", "info")

    def open(self, path: str) -> None:
        if path == self.path:
            return
        info = probe(self.qm.tc.ffprobe, path)
        if not info.width:
            QMessageBox.warning(self, "Ralenti", f"This file isn't readable as video:\n{path}")
            return
        self._drop_all_takes()
        self.edit_crop.setChecked(False)
        self.path, self.info = path, info
        self.st = dict(crop=None, manual=False, start=None, end=None)
        self._left, self._right = None, "off"
        self.player.load(path, info)
        self.filebar.set(path, info)
        self._update_side(); self._refresh_takes()

    def close_video(self) -> None:
        self._drop_all_takes()
        self.edit_crop.setChecked(False)
        self.path = self.info = self.st = None
        self.player.unload()
        self.filebar.hide()
        self._update_side(); self._refresh_takes()

    def _update_side(self, *_) -> None:
        st = self.st
        for w in (self.edit_crop, self.detect_btn, self.reset_btn):
            w.setEnabled(st is not None)
        if not st:
            self.crop_info.setText("Open a video to draw a crop or trim it.")
            self.range_info.setText("")
        else:
            if st["manual"]:
                c = st["crop"]
                self.crop_info.setText(f"This video: {c[0]}×{c[1]} at {c[2]},{c[3]}" if c else "This video: full frame")
            else:
                self.crop_info.setText("Using the default: " + self.crop_default.currentText().lower())
            s, e = st["start"], st["end"]
            if s is None and e is None:
                self.range_info.setText("Whole clip · press I and O in the player to trim")
            else:
                a, b = s or 0.0, e if e is not None else self.info.duration
                self.range_info.setText(f"Trimmed: {_clock(a)} → {_clock(b)} ({_dur(b - a)})")
        self._update_summary()

    # =================== range & crop ===================
    def _on_range(self, s, e) -> None:
        if self.st:
            self.st.update(start=s, end=e)
            self._update_side()

    def _toggle_crop(self, on: bool) -> None:
        if on and self.player.take:
            self.player.show_source()
        ov = self.player.view.crop
        if on and self.st:
            ov.set_crop(self.st["crop"] if self.st["manual"] else None)
            self.player.pause()
        ov.setVisible(bool(on and self.st))

    def _on_crop_drawn(self, crop) -> None:
        if self.st:
            self.st.update(crop=list(crop) if crop else None, manual=True)
            self._update_side(); self._refresh_takes()

    def _set_ratio(self, *_) -> None:
        r = self.ratio.currentData()
        if r == "source":
            r = self.info.width / self.info.height if self.info and self.info.height else None
        self.player.view.crop.set_ratio(r)

    def _detect(self) -> None:
        tc = self.qm.tc
        if not self.path or not tc.has_cropdetect:
            return
        p, info, ff = self.path, self.info, tc.ffmpeg
        self.detect_btn.setEnabled(False); self.detect_btn.setText("Scanning…")
        run_async(lambda: detect_crop(ff, info), lambda c: self._detected(p, c), self._async_error)

    def _detected(self, p, c) -> None:
        self.detect_btn.setText("Detect bars")
        if p != self.path:
            return
        crop = [c.w, c.h, c.x, c.y] if c else None
        self.st.update(crop=crop, manual=True)
        self.player.view.crop.set_crop(crop)
        if crop:
            self.edit_crop.setChecked(True)
        self._update_side(); self._refresh_takes()
        notify(self, f"Found bars · cropping to {c.w}×{c.h}" if c else "No black bars found", "ok" if c else "info")

    def _reset_crop(self) -> None:
        if self.st:
            self.st.update(crop=None, manual=False)
            self.player.view.crop.set_crop(None)
            self._update_side(); self._refresh_takes()

    def _async_error(self, msg: str) -> None:
        self.detect_btn.setText("Detect bars")
        self._update_side()
        QMessageBox.warning(self, "Ralenti", msg[-2500:])

    # =================== engine confirmation ===================
    def _engine_ok(self) -> bool:
        eng = self._cur_engine()
        if eng is None:
            notify(self, "Install an AI engine first: RIFE is free, in Downloads.", "warn")
            return False
        res = eng.confirm(self, self.settings, self.qm.tc)
        if isinstance(res, str) and res:
            self._select_model(res)
            notify(self, f"Switched to {self._model_name(self.model.currentData())}", "info")
            return True
        return bool(res)

    # =================== takes ===================
    def _tab(self, v) -> None:
        self.pages.setCurrentIndex(0 if v == "settings" else 1)

    def _sig(self) -> str:
        o = self.current_options()
        crop = (self.st["crop"] or "none") if self.st["manual"] else o["crop"]
        return json.dumps({**{k: o[k] for k in SIG_KEYS}, "crop": crop}, sort_keys=True)

    def _jobs(self) -> list:
        return [j for j in (self.qm.get(i) for i in self.take_ids) if j]

    @staticmethod
    def _ready(job) -> bool:
        return job.status == Status.DONE and os.path.exists(job.output)

    def _texts(self, job) -> tuple[str, str]:
        s, st = job.spec, job.state
        start, end = s.get("start") or 0.0, s.get("end") or 0.0
        size = st.get("size")
        res = f"{size[0]}×{size[1]}" if size else (f"{round(s['scale'] * 100)}%" if s.get("scale", 1) < 1 else "full res")
        title = f"{_clock(start)} · {s['slowmo']:g}× · {self._model_name(s['model'])}"
        sub = f"{_dur(end - start)} · {res} · {AUDIO_NAMES.get(job.plan.get('audio_mode'), '')}"
        return title, sub

    def _take_sub(self, jid) -> str:
        job = self.qm.get(jid)
        return self._texts(job)[1] if job else ""

    def _details(self, job) -> str:
        s, st = job.spec, job.state
        bits = [self._model_name(s["model"])]
        if st.get("fps"):
            bits.append(f"{float(Fraction(st['fps'])):.3f} fps")
        if s["slowmo"] != 1:
            bits.append(f"{s['slowmo']:g}× slow-mo")
        size = st.get("size")
        bits.append(f"{size[0]}×{size[1]} ({round(s['scale'] * 100)}%)" if size else "full resolution")
        if st.get("crop"):
            bits.append(f"crop {st['crop'][0]}×{st['crop'][1]}")
        bits.append(AUDIO_NAMES.get(job.plan.get("audio_mode"), ""))
        return " · ".join(b for b in bits if b)

    def _stale(self, job) -> bool:
        return job.spec.get("src") == self.path and self.st is not None and job.spec.get("sig") != self._sig()

    def _take_dict(self, job):
        info = self._tinfo.get(job.id)
        if info is None:
            info = probe(self.qm.tc.ffprobe, job.output)
            if not info.width:
                return None
            self._tinfo[job.id] = info
        s = job.spec
        start = s.get("start") or 0.0
        end = s.get("end") or start
        side = job.state.get("sidecars")
        audio = tuple(side) if side and all(os.path.exists(a) for a in side) else None
        note = None
        if audio and not job.state.get("orig_stretched"):
            audio, note = (None, audio[1]), "Re-render this take to hear the original audio in sync"
        stale = self._stale(job)
        return dict(id=job.id, path=job.output, info=info, start=start, end=end, slowmo=float(s["slowmo"]),
                    before="Original", after=f"AI · {self._model_name(s['model'])}",
                    details=self._details(job) + (" · settings changed since this render" if stale else ""),
                    caption=f"Take {_clock(start)}–{_clock(end)}", audio=audio, audio_note=note,
                    audio_labels=("Original", AUDIO_NAMES.get(job.plan.get("audio_mode"), "Processed")))

    def _render_desc(self) -> str:
        o = self.current_options()
        n = self.take_panel.length()
        bits = [self._model_name(o["model"]),
                f"{o['slowmo']:g}× slow-mo" if o["slowmo"] != 1 else self.fps.currentText(),
                f"{round(o['scale'] * 100)}%" if o["scale"] < 1 else "full res",
                AUDIO_NAMES.get(o["audio"], ""), "whole clip" if n <= 0 else f"{n:g} s"]
        return " · ".join(b for b in bits if b)

    def _refresh_takes(self, *_) -> None:
        if not hasattr(self, "take_panel") or self._refreshing:
            return
        self._refreshing = True
        reroute = False
        try:
            self.take_panel.set_has_file(self.path is not None)
            jobs = self._jobs() if self.path else []
            shown = self.player.take["id"] if self.player.take else None
            b_id = self.player.take_b["id"] if self.player.take_b else None
            right_shown = b_id or shown
            items = [(j, *self._texts(j), j.status == Status.DONE and self._stale(j), j.id == right_shown,
                      bool(b_id) and j.id == shown) for j in jobs]
            self.take_panel.set_takes(items)
            self.tabs.group.button(1).setText(f"Previews · {len(jobs)}" if jobs else "Previews")
            ready = [j for j in jobs if self._ready(j)]
            self.player.set_takes([(j.spec.get("start") or 0.0, j.spec.get("end") or 0.0, j.id in (shown, b_id))
                                   for j in ready])
            self.player.set_compare_choices([(j.id, self._texts(j)[0]) for j in ready], self._left, self._right)
            reroute = bool(shown) and shown not in {j.id for j in ready}
        finally:
            self._refreshing = False
        if reroute:
            self.player.route_to(self.player.source_position())

    def _render(self, length=None) -> None:
        if not self.path:
            return
        length = self.take_panel.length() if length is None else float(length)
        if length <= 0:
            t0 = self.st["start"] or 0.0
            end = self.st["end"] if self.st["end"] is not None else self.info.duration
            length = max(0.2, end - t0)
        else:
            t0 = self.player.source_position()
            if self.info.duration - t0 < 0.5:
                t0 = max(0.0, self.info.duration - length)
        o = self.current_options()
        if o["model"] and o["slowmo"] == 1 and o["fps_mode"] == "keep":
            notify(self, "Nothing to preview: turn on slow motion or pick a higher frame rate.", "warn")
            return
        self._start_take(t0, length)

    def _start_take(self, t0: float, length: float) -> None:
        if not self._engine_ok():
            return
        o = self.current_options()
        crop = (self.st["crop"] or "none") if self.st["manual"] else o["crop"]
        try:
            job = pipeline.make_preview_job(self.qm.tc, self.settings, self.path, t0, length,
                                            **{**o, "crop": crop, "device": self.settings.gpu_device or "0"})
        except Exception as e:
            QMessageBox.warning(self, "Ralenti", str(e)); return
        job.spec["sig"] = self._sig()
        self.take_ids.append(job.id)
        self._want = job.id
        self.qm.add(job)
        self.tabs.set_value("previews"); self._tab("previews")
        self._refresh_takes()
        if self.qm.is_running:
            notify(self, "Preview started next to the running job. They share the GPU, so it may take a bit longer.",
                   "info")

    def _rerender(self, jid: str) -> None:
        job = self.qm.get(jid)
        if not job or job.spec.get("src") != self.path:
            return
        s = job.spec
        start = s.get("start") or 0.0
        length = max(0.2, (s.get("end") or start) - start)
        self._drop_take(jid)
        self._start_take(start, length)

    def _drop_all_takes(self) -> None:
        for jid in list(self.take_ids):
            self._drop_take(jid)

    def _drop_take(self, jid: str) -> None:
        if self._left == jid:
            self._left = None
        if self._right == jid:
            self._right = "off"
        if jid in self.take_ids:
            self.take_ids.remove(jid)
        job = self.qm.get(jid)
        if job:
            if self.player.take and self.player.take["id"] == jid:
                self.player.show_source()
            if job.status == Status.RUNNING:
                self._discard.add(jid)
                self.qm.cancel_job(jid)
            else:
                self._purge(job)
        self._refresh_takes()

    def _purge(self, job) -> None:
        self.qm.remove(job.id)
        for f in (job.state.get("sidecars") or []) + [job.state.get("thumb")]:
            if f:
                try:
                    Path(f).unlink(missing_ok=True)
                except OSError:
                    pass
        self._tinfo.pop(job.id, None)

    def _queue_event(self, ev, job) -> None:
        if job is None or job.kind != "preview":
            return
        src = job.spec.get("src")
        if ev == "removed":
            if job.id in self.take_ids:
                self.take_ids.remove(job.id)
            if self.player.take and self.player.take["id"] == job.id:
                self.player.show_source()
        elif ev == "finished":
            if job.id in self._discard:
                self._discard.discard(job.id)
                self._purge(job)
            elif job.status == Status.DONE and job.id == self._want:
                self._want = None
                if src == self.path:
                    self._show(job)
            elif job.status == Status.FAILED and src == self.path:
                first = (job.error or "").strip().splitlines() or ["see the queue log"]
                notify(self, "Preview failed: " + first[0], "error")
        if src == self.path:
            self._refresh_takes()

    def _show(self, job, at=None) -> None:
        if self.edit_crop.isChecked():
            self.edit_crop.setChecked(False)
        if self._left == job.id:
            self._left = None
        self._right = job.id
        self._refresh_takes()
        self.player.route_to((job.spec.get("start") or 0.0) if at is None else at, play=True)

    # ---------- compare model: follows the playhead ----------
    def _resolve(self, t: float):
        if not self.path:
            return None
        ready = {j.id: j for j in self._jobs() if self._ready(j)}
        if self._left:
            a = ready.get(self._left)
            if not a or not _covers(a, t):
                return None
            b = ready.get(self._right) if self._right != "off" else None
            A = self._take_dict(a)
            if b and b.id != a.id and _covers(b, t):
                B = self._take_dict(b)
                return (A, B, "ab") if A and B else None
            return (A, None, "result") if A else None
        if self._right == "off":
            return None
        j = ready.get(self._right)
        if not j or not _covers(j, t):
            return None
        A = self._take_dict(j)
        return (A, None, "orig") if A else None

    def _uncovered(self, t: float) -> None:
        if self._right == "off" or not self.path:
            return
        n = self.take_panel.length()
        self.player.show_cta(f"No preview at {_clock(t)}",
                             "Render a few seconds here to compare the AI result with the original.",
                             "Render the whole clip" if n <= 0 else f"Render {n:g} s here")

    def _compare_requested(self, left, right) -> None:
        if left and left == right:
            right = "off"
        self._left, self._right = left, right
        t = self.player.source_position()
        target = left or (right if right != "off" else None)
        job = self.qm.get(target) if target else None
        if job and self._ready(job) and not _covers(job, t):
            t = job.spec.get("start") or 0.0
        self._refresh_takes()
        self.player.route_to(t)

    def _take_clicked(self, jid: str) -> None:
        job = self.qm.get(jid)
        if not job:
            return
        if self._ready(job):
            self._show(job)
        elif job.status in (Status.QUEUED, Status.RUNNING):
            notify(self, "This take is still rendering", "info")

    def _take_versus(self, jid: str) -> None:
        cur = self._right if self._right != "off" else (self.player.take["id"] if self.player.take else None)
        if not cur or cur == jid:
            self._take_clicked(jid)
            notify(self, "Showing this take. Press ⇄ on another take to compare the two.", "info")
            return
        a, b = self.qm.get(cur), self.qm.get(jid)
        if not (a and b and self._ready(a) and self._ready(b)):
            return
        s = max(a.spec.get("start") or 0.0, b.spec.get("start") or 0.0)
        e = min(a.spec.get("end") or 0.0, b.spec.get("end") or 0.0)
        if e - s < 0.05:
            notify(self, "These two takes don't cover the same moment, so they can't be compared.", "warn")
            return
        self._left, self._right = cur, jid
        self._refresh_takes()
        self.player.route_to(s, play=True)

    def _set_maximized(self, on: bool) -> None:
        self.header.setVisible(not on)
        self.side.setVisible(not on)
        self.filebar.setVisible(not on and self.path is not None)

    def _set_preview_length(self, v: float) -> None:
        self.settings.preview_length = float(v)
        self.settings.save()

    # =================== queue ===================
    def queue_files(self) -> None:
        if not self.path or not self._engine_ok():
            return
        o = self.current_options()
        presets.save_last(o)
        base = {**o, "vmaf": self.vmaf.effective(), "device": self.settings.gpu_device or "0",
                "out_dir": self.out_dir.text()}
        default_crop = base.pop("crop")
        crop = (self.st["crop"] or "none") if self.st["manual"] else default_crop
        try:
            job = pipeline.make_interpolate_job(self.qm.tc, self.settings, self.path,
                                                **{**base, "crop": crop, "start": self.st["start"],
                                                   "end": self.st["end"]})
        except Exception as e:
            QMessageBox.warning(self, "Ralenti", str(e)); return
        if not confirm_space(self, self.qm, [job]):
            return
        self.qm.add(job)
        name = Path(self.path).name
        self.close_video()
        notify(self, f"Added {name} to the queue · open the next video when ready", "ok")
        if self.summary.autostart.isChecked():
            self.qm.start()
