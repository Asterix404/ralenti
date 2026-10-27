"""Watch a job while it renders: plays the finished parts in order and picks up new ones.
The parts have no sound; the finished video (with sound) plays once the job is done."""
import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QToolButton, QVBoxLayout

from ..core.jobs import Status
from .icons import icon
from .util import notify
from .widgets.fields import button

_open: dict = {}


def parts(job) -> tuple[list[str], int]:
    seg_dir = Path(job.plan.get("tmp", "")) / "seg"
    files = []
    for i in sorted(job.state.get("seg_done") or []):
        f = seg_dir / f"{i:05d}.mkv"
        if i != len(files) or not f.exists():
            break
        files.append(str(f))
    return files, len(job.state.get("segments") or [])


class LiveDialog(QDialog):
    def __init__(self, parent, qm, job):
        super().__init__(parent)
        self.qm, self.jid = qm, job.id
        self.files, self.idx, self.waiting, self.final = [], -1, False, False
        self.setWindowTitle(f"Live · {job.label}")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(1040, 660)
        v = QVBoxLayout(self); v.setContentsMargins(16, 16, 16, 12); v.setSpacing(10)
        self.video = QVideoWidget(); self.video.setStyleSheet("background: #000;")
        self.title = QLabel(); self.title.setObjectName("Section")
        self.status = QLabel(); self.status.setObjectName("Hint"); self.status.setWordWrap(True)
        row = QHBoxLayout(); row.setSpacing(6)
        self.prev_b = self._tb("caret-left", "Previous part", self._prev)
        self.play_b = self._tb("pause-fill", "Play / pause", self._toggle)
        self.next_b = self._tb("caret-right", "Next part", lambda: self._next(False))
        for b in (self.prev_b, self.play_b, self.next_b):
            row.addWidget(b)
        row.addSpacing(8); row.addWidget(self.title); row.addStretch(1)
        row.addWidget(button("Open in external player", self._external, "ghost", icon="arrow-square-out"))
        row.addWidget(button("Close", self.close, "ghost"))
        v.addWidget(self.video, 1); v.addLayout(row); v.addWidget(self.status)
        self.player = QMediaPlayer(self)
        self.out = QAudioOutput(self)
        self.player.setAudioOutput(self.out)
        self.player.setVideoOutput(self.video)
        self.player.mediaStatusChanged.connect(self._media)
        self.player.playbackStateChanged.connect(self._icon)
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(1500)
        self.refresh()

    def _tb(self, name, tip, fn):
        b = QToolButton(); b.setIcon(icon(name, "muted", active="text", checked=None)); b.setIconSize(QSize(20, 20))
        b.setToolTip(tip); b.setCursor(Qt.PointingHandCursor); b.clicked.connect(lambda: fn())
        return b

    def refresh(self) -> None:
        job = self.qm.get(self.jid)
        if job is None:
            self.status.setText("This job was removed from the queue.")
            return
        if job.status == Status.DONE and os.path.exists(job.output):
            if not self.final:
                self.final = True
                self.player.setSource(QUrl.fromLocalFile(job.output)); self.player.play()
                self.title.setText("Finished")
                self.status.setText("The job is done: this is the final video, with sound.")
                self.prev_b.setEnabled(False); self.next_b.setEnabled(False)
            return
        self.files, total = parts(job)
        n = len(self.files)
        if self.idx < 0 and n:
            self._load(0)
        elif self.waiting and self.idx < n:
            self._load(self.idx)
        state = {Status.FAILED: "the job failed", Status.PAUSED: "paused", Status.CANCELLED: "stopped"}.get(
            job.status, f"rendering part {n + 1}" if n < total else "joining the parts")
        if not n:
            self.title.setText("Waiting for the first part…")
        elif self.waiting:
            self.title.setText(f"Waiting for part {self.idx + 1} of {total}…")
        else:
            self.title.setText(f"Part {self.idx + 1} of {total}")
        self.status.setText(f"{n} of {total} parts ready · {state}. Each part covers about 10 seconds of the "
                            "source. No sound until the job finishes.")
        self.prev_b.setEnabled(self.idx > 0)
        self.next_b.setEnabled(self.idx + 1 < n)

    def _load(self, i: int) -> None:
        self.idx, self.waiting = i, False
        self.player.setSource(QUrl.fromLocalFile(self.files[i]))
        self.player.play()

    def _media(self, s) -> None:
        if s == QMediaPlayer.EndOfMedia and not self.final:
            self._next(True)

    def _next(self, auto: bool) -> None:
        if self.final:
            return
        if self.idx + 1 < len(self.files):
            self._load(self.idx + 1)
        elif auto:
            self.idx += 1
            self.waiting = True
            self.refresh()

    def _prev(self) -> None:
        if not self.final and self.idx > 0 and self.files:
            self._load(min(self.idx - 1, len(self.files) - 1))

    def _toggle(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _icon(self, *_):
        playing = self.player.playbackState() == QMediaPlayer.PlayingState
        self.play_b.setIcon(icon("pause-fill" if playing else "play-fill", "text", checked=None))

    def _external(self) -> None:
        job = self.qm.get(self.jid)
        if job and job.status == Status.DONE and os.path.exists(job.output):
            os.startfile(job.output)
            return
        if not self.files:
            notify(self, "No part is ready yet.", "info")
            return
        m3u = Path(self.files[0]).parent / "live.m3u"
        m3u.write_text("#EXTM3U\n" + "\n".join(self.files) + "\n", encoding="utf-8")
        os.startfile(m3u)
        notify(self, f"Opened {len(self.files)} parts. Reopen to include newer ones.", "info")

    def closeEvent(self, e):
        self.timer.stop()
        self.player.stop()
        self.player.setSource(QUrl())
        _open.pop(self.jid, None)
        super().closeEvent(e)


def open_live(parent, qm, job) -> None:
    d = _open.get(job.id)
    if d is not None:
        d.raise_(); d.activateWindow()
        return
    d = LiveDialog(parent, qm, job)
    _open[job.id] = d
    d.show()
