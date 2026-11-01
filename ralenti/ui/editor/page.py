"""The editor: one video (one or more clips), a shared player, five step pages and one summary."""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from ...core.edit import compile_job
from ..space_check import confirm_space
from ..util import notify
from ..widgets.panel import options_scroll
from ..widgets.player import PlayerPanel
from .clips_bar import ClipsBar
from .panels import AudioPanel, CropPanel, MotionPanel, OutputPanel, TrimPanel
from .session import EditSession
from .summary import analyse, estimate_html, messages, rows
from .widgets import StepSummary, ToggleSwitch

STEPS_UI = {
    "trim": ("Trim", "Keep only the part you need. On its own it's instant and lossless."),
    "crop": ("Transform", "Crop, remove black bars, rotate, flip and resize. Applied before the AI, "
                                 "so it also makes it faster."),
    "motion": ("Interpolation", "Smooth slow motion and higher frame rates with AI interpolation."),
    "audio": ("Audio", "Keep the original sound, convert it, or remove it."),
    "output": ("Output", "How the video is encoded and where it's saved."),
}
TOGGLES = ("trim", "crop", "motion")


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
        self.toggle_lbl = QLabel(); self.toggle_lbl.setObjectName("Muted")
        self.toggle = ToggleSwitch(); self.toggle.toggled.connect(lambda on: self.session.set(self.step, on=on))
        head.addLayout(texts, 1); head.addWidget(self.toggle_lbl, 0, Qt.AlignVCenter)
        head.addWidget(self.toggle, 0, Qt.AlignVCenter)
        root.addLayout(head)

        self.clips = ClipsBar(s); self.clips.selected.connect(self._show_clip)
        root.addWidget(self.clips)

        body = QHBoxLayout(); body.setSpacing(18); root.addLayout(body, 1)
        p = self.player = PlayerPanel(qm, maximizable=True)
        p.empty_text = "Open a video to get started. You can also drop clips anywhere in the window."
        p._set_placeholder("film-strip", p.empty_text)
        p.ph_btn.setVisible(True)
        p.open_requested.connect(self.browse)
        p.range_changed.connect(self._range)
        p.view.crop.changed.connect(self._crop_drawn)
        p.maximize_toggled.connect(self._maximized)
        body.addWidget(p, 1)

        self.side = QWidget(); self.side.setFixedWidth(440)
        sv = QVBoxLayout(self.side); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(12)
        card = QFrame(); card.setObjectName("Card")
        cl = QVBoxLayout(card); cl.setContentsMargins(1, 1, 1, 1)
        self.stack = QStackedWidget(); cl.addWidget(self.stack)
        self.panels = {"trim": TrimPanel(s, p), "crop": CropPanel(s), "motion": MotionPanel(s),
                       "audio": AudioPanel(s), "output": OutputPanel(s)}
        for panel in self.panels.values():
            self.stack.addWidget(options_scroll(panel))
        self.panels["crop"].draw_toggled.connect(self._draw)
        self.panels["crop"].ratio_changed.connect(self._ratio)
        self.summary = StepSummary()
        self.summary.step_clicked.connect(lambda k: self.window().go(k))
        self.summary.add_clicked.connect(self.queue)
        sv.addWidget(card, 1); sv.addWidget(self.summary)
        body.addWidget(self.side)

        for keys, fn in (("Ctrl+Z", s.undo), ("Ctrl+Y", s.redo), ("Ctrl+Shift+Z", s.redo)):
            sc = QShortcut(QKeySequence(keys), self); sc.setContext(Qt.WidgetWithChildrenShortcut); sc.activated.connect(fn)
        s.changed.connect(self._changed)
        s.restore()
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
                "audio": (e.audio.get("mode") or "auto") != "auto", "output": False}

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
        self._sync_overlays()
        self._head()
        an = analyse(s)
        msgs = messages(s, an)
        self.summary.update_rows(rows(s, an), msgs, estimate_html(s, an),
                                 bool(e.clips) and not any(lvl == "error" for lvl, _m in msgs))
        self.dots_changed.emit()

    # ---------- player ----------
    def _show_clip(self, path) -> None:
        if not path or path == self.cur_clip:
            return
        self.cur_clip = path
        self.player.load(path, self.session.info(path))
        self._sync_overlays()

    def _sync_overlays(self) -> None:
        e = self.session.edit
        if not self.cur_clip:
            return
        off = self.session.clip_offset(self.cur_clip)
        cd = self.session.info(self.cur_clip).duration
        total = self.session.media().duration
        t = e.trim
        if t.get("on"):
            a = (t.get("start") or 0.0) - off
            b = (t["end"] if t.get("end") is not None else total) - off
            self.player.set_range(a if 0 < a < cd else None, b if 0 < b < cd else None)
        else:
            self.player.set_range(None, None)
        crop = e.crop.get("crop")
        if isinstance(crop, list) and self.cur_clip == e.clips[0]:
            self.player.view.crop.set_crop(crop)

    def _range(self, a, b) -> None:
        if not self.cur_clip:
            return
        s = self.session
        total = s.media().duration
        if len(s.edit.clips) == 1:
            if a is None and b is None:
                s.set("trim", on=False, start=None, end=None)
            else:
                s.set("trim", on=True, start=a, end=b)
            return
        off = s.clip_offset(self.cur_clip)
        cd = s.info(self.cur_clip).duration
        ja = off + (a or 0.0)
        jb = off + (b if b is not None else cd)
        s.set("trim", on=True, start=ja if ja > 0.001 else None, end=jb if jb < total - 0.001 else None)

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
        for w in (self.side, self.clips, self.title, self.sub):
            w.setVisible(not on)
        self._head() if not on else (self.toggle.hide(), self.toggle_lbl.hide())

    # ---------- queue ----------
    def queue(self) -> None:
        s = self.session
        try:
            job = compile_job(self.qm.tc, self.settings, s.edit)
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
