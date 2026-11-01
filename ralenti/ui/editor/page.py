"""The editor: one video (one or more clips), a player tailored to each step, six step pages, presets and one
summary. Each step has its own tools under the player. From Transform on, the player shows the rotated, flipped
and cropped picture live. Removed segments are skipped while playing (in Trim only when asked)."""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QMessageBox, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)
from shiboken6 import isValid

from ...core import edit_presets
from ...core.edit import compile_job
from ...core.scenes import detect_times
from ...core.segments import add_cut, kept_ranges, remove_cut, segments, to_cuts, toggle
from ..icons import icon
from ..space_check import confirm_space
from ..util import fmt_ts, notify
from ..widgets.panel import options_scroll
from ..widgets.player import PlayerPanel
from ..workers import run_async
from .clips_bar import ClipsBar
from .panels import RATIOS, AudioPanel, CropPanel, EncodePanel, MotionPanel, OutputPanel, TrimPanel
from .presets_ui import PresetBar
from .session import EditSession
from .summary import analyse, dur, estimate_html, messages, rows
from .toolbars import AppliedChips, ToolRow, tool
from .trim_view import SegmentBar
from .widgets import StepSummary, ToggleSwitch

STEPS_UI = {
    "trim": ("Trim", "Cut the video into segments and keep only what you need. On its own it's instant and lossless."),
    "crop": ("Transform", "Crop, remove black bars, rotate, flip and resize. Applied before the AI, so it also "
                          "makes it faster."),
    "motion": ("Interpolation", "Smooth slow motion and higher frame rates with AI interpolation."),
    "audio": ("Audio", "Keep the original sound, convert it, or remove it."),
    "encode": ("Encode", "How the video is encoded: copied as is, or re-encoded to a quality target."),
    "output": ("Output", "Where the file goes and what it's called."),
}
ORDER = list(STEPS_UI)
TOGGLES = ("trim", "crop", "motion")
TOOL_STEPS = ("trim", "crop", "motion")
PLAYING = QMediaPlayer.PlayingState
SCENE_TOL, PICK_TOL = 0.12, 0.25


