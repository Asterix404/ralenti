import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QVBoxLayout

from ...core.jobs import Job, Status
from ...core.progress import fmt_eta
from .. import theme
from ..icons import icon
from ..thumbs import rounded, thumbs
from ..util import clip, muted
from .anim import SmoothBar
from .step_track import StepTrack

KIND = {"interpolate": "Interpolate", "encode": "Encode", "cut": "Cut", "merge": "Merge", "preview": "Preview"}
STATUS_TEXT = {Status.QUEUED: "Waiting", Status.RUNNING: "Running", Status.DONE: "Done",
               Status.FAILED: "Failed", Status.CANCELLED: "Stopped", Status.PAUSED: "Paused"}
MENU = [("Show log", "log", "file-text"), ("Open folder", "folder", "folder-open"),
        ("Copy output path", "copy", "copy"), None,
        ("Resume / retry", "resume", "arrow-clockwise"),
        ("Discard progress and restart", "restart", "arrow-counter-clockwise"),
        ("Stop job · progress is kept", "cancel", "stop"), None,
        ("Move up", "up", "arrow-up"), ("Move down", "down", "arrow-down"), None,
        ("Remove", "remove", "trash")]
TW, TH = 120, 68


def job_source(job: Job) -> str | None:
    s = job.spec
    return s.get("src") or (s.get("files") or [None])[0]


