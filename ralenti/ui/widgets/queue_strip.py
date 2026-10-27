from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout

from ...core.jobs import Status
from ..icons import icon
from ..thumbs import rounded, thumbs
from ..util import clip, muted
from .anim import SmoothBar
from .job_card import job_source
from .step_track import StepTrack


class QueueStrip(QFrame):
    """Bottom bar: current job with thumbnail, its step chips, progress and queue controls."""
    open_queue = Signal()

    def __init__(self, qm, bridge):
        super().__init__()
        self.qm = qm
        self._thumb_key = None
        self.setObjectName("QueueStrip")
        self.setFixedHeight(64)
        h = QHBoxLayout(self); h.setContentsMargins(16, 8, 14, 8); h.setSpacing(14)
        self.thumb = QLabel(); self.thumb.setFixedSize(64, 36); self.thumb.hide()
        txt = QVBoxLayout(); txt.setSpacing(0)
        self.title = QLabel(); self.title.setStyleSheet("font-weight: 600;")
        self.detail = muted(QLabel())
        for w in (self.title, self.detail):
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            txt.addWidget(w)
        self.track = StepTrack(); self.track.setMinimumWidth(260); self.track.hide()
        self.bar = SmoothBar(); self.bar.setFixedWidth(160)
        self.pct = muted(QLabel()); self.pct.setMinimumWidth(110)
        self.toggle = QPushButton(); self.toggle.setIconSize(QSize(16, 16))
        self.toggle.clicked.connect(self._toggle)
        open_btn = QPushButton("Open queue")
        open_btn.setIcon(icon("list-checks", "muted", active="text")); open_btn.setIconSize(QSize(16, 16))
        open_btn.clicked.connect(self.open_queue.emit)
        h.addWidget(self.thumb); h.addLayout(txt, 2); h.addWidget(self.track, 3, Qt.AlignVCenter)
        for w in (self.bar, self.pct, self.toggle, open_btn):
            h.addWidget(w)
        bridge.event.connect(lambda *_: self.refresh())
        thumbs().ready.connect(lambda _p: self._reset_thumb())
        self.refresh()

    def _reset_thumb(self):
        self._thumb_key = None
        self.refresh()

    def _toggle(self):
        self.qm.stop() if self.qm.is_running else self.qm.start()

    def refresh(self):
        running = self.qm.is_running
        jobs = list(self.qm.jobs)
        cur = (next((j for j in jobs if j.status == Status.RUNNING and j.kind != "preview"), None)
               or next((j for j in jobs if j.status == Status.RUNNING), None))
        queued = sum(j.status == Status.QUEUED and j.kind != "preview" for j in jobs)
        if cur:
            src = job_source(cur)
            if src != self._thumb_key:
                pm = thumbs().get(src)
                self.thumb.setPixmap(rounded(pm, 64, 36, 6, self.devicePixelRatioF()))
                self._thumb_key = src if pm is not None else None
            self.thumb.show()
            self.title.setText(clip(cur.label, 60))
            bits = [cur.detail] if cur.detail else []
            if queued:
                bits.append(f"{queued} more queued")
            self.detail.setText(clip(" · ".join(bits), 110))
            self.track.set_steps(cur.steps); self.track.show()
            self.bar.set_smooth(cur.progress * 1000)
            self.pct.setText(f"{cur.progress * 100:.0f}%" + (f"  ·  ETA {cur.eta}" if cur.eta else ""))
        else:
            self.thumb.hide(); self._thumb_key = None
            self.track.set_steps([]); self.track.hide()
            self.title.setText("Idle")
            self.detail.setText(f"{queued} queued" if queued else "Queue is empty")
            self.bar.set_smooth(0); self.pct.setText("")
        if running:
            self.toggle.setText("Stop"); self.toggle.setIcon(icon("stop", "text", checked=None))
        else:
            self.toggle.setText("Start"); self.toggle.setIcon(icon("play-fill", "on_accent", checked=None))
        self.toggle.setProperty("accent", not running)
        self.toggle.style().unpolish(self.toggle); self.toggle.style().polish(self.toggle)
        self.toggle.setEnabled(running or queued > 0)