class EditorPage(QWidget):
    dots_changed = Signal()

    def __init__(self, qm, settings):
        super().__init__()
        self.qm, self.settings = qm, settings
        self.step, self.cur_clip, self._what, self._pending = "motion", None, set(), False
        self._before, self._scenes, self._scene_busy, self._chip_items = False, {}, None, []
        self.session = s = EditSession(qm)
        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(10)

        head = QHBoxLayout(); head.setSpacing(12)
        texts = QVBoxLayout(); texts.setSpacing(3)
        self.title = QLabel(); self.title.setObjectName("PageTitle")
        self.sub = QLabel(); self.sub.setObjectName("PageSub"); self.sub.setWordWrap(True)
        texts.addWidget(self.title); texts.addWidget(self.sub)
        self.presets = PresetBar(s, settings)
        self.toggle_lbl = QLabel(); self.toggle_lbl.setObjectName("Muted")
        self.toggle = ToggleSwitch(); self.toggle.toggled.connect(lambda on: self.session.set(self.step, on=on))
        head.addLayout(texts, 1); head.addWidget(self.presets, 0, Qt.AlignVCenter); head.addSpacing(14)
        head.addWidget(self.toggle_lbl, 0, Qt.AlignVCenter); head.addWidget(self.toggle, 0, Qt.AlignVCenter)
        root.addLayout(head)

        self.clips = ClipsBar(s); self.clips.selected.connect(self._show_clip)
        root.addWidget(self.clips)
        self.chips = AppliedChips(); self.chips.clicked.connect(lambda k: self.window().go(k))
        root.addWidget(self.chips)

        body = QHBoxLayout(); body.setSpacing(18); root.addLayout(body, 1)
        p = self.player = PlayerPanel(qm, show_range=False, maximizable=True)
        p.empty_text = "Open a video to get started. You can also drop clips anywhere in the window."
        p._set_placeholder("film-strip", p.empty_text)
        p.ph_btn.setVisible(True)
        p.open_requested.connect(self.browse)
        p.view.crop.changed.connect(self._crop_drawn)
        p.maximize_toggled.connect(self._maximized)
        p.player.positionChanged.connect(self._pos)
        p.player.mediaStatusChanged.connect(self._media_status)
        self.segbar = SegmentBar()
        self.segbar.toggled.connect(self._seg_toggled)
        self.segbar.cut_moved.connect(self._cut_moved)
        self.segbar.cut_removed.connect(self._cut_removed)
        p.timeline.view_changed.connect(self.segbar.update)
        self._build_tools()
        p.add_row(self.tool_stack)
        self.trim_box = QWidget()
        tb = QVBoxLayout(self.trim_box); tb.setContentsMargins(0, 2, 0, 0); tb.setSpacing(4)
        h = QLabel("Click a segment to keep or remove it · drag a cut to move it · right-click a cut to delete it")
        h.setObjectName("Hint"); h.setWordWrap(True)
        tb.addWidget(self.segbar); tb.addWidget(h)
        p.add_row(self.trim_box)
        body.addWidget(p, 1)

        self.side = QWidget(); self.side.setFixedWidth(440)
        sv = QVBoxLayout(self.side); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(12)
        card = QFrame(); card.setObjectName("Card")
        cl = QVBoxLayout(card); cl.setContentsMargins(1, 1, 1, 1)
        self.stack = QStackedWidget(); cl.addWidget(self.stack)
        self.panels = {"trim": TrimPanel(s), "crop": CropPanel(s), "motion": MotionPanel(s), "audio": AudioPanel(s),
                       "encode": EncodePanel(s), "output": OutputPanel(s)}
        for panel in self.panels.values():
            self.stack.addWidget(options_scroll(panel))
        self.panels["trim"].goto.connect(lambda t: self._goto(t, play=False))
        self.panels["crop"].draw_toggled.connect(self.c_draw.setChecked)
        self.panels["crop"].ratio_changed.connect(self._ratio)
        self.summary = StepSummary()
        self.summary.step_clicked.connect(lambda k: self.window().go(k))
        self.summary.add_clicked.connect(self.queue)
        sv.addWidget(card, 1); sv.addWidget(self.summary)
        body.addWidget(self.side)

        keys = [("Ctrl+Z", s.undo), ("Ctrl+Y", s.redo), ("Ctrl+Shift+Z", s.redo),
                ("C", lambda: self._on("trim", self.add_cut_here)),
                ("X", lambda: self._on("trim", self._toggle_here)),
                (Qt.Key_Comma, lambda: self._prev_next(-1)), (Qt.Key_Period, lambda: self._prev_next(1)),
                ("R", lambda: self._on("crop", lambda: self._rotate(90))),
                ("Shift+R", lambda: self._on("crop", lambda: self._rotate(-90))),
                ("H", lambda: self._on("crop", self.c_hf.toggle)), ("V", lambda: self._on("crop", self.c_vf.toggle)),
                ("D", lambda: self._on("crop", self.c_draw.toggle)),
                ("B", lambda: self.step != "trim" and self.c_before.toggle()),
                ("S", lambda: self._on("motion", self._toggle_scene))]
        for k, fn in keys:
            sc = QShortcut(QKeySequence(k), self); sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        s.changed.connect(self._changed)
        s.restore()
        if not s.edit.clips and (d := edit_presets.get_default()) and (r := edit_presets.get(d)):
            edit_presets.apply(s.edit, r)
        self._changed("all")
        self.show_step("motion")

    # ---------- tool rows ----------
    def _build_tools(self) -> None:
        self.rows = {k: ToolRow() for k in TOOL_STEPS}
        r = self.rows["trim"]
        self.t_cut = tool("Add cut", "scissors", "Split the segment under the playhead (C)", self.add_cut_here,
                          accent=True)
        self.t_prev = tool("", "caret-double-left", "Previous cut (,)", lambda: self._jump_cut(-1))
        self.t_next = tool("", "caret-double-right", "Next cut (.)", lambda: self._jump_cut(1))
        self.t_keep = tool("Remove", "x", "Keep or remove the segment under the playhead (X)", self._toggle_here)
        self.t_kept = tool("Kept only", "play-circle", "Play only the kept parts here. The other steps always "
                                                       "skip removed parts.", checkable=True)
        self.t_snap = tool("Snap", "crosshair-simple", "Snap cuts to keyframes: needed for exact lossless cuts "
                                                       "with Encode › Copy", checkable=True)
        self.t_snap.setChecked(True)
        r.add(self.t_cut); r.sep(); r.add(self.t_prev, self.t_next); r.sep(); r.add(self.t_keep); r.sep()
        r.add(self.t_kept, self.t_snap); r.finish()

        r = self.rows["crop"]
        self.c_rl = tool("", "arrow-counter-clockwise", "Rotate left (Shift+R)", lambda: self._rotate(-90))
        self.c_rr = tool("", "arrow-clockwise", "Rotate right (R)", lambda: self._rotate(90))
        self.c_hf = tool("Flip ↔", None, "Flip horizontally (H)", lambda on: self._flip("hflip", on), checkable=True)
        self.c_vf = tool("Flip ↕", None, "Flip vertically (V)", lambda on: self._flip("vflip", on), checkable=True)
        self.c_draw = tool("Draw crop", "crop", "Draw the crop on the video (D)", self._draw, checkable=True)
        self.c_shape = tool("Free ▾", None, "Crop shape")
        menu = QMenu(self.c_shape)
        for i, (label, _r) in enumerate(RATIOS):
            act = menu.addAction(label)
            act.triggered.connect(lambda _=False, i=i: self.panels["crop"].ratio.setCurrentIndex(i))
        self.c_shape.setMenu(menu); self.c_shape.setPopupMode(QToolButton.InstantPopup)
        self.c_detect = tool("", "magic-wand", "Detect black bars and crop them", lambda: self.panels["crop"]._detect())
        self.c_reset = tool("", "eraser", "Reset: no crop, rotation or flip", self._reset_transform)
        self.c_before = tool("Before", "eye", "Show the untouched source (B)", self._set_before, checkable=True)
        r.add(self.c_rl, self.c_rr); r.sep(); r.add(self.c_hf, self.c_vf); r.sep()
        r.add(self.c_draw, self.c_shape, self.c_detect, self.c_reset); r.sep(); r.add(self.c_before); r.finish()

        r = self.rows["motion"]
        self.m_scene = tool("Scene cut", "plus-circle", "Add a scene cut at the playhead, or remove the one there (S). "
                                                        "Interpolation never blends across a scene cut.",
                            self._toggle_scene, accent=True)
        self.m_prev = tool("", "caret-double-left", "Previous scene cut (,)", lambda: self._jump_scene(-1))
        self.m_next = tool("", "caret-double-right", "Next scene cut (.)", lambda: self._jump_scene(1))
        self.m_find = tool("Find cuts", "magic-wand", "Detect the scene cuts in the video",
                           lambda: self._find_scenes(force=True))
        r.add(self.m_scene); r.sep(); r.add(self.m_prev, self.m_next); r.sep(); r.add(self.m_find); r.finish()

        self.tool_stack = QStackedWidget()
        for row in self.rows.values():
            self.tool_stack.addWidget(row)

    def _on(self, step: str, fn) -> None:
        if self.step == step:
            fn()

    def _prev_next(self, d: int) -> None:
        if self.step == "trim":
            self._jump_cut(d)
        elif self.step == "motion":
            self._jump_scene(d)

    def _jump(self, times, d: int) -> None:
        t = self._joined_pos()
        if d > 0:
            tgt = next((x for x in times if x > t + 0.05), None)
        else:
            tgt = next((x for x in reversed(times) if x < t - 0.05), None)
        if tgt is not None:
            self._goto(tgt + 0.001, play=False)

    def _sync_tools(self) -> None:
        e, s = self.session.edit, self.session
        has = bool(e.clips) and self.cur_clip is not None
        c = e.crop
        for b, k in ((self.c_hf, "hflip"), (self.c_vf, "vflip")):
            b.blockSignals(True); b.setChecked(bool(c.get("on") and c.get(k))); b.blockSignals(False)
        for b in (self.t_cut, self.t_kept, self.t_snap, self.c_rl, self.c_rr, self.c_hf, self.c_vf, self.c_draw,
                  self.c_shape, self.c_reset, self.c_before):
            b.setEnabled(has)
        cuts = (to_cuts(e.trim, s.media().duration).get("cuts") or []) if has and e.trim.get("on") else []
        self.t_prev.setEnabled(bool(cuts)); self.t_next.setEnabled(bool(cuts))
        self.t_keep.setEnabled(has and len(self.segbar.segs) > 1)
        self.c_detect.setEnabled(has and self.panels["crop"].detect.isEnabled())
        self.c_shape.setText(self.panels["crop"].ratio.currentText() + " ▾")
        if has:
            self._update_keep(self._joined_pos())
        self._update_scene_ui()

    # ---------- navigation ----------
    def show_step(self, step: str) -> None:
        if step not in self.panels:
            return
        if step != "crop" and self.c_draw.isChecked():
            self.c_draw.setChecked(False)
        self.step = step
        title, sub = STEPS_UI[step]
        self.title.setText(title); self.sub.setText(sub)
        self.stack.setCurrentIndex(list(self.panels).index(step))
        self.trim_box.setVisible(step == "trim")
        self.tool_stack.setVisible(step in self.rows)
        if step in self.rows:
            self.tool_stack.setCurrentWidget(self.rows[step])
        self._head()
        self._sync_view()
        self._sync_chips(analyse(self.session))
        if step == "motion":
            self._find_scenes()
        self._sync_marks()

    def _head(self) -> None:
        tog = self.step in TOGGLES
        self.toggle.setVisible(tog); self.toggle_lbl.setVisible(tog)
        if tog:
            on = bool(getattr(self.session.edit, self.step).get("on"))
            self.toggle.set_state(on)
            self.toggle_lbl.setText("On" if on else "Off")

    def dots(self) -> dict:
        e = self.session.edit
        return {"trim": bool(e.trim.get("on")), "crop": bool(e.crop.get("on")), "motion": bool(e.motion.get("on")),
                "audio": (e.audio.get("mode") or "auto") != "auto", "encode": False,
                "output": bool(e.output.get("name") or e.output.get("out_dir"))}

    # ---------- chips ----------
    def _chips(self, an) -> list:
        e = self.session.edit
        if not an or self.step not in ORDER:
            return []
        before, out = ORDER[:ORDER.index(self.step)], []
        if "trim" in before and e.trim.get("on"):
            r = an["ranges"]
            out.append(("trim", "scissors", f"{len(r)} segments · {dur(an['dur'])}" if len(r) > 1
                        else f"Trimmed to {dur(an['dur'])}"))
        if "crop" in before and e.crop.get("on") and an["filters"]:
            bits, c = [], an["crop"]
            if isinstance(c, list):
                bits.append(f"Cropped {c[0]}×{c[1]}")
            elif c == "auto":
                bits.append("Black bars removed")
            if an["rot"]:
                bits.append(f"Rotated {an['rot']}°")
            if an["hf"]:
                bits.append("Flipped ↔")
            if an["vf"]:
                bits.append("Flipped ↕")
            w, h = an["size"]
            out.append(("crop", "crop", " · ".join(bits + [f"{w}×{h}"])))
        if "motion" in before and an["motion"]:
            fps = f"{float(an['fps'] or 0):.2f}".rstrip("0").rstrip(".")
            out.append(("motion", "sparkle", f"{an['slow']:g}× slower" if an["slow"] != 1 else f"{fps} fps"))
        if "audio" in before and (e.audio.get("mode") or "auto") != "auto":
            out.append(("audio", "speaker-high", an["aplan"]["label"]))
        if "encode" in before:
            out.append(("encode", "gauge", "Copy · lossless" if an["copy"]
                        else (an["enc"].label if an["enc"] else "No encoder")))
        return out

    def _sync_chips(self, an) -> None:
        self._chip_items = self._chips(an)
        self.chips.set_items(self._chip_items)
        self.chips.setVisible(bool(self._chip_items) and not self.player._maxed)

    # ---------- files ----------
    def browse(self) -> None:
        self.clips.browse()

    def add_files(self, paths) -> None:
        bad = self.session.add_clips(paths)
        if bad:
            QMessageBox.warning(self, "Ralenti", "These files aren't readable as video:\n" + "\n".join(bad))

    def reload_options(self) -> None:
        for panel in self.panels.values():
            panel.reload_options()
        self._changed("all")

    # ---------- session changes (coalesced) ----------
    def _changed(self, what: str) -> None:
        self._what.add(what)
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._refresh)

    def _refresh(self) -> None:
        self._pending = False
        what, self._what = self._what, set()
        s, e = self.session, self.session.edit
        if what & {"clips", "all"} and self.cur_clip not in e.clips:
            self.cur_clip = None
            if e.clips:
                self._show_clip(e.clips[0])
            else:
                self.player.unload()
        for panel in self.panels.values():
            panel.load(e)
        m = s.media()
        if m:
            tr = to_cuts(e.trim, m.duration) if e.trim.get("on") else {"cuts": [], "drop": []}
            self.segbar.view_fn = self.player.timeline.view if len(e.clips) == 1 else None
            self.segbar.set_data(segments(tr, m.duration), m.duration)
        else:
            self.segbar.set_data([], 0)
        self._sync_overlays()
        self._sync_view()
        self._sync_tools()
        self._head()
        an = analyse(s)
        self._sync_chips(an)
        msgs = messages(s, an)
        self.summary.update_rows(rows(s, an), msgs, estimate_html(s, an),
                                 bool(e.clips) and not any(lvl == "error" for lvl, _m in msgs))
        if self.step == "motion" and what & {"clips", "all", "motion"}:
            self._find_scenes()
        self._sync_marks()
        self.dots_changed.emit()

    # ---------- player ----------
    def _show_clip(self, path, select: bool = False) -> None:
        if not path or path == self.cur_clip:
            return
        self.cur_clip = path
        self.player.autoplay = False
        self.player.load(path, self.session.info(path))
        if select and path in self.session.edit.clips:
            self.clips.list.blockSignals(True)
            self.clips.list.setCurrentRow(self.session.edit.clips.index(path))
            self.clips.list.blockSignals(False)
        self._sync_overlays()
        self._sync_view()
        self._sync_marks()

    def _goto(self, t: float, play=None) -> None:
        s = self.session
        if not s.edit.clips:
            return
        path, local = s.clip_at(t)
        playing = self.player.player.playbackState() == PLAYING if play is None else play
        if path != self.cur_clip:
            self._show_clip(path, select=True)
            self.player._pending_seek = local
            self.player.autoplay = playing
        else:
            self.player.seek(local)
            if playing:
                self.player.player.play()

    def _joined_pos(self) -> float:
        return self.session.clip_offset(self.cur_clip) + self.player.position() if self.cur_clip else 0.0

    def _pos(self, ms: int) -> None:
        s = self.session
        if not self.cur_clip or not s.edit.clips:
            return
        t = s.clip_offset(self.cur_clip) + ms / 1000
        self.segbar.set_position(t)
        if self.step == "trim":
            self._update_keep(t)
        e = s.edit
        if not e.trim.get("on") or self.player.player.playbackState() != PLAYING:
            return
        if self.step == "trim" and not self.t_kept.isChecked():
            return
        ranges = kept_ranges(e.trim, s.media().duration)
        if not ranges or any(a - 0.03 <= t < b for a, b in ranges):
            return
        nxt = next((a for a, _b in ranges if a > t), None)
        if nxt is None:
            self.player.pause()
        else:
            self._goto(nxt + 0.001, play=True)

    def _media_status(self, st) -> None:
        clips = self.session.edit.clips
        if st != QMediaPlayer.EndOfMedia or self.cur_clip not in clips:
            return
        i = clips.index(self.cur_clip)
        if i + 1 >= len(clips):
            return
        off = self.session.clip_offset(clips[i + 1])
        target = off
        if self.session.edit.trim.get("on"):
            r = next(((a, b) for a, b in kept_ranges(self.session.edit.trim, self.session.media().duration)
                      if b > off + 1e-3), None)
            if r is None:
                return
            target = max(r[0], off)
        self._goto(target + 0.001, play=True)

    def _sync_overlays(self) -> None:
        e, s = self.session.edit, self.session
        if not self.cur_clip:
            return
        off, cd = s.clip_offset(self.cur_clip), s.info(self.cur_clip).duration
        spans = []
        if e.trim.get("on"):
            for a, b in kept_ranges(e.trim, s.media().duration):
                la, lb = max(0.0, a - off), min(cd, b - off)
                if lb > la + 1e-3:
                    spans.append((la, lb, True))
        self.player.set_takes(spans)
        crop = e.crop.get("crop")
        if isinstance(crop, list) and self.cur_clip == e.clips[0]:
            self.player.view.crop.set_crop(crop)

    def _sync_view(self) -> None:
        """Rotation, flip and crop applied to the picture: on Transform and every step after it."""
        s, e = self.session, self.session.edit
        if not self.cur_clip or not e.clips:
            return
        info, first = s.info(self.cur_clip), s.info(e.clips[0])
        c = e.crop
        on = bool(c.get("on")) and self.step != "trim" and not self._before
        drawing = self.c_draw.isChecked() and self.step == "crop"
        crop = c.get("crop") if on else None
        same = info and first and (info.width, info.height) == (first.width, first.height)
        rect = crop if isinstance(crop, list) and same and not drawing else None
        self.player.view.set_transform(int(c.get("rotate") or 0) % 360 if on else 0,
                                       bool(c.get("hflip")) and on, bool(c.get("vflip")) and on, rect)

    # ---------- trim ----------
    def _snap(self, t: float) -> float:
        if not self.t_snap.isChecked() or not self.cur_clip or not self.player.keys:
            return t
        off = self.session.clip_offset(self.cur_clip)
        local = t - off
        if not 0 <= local <= self.session.info(self.cur_clip).duration:
            return t
        k = min(self.player.keys, key=lambda x: abs(x - local))
        return off + k if abs(k - local) <= 1.5 else t

    def _set_trim(self, tr: dict) -> None:
        self.panels["trim"]._trim(tr)

    def add_cut_here(self) -> None:
        m = self.session.media()
        if not m or not self.cur_clip:
            return
        before = len(to_cuts(self.session.edit.trim, m.duration).get("cuts") or [])
        tr = add_cut(self.session.edit.trim, self._snap(self._joined_pos()), m.duration)
        if len(tr.get("cuts") or []) == before:
            notify(self, "A cut can't go there: too close to another cut or to the ends.", "info")
            return
        self._set_trim(tr)

    def _update_keep(self, t: float) -> None:
        keep = next((k for a, b, k in self.segbar.segs if a <= t < b), True)
        txt = "Remove" if keep else "Keep"
        if self.t_keep.text() != txt:
            self.t_keep.setText(txt)
            self.t_keep.setIcon(icon("x" if keep else "check", "muted", active="text", checked=None))

    def _toggle_here(self) -> None:
        m, segs = self.session.media(), self.segbar.segs
        if not m or len(segs) < 2:
            return
        t = self._joined_pos()
        i = next((k for k, (a, b, _x) in enumerate(segs) if a <= t < b), len(segs) - 1)
        self._set_trim(toggle(self.session.edit.trim, i, m.duration))

    def _jump_cut(self, d: int) -> None:
        m = self.session.media()
        if not m or not self.session.edit.trim.get("on"):
            return
        self._jump(sorted(float(c) for c in to_cuts(self.session.edit.trim, m.duration).get("cuts") or []), d)

    def _seg_toggled(self, i: int) -> None:
        m = self.session.media()
        if m:
            self._set_trim(toggle(self.session.edit.trim, i, m.duration))

    def _cut_removed(self, k: int) -> None:
        m = self.session.media()
        if m:
            self._set_trim(remove_cut(self.session.edit.trim, k, m.duration))

    def _cut_moved(self, k: int, t: float) -> None:
        m = self.session.media()
        if not m:
            return
        tr = to_cuts(self.session.edit.trim, m.duration)
        cuts = sorted(float(c) for c in tr.get("cuts") or [])
        if not 0 <= k < len(cuts):
            return
        lo = cuts[k - 1] + 0.05 if k > 0 else 0.05
        hi = cuts[k + 1] - 0.05 if k + 1 < len(cuts) else m.duration - 0.05
        cuts[k] = round(min(max(self._snap(t), lo), hi), 3)
        tr.update(cuts=cuts, on=True)
        self._set_trim(tr)

    # ---------- transform ----------
    def _rotate(self, d: int) -> None:
        if not self.session.edit.clips:
            return
        c = self.session.edit.crop
        cur = int(c.get("rotate") or 0) % 360 if c.get("on") else 0
        self.session.set("crop", on=True, rotate=(cur + d) % 360)
        if self.c_draw.isChecked():
            QTimer.singleShot(0, lambda: self._ratio(self.panels["crop"].ratio.currentData()))

    def _flip(self, key: str, on: bool) -> None:
        if self.session.edit.clips:
            self.session.set("crop", on=True, **{key: bool(on)})

    def _reset_transform(self) -> None:
        self.c_draw.setChecked(False)
        self.session.set("crop", on=False, crop="none", rotate=0, hflip=False, vflip=False, scale=1.0, max_h=0)

    def _set_before(self, on: bool) -> None:
        self._before = bool(on)
        if on and self.c_draw.isChecked():
            self.c_draw.setChecked(False)
        self._sync_view()

    def _draw(self, on: bool) -> None:
        ov = self.player.view.crop
        on = bool(on and self.cur_clip and self.step == "crop")
        if on:
            if self._before:
                self.c_before.setChecked(False)
            crop = self.session.edit.crop.get("crop")
            ov.set_crop(crop if isinstance(crop, list) else None)
            self.player.pause()
            if self.cur_clip != self.session.edit.clips[0]:
                notify(self, "The crop is drawn on the first clip; other clips are scaled to its size.", "info")
        ov.setVisible(on)
        if on:
            self._ratio(self.panels["crop"].ratio.currentData())
        self._sync_view()

    def _crop_drawn(self, crop) -> None:
        self.session.set("crop", on=True, crop=list(crop) if crop else "none")

    def _ratio(self, r) -> None:
        info = self.session.info(self.cur_clip) if self.cur_clip else None
        c = self.session.edit.crop
        rot = int(c.get("rotate") or 0) % 360 if c.get("on") else 0
        if r == "source":
            r = info.width / info.height if info and info.height else None
        elif r and rot in (90, 270):
            r = 1 / r          # the overlay works in source pixels: a rotated shape is the inverse ratio
        self.player.view.crop.set_ratio(r)
        self.c_shape.setText(self.panels["crop"].ratio.currentText() + " ▾")

    # ---------- scene cuts ----------
    def _scene_key(self):
        e = self.session.edit
        return tuple(e.clips), e.motion.get("scene") or "normal"

    def _scene_marks(self) -> list:
        m = self.session.edit.motion
        det = self._scenes.get(self._scene_key(), [])
        rem = [float(x) for x in m.get("cuts_remove") or []]
        out = [(t, "removed" if any(abs(t - r) <= SCENE_TOL for r in rem) else "detected") for t in det]
        out += [(float(t), "added") for t in m.get("cuts_add") or []]
        return sorted(out)

    def _find_scenes(self, force: bool = False) -> None:
        s, e = self.session, self.session.edit
        level = e.motion.get("scene") or "normal"
        key = self._scene_key()
        if not e.clips or level == "off" or not self.qm.tc.ffmpeg or self._scene_busy:
            return
        if key in self._scenes and not force:
            return
        ffmpeg = self.qm.tc.ffmpeg
        clips = [(s.clip_offset(c), s.info(c)) for c in e.clips]
        self._scene_busy = key
        self._update_scene_ui()

        def work():
            out = []
            for off, info in clips:
                out += [round(off + t, 3) for t in detect_times(ffmpeg, info, level)]
            return out

        def done(times):
            if not isValid(self):
                return
            self._scene_busy = None
            self._scenes[key] = times
            if isValid(self.session):
                self._update_scene_ui(); self._sync_marks()

        def fail(msg):
            if not isValid(self):
                return
            self._scene_busy = None
            notify(self, f"Scene detection failed: {msg}", "error")
            if isValid(self.session):
                self._update_scene_ui()

        run_async(work, done, fail)

    def _update_scene_ui(self) -> None:
        e = self.session.edit
        level = e.motion.get("scene") or "normal"
        ok = bool(e.clips) and self.cur_clip is not None and level != "off"
        for b in (self.m_scene, self.m_prev, self.m_next, self.m_find):
            b.setEnabled(ok)
        note, found = self.rows["motion"].note, self._scene_key() in self._scenes
        self.m_find.setText("Find again" if found else "Find cuts")
        if not e.clips:
            note.setText("")
        elif level == "off":
            note.setText("Scene cuts are off in the panel")
        elif self._scene_busy:
            note.setText("Finding scene cuts…")
            self.m_find.setEnabled(False)
        else:
            marks = self._scene_marks()
            n = sum(1 for _t, k in marks if k != "removed")
            add = sum(1 for _t, k in marks if k == "added")
            rem = sum(1 for _t, k in marks if k == "removed")
            extra = ", ".join(x for x in (f"{add} added" if add else "", f"{rem} ignored" if rem else "") if x)
            note.setText((f"{n} scene cut{'s' if n != 1 else ''}" + (f" · {extra}" if extra else ""))
                         if found or add else "Not scanned yet")

    def _sync_marks(self) -> None:
        tl = self.player.timeline
        if self.step != "motion" or not self.cur_clip:
            tl.set_marks([])
            return
        off, d = self.session.clip_offset(self.cur_clip), self.session.info(self.cur_clip).duration
        tl.set_marks([(t - off, k) for t, k in self._scene_marks() if off - 1e-3 <= t <= off + d + 1e-3])

    def _jump_scene(self, d: int) -> None:
        self._jump([t for t, k in self._scene_marks() if k != "removed"], d)

    def _toggle_scene(self) -> None:
        e = self.session.edit
        if not e.clips or not self.cur_clip or (e.motion.get("scene") or "normal") == "off":
            return
        t = round(self._joined_pos(), 3)
        add = [float(x) for x in e.motion.get("cuts_add") or []]
        rem = [float(x) for x in e.motion.get("cuts_remove") or []]
        det = self._scenes.get(self._scene_key(), [])

        def near(xs):
            if not xs:
                return None
            x = min(xs, key=lambda v: abs(v - t))
            return x if abs(x - t) <= PICK_TOL else None

        a, dd = near(add), near(det)
        if a is not None:
            add.remove(a); msg = "Scene cut removed"
        elif dd is not None:
            r = next((x for x in rem if abs(x - dd) <= SCENE_TOL), None)
            if r is None:
                rem.append(dd); msg = "Detected scene cut ignored: frames blend across it"
            else:
                rem.remove(r); msg = "Detected scene cut restored"
        else:
            add.append(t); msg = f"Scene cut added at {fmt_ts(t, False)}"
        self.session.set("motion", cuts_add=sorted(add), cuts_remove=sorted(rem))
        notify(self, msg, "info")

    def _maximized(self, on: bool) -> None:
        for w in (self.side, self.clips, self.title, self.sub, self.presets):
            w.setVisible(not on)
        if on:
            self.toggle.hide(); self.toggle_lbl.hide(); self.chips.hide()
        else:
            self._head()
            self.chips.setVisible(bool(self._chip_items))

    # ---------- queue ----------
    def queue(self) -> None:
        try:
            job = compile_job(self.qm.tc, self.settings, self.session.edit)
        except Exception as ex:
            QMessageBox.warning(self, "Ralenti", str(ex))
            return
        if not confirm_space(self, self.qm, [job]):
            return
        self.qm.add(job)
        notes = job.state.get("notes") or []
        notify(self, f"Added {Path(job.output).name} to the queue" + (f" · {notes[0]}" if notes else ""), "ok")
        if self.summary.autostart.isChecked():
            self.qm.start()