def fmt_size(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"


class JobCard(QFrame):
    action = Signal(str, str)   # action key, job id

    def __init__(self, job: Job):
        super().__init__()
        self.setObjectName("Card")
        self.job_id = job.id
        self._src = job_source(job)
        self._bar_state = None
        root = QHBoxLayout(self); root.setContentsMargins(12, 12, 10, 12); root.setSpacing(14)
        self.thumb = QLabel(); self.thumb.setObjectName("Thumb"); self.thumb.setFixedSize(TW, TH)
        root.addWidget(self.thumb, 0, Qt.AlignTop)
        body = QVBoxLayout(); body.setSpacing(7); root.addLayout(body, 1)

        top = QHBoxLayout(); top.setSpacing(10)
        self.badge = QLabel(KIND.get(job.kind, job.kind)); self.badge.setObjectName("Badge")
        self.title = QLabel(); self.title.setStyleSheet("font-weight: 600; font-size: 10.5pt;")
        self.status = QLabel()
        menu_btn = QToolButton(); menu_btn.setIcon(icon("dots-three", "muted", active="text"))
        menu_btn.setIconSize(QSize(20, 20)); menu_btn.setPopupMode(QToolButton.InstantPopup)
        menu_btn.setCursor(Qt.PointingHandCursor)
        menu = QMenu(self)
        for item in MENU:
            if item is None:
                menu.addSeparator(); continue
            text, key, ic = item
            act = menu.addAction(icon(ic, "muted", active="accent", checked=None), text)
            act.triggered.connect(lambda _=False, k=key: self.action.emit(k, self.job_id))
        menu_btn.setMenu(menu)
        top.addWidget(self.badge); top.addWidget(self.title, 1)
        top.addWidget(self.status); top.addWidget(menu_btn)

        self.track = StepTrack()
        self.detail = QLabel()
        self.bar = SmoothBar()
        bottom = QHBoxLayout(); bottom.setSpacing(6)
        self.output = muted(QLabel()); self.metrics = muted(QLabel())
        self.resume_btn = self._quick("Resume", "resume", "arrow-clockwise")
        self.pause_btn = self._quick("Pause", "pause", "pause")
        self.live_btn = self._quick("Watch live", "live", "eye")
        self.play_btn = self._quick("Play", "play", "play-fill")
        self.folder_btn = self._quick("Show in folder", "folder", "folder-open")
        bottom.addWidget(self.output, 1); bottom.addWidget(self.metrics)
        for b in (self.live_btn, self.pause_btn, self.resume_btn, self.play_btn, self.folder_btn):
            bottom.addWidget(b)

        body.addLayout(top); body.addWidget(self.track); body.addWidget(self.detail)
        body.addWidget(self.bar); body.addLayout(bottom)

        thumbs().ready.connect(self._thumb_ready)
        self._set_thumb()
        self.update_job(job)

    def _quick(self, text: str, key: str, ic: str) -> QPushButton:
        b = QPushButton(text); b.setProperty("ghost", True)
        b.setIcon(icon(ic, "muted", active="text", checked=None)); b.setIconSize(QSize(15, 15))
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: self.action.emit(key, self.job_id))
        b.hide()
        return b

    def _set_thumb(self) -> None:
        self.thumb.setPixmap(rounded(thumbs().get(self._src), TW, TH, 8, self.devicePixelRatioF()))

    def _thumb_ready(self, path: str) -> None:
        if path == self._src:
            self._set_thumb()

    def _set_bar_state(self, state: str) -> None:
        if state != self._bar_state:
            self._bar_state = state
            self.bar.setProperty("state", state)
            self.bar.style().unpolish(self.bar); self.bar.style().polish(self.bar)

    @staticmethod
    def _result(job: Job) -> str:
        bits = []
        try:
            bits.append(fmt_size(os.path.getsize(job.output)))
        except OSError:
            bits.append("Output file not found")
        q, hist = job.state.get("q"), job.state.get("vmaf_history") or {}
        if q is not None and str(q) in hist:
            bits.append(f"VMAF {hist[str(q)]['vmaf']:.1f}")
        if job.finished and job.started:
            bits.append(f"took {fmt_eta(job.finished - job.started)}")
        return "  ·  ".join(bits)

    def update_job(self, job: Job) -> None:
        st = job.status
        self.title.setText(clip(job.label, 70)); self.title.setToolTip(job.label)
        colors = {Status.QUEUED: theme.MUTED, Status.RUNNING: theme.ACCENT, Status.DONE: theme.OK,
                  Status.FAILED: theme.DANGER, Status.CANCELLED: theme.WARN, Status.PAUSED: theme.WARN}
        step = job.step
        self.status.setText(step.label if st == Status.RUNNING and step else STATUS_TEXT[st])
        self.status.setStyleSheet(f"color: {colors[st]}; font-weight: 600;")
        self.track.set_steps(job.steps)

        color = theme.MUTED
        if st == Status.DONE:
            text, color = self._result(job), theme.TEXT
        elif st == Status.FAILED and job.error:
            text, color = job.error.strip().splitlines()[0], theme.DANGER
        elif warn := job.state.get("vmaf_warning"):
            text, color = warn, theme.WARN
        else:
            text = job.detail
        self.detail.setText(clip(text or " ", 130))
        self.detail.setToolTip(job.error or job.detail)
        self.detail.setStyleSheet(f"color: {color};")

        self.bar.setVisible(st != Status.DONE)
        self._set_bar_state(st.value)
        self.bar.set_smooth(job.progress * 1000)

        self.output.setText("→ " + clip(Path(job.output).name, 80))
        self.output.setToolTip(job.output)
        if st == Status.RUNNING:
            parts = [f"{job.fps} fps" if job.fps else "", job.speed,
                     f"ETA {job.eta}" if job.eta else "", f"{job.progress * 100:.0f}%"]
        elif st == Status.QUEUED and job.progress > 0:
            parts = [f"{job.progress * 100:.0f}% done · resumes here"]
        else:
            parts = []
        self.metrics.setText("  ·  ".join(p for p in parts if p))
        self.metrics.setVisible(bool(self.metrics.text()))
        self.play_btn.setVisible(st == Status.DONE)
        self.folder_btn.setVisible(st == Status.DONE)
        self.resume_btn.setVisible(st in (Status.FAILED, Status.CANCELLED, Status.PAUSED))
        self.pause_btn.setVisible(st in (Status.RUNNING, Status.QUEUED) and job.kind != "preview")
        self.live_btn.setVisible(job.kind == "interpolate" and bool(job.plan.get("segmented"))
                                 and st in (Status.RUNNING, Status.PAUSED))