from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QVBoxLayout

from ...core.jobs import Status
from ..icons import icon
from ..thumbs import rounded, thumbs
from ..util import clip, muted
from .anim import SmoothBar
from .job_card import job_source
from .step_track import StepTrack


def stop_menu(parent, qm) -> QMenu:
    m = QMenu(parent)
    a = m.addAction(icon("timer", "muted", active="accent", checked=None), "Stop after the running jobs finish")
    a.triggered.connect(lambda: qm.stop(kill_current=False))
    b = m.addAction(icon("stop", "muted", active="accent", checked=None), "Stop now · progress is kept")
    b.triggered.connect(lambda: qm.stop(kill_current=True))
    return m


class QueueStrip(QFrame):
    """Bottom bar: the running job(s) with thumbnail and step chips, progress, Start/Pause/Resume and Stop."""
    open_queue = Signal()

    def __init__(self, qm, bridge):
        super().__init__()
        self.qm = qm
        self._thumb_key = None
        self.setObjectName("QueueStrip")
        self.setFixedHeight(64)
        h = QHBoxLayout(self); h.setContentsMargins(16, 8, 14, 8); h.setSpacing(14)
        self.thumb = QLabel(); self.thumb.setFixedSize(64, 36)
        txt = QVBoxLayout(); txt.setSpacing(0)
        self.title = QLabel(); self.title.setStyleSheet("font-weight: 600;")
        self.detail = muted(QLabel())
        for w in (self.title, self.detail):
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            txt.addWidget(w)
        self.track = StepTrack(); self.track.setMinimumWidth(260)
        self.bar = SmoothBar(); self.bar.setFixedWidth(150)
        self.pct = muted(QLabel()); self.pct.setMinimumWidth(110)
        self.main = QPushButton(); self.main.setIconSize(QSize(16, 16)); self.main.setCursor(Qt.PointingHandCursor)
        self.main.clicked.connect(self._main)
        self.stop = QPushButton("Stop"); self.stop.setIcon(icon("stop", "text", checked=None))
        self.stop.setIconSize(QSize(16, 16)); self.stop.setMenu(stop_menu(self, qm))
        open_btn = QPushButton("Open queue")
        open_btn.setIcon(icon("list-checks", "muted", active="text")); open_btn.setIconSize(QSize(16, 16))
        open_btn.clicked.connect(self.open_queue.emit)
        h.addWidget(self.thumb); h.addLayout(txt, 2); h.addWidget(self.track, 3, Qt.AlignVCenter)
        for w in (self.bar, self.pct, self.main, self.stop, open_btn):
            h.addWidget(w)
        self.thumb.hide(); self.track.hide()
        bridge.event.connect(lambda *_: self.refresh())
        thumbs().ready.connect(lambda _p: self._reset_thumb())
        self.refresh()

    def _reset_thumb(self):
        self._thumb_key = None
        self.refresh()

    def _main(self):
        qm = self.qm
        if not qm.is_running:
            qm.start()
        elif qm.all_paused:
            qm.resume_all()
        else:
            qm.pause_all()

    def _set_main(self, text: str, ic: str, accent: bool, enabled: bool) -> None:
        self.main.setText(text)
        self.main.setIcon(icon(ic, "on_accent" if accent else "text", checked=None))
        self.main.setProperty("accent", accent)
        self.main.style().unpolish(self.main); self.main.style().polish(self.main)
        self.main.setEnabled(enabled)

    def refresh(self):
        qm = self.qm
        running = qm.is_running
        live = qm.live_ids()
        jobs = list(qm.jobs)
        mains = [j for j in jobs if j.id in live and j.kind != "preview"]
        cur = mains[0] if mains else next((j for j in jobs if j.id in live), None)
        queued = sum(j.status == Status.QUEUED and j.kind != "preview" for j in jobs)
        if cur:
            src = job_source(cur)
            if src != self._thumb_key:
                pm = thumbs().get(src)
                self.thumb.setPixmap(rounded(pm, 64, 36, 6, self.devicePixelRatioF()))
                self._thumb_key = src if pm is not None else None
            self.thumb.show()
            more = f"  +{len(mains) - 1} more" if len(mains) > 1 else ""
            self.title.setText(clip(cur.label, 50) + more)
            bits = ["Paused" if cur.status == Status.PAUSED else cur.detail]
            if queued:
                bits.append(f"{queued} queued")
            if running:
                bits.append(qm.concurrency_label())
            self.detail.setText(clip(" · ".join(b for b in bits if b), 120))
            self.track.set_steps(cur.steps); self.track.show()
            self.bar.set_smooth(cur.progress * 1000)
            self.pct.setText(f"{cur.progress * 100:.0f}%" + (f"  ·  ETA {cur.eta}" if cur.eta else ""))
        else:
            self.thumb.hide(); self._thumb_key = None
            self.track.set_steps([]); self.track.hide()
            self.title.setText("Idle")
            self.detail.setText(f"{queued} queued" if queued else "Queue is empty")
            self.bar.set_smooth(0); self.pct.setText("")
        if not running:
            self._set_main("Start", "play-fill", True, queued > 0)
        elif qm.all_paused:
            self._set_main("Resume", "play-fill", True, True)
        else:
            self._set_main("Pause", "pause", False, True)
        self.stop.setEnabled(running)
