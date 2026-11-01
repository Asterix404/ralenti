"""The editor: one video (one or more clips), a player tailored to each step, six step pages, presets and
one summary. Removed segments are skipped while playing (in Trim only when asked)."""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from ...core import edit_presets
from ...core.edit import compile_job
from ...core.segments import add_cut, kept_ranges, remove_cut, segments, to_cuts, toggle
from ..space_check import confirm_space
from ..util import notify
from ..widgets.panel import options_scroll
from ..widgets.player import PlayerPanel
from .clips_bar import ClipsBar
from .panels import AudioPanel, CropPanel, EncodePanel, MotionPanel, OutputPanel, TrimPanel
from .presets_ui import PresetBar
from .session import EditSession
from .summary import analyse, estimate_html, messages, rows
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
TOGGLES = ("trim", "crop", "motion")
PLAYING = QMediaPlayer.PlayingState


class EditorPage(QWidget):
    dots_changed = Signal()

    def __init__(self, qm, settings):
        super().__init__()
        self.qm, self.settings = qm, settings
        self.step, self.cur_clip, self._what, self._pending = "motion", None, set(), False
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
        self.trim_box = QWidget()
        tb = QVBoxLayout(self.trim_box); tb.setContentsMargins(0, 2, 0, 0); tb.setSpacing(4)
        h = QLabel("Click a segment to keep or remove it · drag a cut to move it · right-click a cut to delete it · "
                   "C adds a cut at the playhead")
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
        self.panels["trim"].add_cut.connect(self.add_cut_here)
        self.panels["trim"].goto.connect(lambda t: self._goto(t, play=False))
        self.panels["crop"].draw_toggled.connect(self._draw)
        self.panels["crop"].ratio_changed.connect(self._ratio)
        self.summary = StepSummary()
        self.summary.step_clicked.connect(lambda k: self.window().go(k))
        self.summary.add_clicked.connect(self.queue)
        sv.addWidget(card, 1); sv.addWidget(self.summary)
        body.addWidget(self.side)

        for keys, fn in (("Ctrl+Z", s.undo), ("Ctrl+Y", s.redo), ("Ctrl+Shift+Z", s.redo),
                         ("C", lambda: self.step == "trim" and self.add_cut_here())):
            sc = QShortcut(QKeySequence(keys), self); sc.setContext(Qt.WidgetWithChildrenShortcut); sc.activated.connect(fn)
        s.changed.connect(self._changed)
        s.restore()
        if not s.edit.clips and (d := edit_presets.get_default()) and (r := edit_presets.get(d)):
            edit_presets.apply(s.edit, r)
        self._changed("all")
        self.show_step("motion")

    # ---------- navigation ----------
    def show_step(self, step: str) -> None:
        if step not in self.panels:
            return
        if step != "crop" and self.panels["crop"].draw.isChecked():
            self.panels["crop"].draw.setChecked(False)
        self.step = step
        title, sub = STEPS_UI[step]
        self.title.setText(title); self.sub.setText(sub)
        self.stack.setCurrentIndex(list(self.panels).index(step))
        self.trim_box.setVisible(step == "trim")
        self._head()

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
        self._head()
        an = analyse(s)
        msgs = messages(s, an)
        self.summary.update_rows(rows(s, an), msgs, estimate_html(s, an),
                                 bool(e.clips) and not any(lvl == "error" for lvl, _m in msgs))
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
        e = s.edit
        if not e.trim.get("on") or self.player.player.playbackState() != PLAYING:
            return
        if self.step == "trim" and not self.panels["trim"].kept_only.isChecked():
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

    # ---------- trim ----------
    def _snap(self, t: float) -> float:
        if not self.panels["trim"].snap.isChecked() or not self.cur_clip or not self.player.keys:
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
    def _draw(self, on: bool) -> None:
        ov = self.player.view.crop
        if on and self.cur_clip:
            crop = self.session.edit.crop.get("crop")
            ov.set_crop(crop if isinstance(crop, list) else None)
            self.player.pause()
            if self.cur_clip != self.session.edit.clips[0]:
                notify(self, "The crop is drawn on the first clip; other clips are scaled to its size.", "info")
        ov.setVisible(bool(on and self.cur_clip))

    def _crop_drawn(self, crop) -> None:
        self.session.set("crop", on=True, crop=list(crop) if crop else "none")

    def _ratio(self, r) -> None:
        if r == "source":
            info = self.session.info(self.cur_clip) if self.cur_clip else None
            r = info.width / info.height if info and info.height else None
        self.player.view.crop.set_ratio(r)

    def _maximized(self, on: bool) -> None:
        for w in (self.side, self.clips, self.title, self.sub, self.presets):
            w.setVisible(not on)
        if on:
            self.toggle.hide(); self.toggle_lbl.hide()
        else:
            self._head()

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
