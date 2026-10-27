"""Live page: the job's player on the left, and what's being watched on the right (job picker, part grid,
details)."""
from fractions import Fraction

from PySide6.QtCore import QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QStackedWidget, QToolTip,
                               QVBoxLayout, QWidget)

from ...core.jobs import Status
from .. import theme
from ..bridge import QueueBridge
from ..live_view import LiveView
from ..thumbs import rounded, thumbs
from ..util import clip, fmt_ts
from ..widgets.choice import BLURB, TAG, RichCombo
from ..widgets.empty import EmptyState
from ..widgets.fields import page_header
from ..widgets.job_card import job_source

STATE = {Status.RUNNING: ("Rendering", "Running"), Status.PAUSED: ("Paused", "Paused"),
         Status.DONE: ("Finished", "Done"), Status.FAILED: ("Failed", "Failed"),
         Status.CANCELLED: ("Stopped", "Stopped"), Status.QUEUED: ("Waiting", "Queued")}


class PartsGrid(QWidget):
    clicked = Signal(int)
    CELL, GAP = 16, 4

    def __init__(self):
        super().__init__()
        self.parts, self.rendering, self.cur, self.phase = [], -1, -1, 0.0
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.anim = QVariantAnimation(self)
        self.anim.setStartValue(0.0); self.anim.setEndValue(1.0); self.anim.setDuration(1200); self.anim.setLoopCount(-1)
        self.anim.valueChanged.connect(self._tick)

    def _tick(self, v) -> None:
        self.phase = float(v)
        self.update()

    def set_parts(self, parts, rendering: int, cur: int) -> None:
        resize = len(parts) != len(self.parts)
        self.parts, self.rendering, self.cur = parts, rendering, cur
        if rendering >= 0 and self.anim.state() != QVariantAnimation.Running:
            self.anim.start()
        elif rendering < 0:
            self.anim.stop()
        if resize:
            self._fit()
        self.update()

    def _cols(self) -> int:
        return max(1, (self.width() + self.GAP) // (self.CELL + self.GAP))

    def _fit(self) -> None:
        rows = -(-len(self.parts) // self._cols()) if self.parts else 1
        self.setFixedHeight(max(self.CELL, rows * (self.CELL + self.GAP) - self.GAP))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit()

    def _rect(self, i: int) -> QRectF:
        c, s = self._cols(), self.CELL + self.GAP
        return QRectF((i % c) * s, (i // c) * s, self.CELL, self.CELL)

    def _index(self, pos) -> int:
        return next((i for i in range(len(self.parts)) if self._rect(i).contains(pos)), -1)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        for i, (_s, _d, _p, ok) in enumerate(self.parts):
            r = self._rect(i)
            if ok:
                c = QColor(theme.ACCENT)
            elif i == self.rendering:
                c = QColor(theme.WARN); c.setAlphaF(0.3 + 0.6 * (1 - abs(self.phase - 0.5) * 2))
            else:
                c = QColor(theme.SURFACE3)
            p.setPen(Qt.NoPen); p.setBrush(c); p.drawRoundedRect(r, 4, 4)
            if i == self.cur:
                p.setPen(QPen(QColor(theme.TEXT), 2)); p.setBrush(Qt.NoBrush)
                p.drawRoundedRect(r.adjusted(-1.5, -1.5, 1.5, 1.5), 5, 5)

    def mouseMoveEvent(self, e):
        i = self._index(e.position())
        if i < 0:
            QToolTip.hideText(); return
        s, d, _p, ok = self.parts[i]
        state = "ready · click to watch" if ok else ("rendering now" if i == self.rendering else "not rendered yet")
        QToolTip.showText(e.globalPosition().toPoint(),
                          f"Part {i + 1} · {fmt_ts(s, False)}–{fmt_ts(s + d, False)} · {state}", self)

    def mousePressEvent(self, e):
        i = self._index(e.position())
        if i >= 0 and self.parts[i][3]:
            self.clicked.emit(i)


def _dot(color: str, text: str) -> str:
    return f"<span style='color:{color}'>■</span>&nbsp;{text}"


class LivePage(QWidget):
    def __init__(self, qm, settings):
        super().__init__()
        self.qm, self._ids = qm, ()
        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(8)
        root.addWidget(page_header("Live", "Watch an interpolation job while it renders, with sound. "
                                   "Jump to any moment that's already rendered."))
        self.stack = QStackedWidget()
        self.empty = EmptyState("eye", "Nothing to watch yet",
                                "Start an interpolation job, then watch it here while it renders, with sound. "
                                "You can jump to any part that's already done.", "Open queue")
        self.empty.action.connect(lambda: self.window().go(self.window().queue_index))

        body = QWidget(); bh = QHBoxLayout(body); bh.setContentsMargins(0, 0, 0, 0); bh.setSpacing(18)
        self.view = LiveView(qm); self.view.changed.connect(self._side)
        bh.addWidget(self.view, 1)
        side = QFrame(); side.setObjectName("Card"); side.setFixedWidth(340)
        sv = QVBoxLayout(side); sv.setContentsMargins(18, 16, 18, 18); sv.setSpacing(12)

        def label(t):
            l = QLabel(t); l.setObjectName("FieldLabel")
            return l

        self.picker = RichCombo(380)
        self.picker.activated.connect(lambda i: self.watch(self.picker.itemData(i)))
        head = QHBoxLayout(); head.setSpacing(12)
        self.thumb = QLabel(); self.thumb.setFixedSize(112, 63)
        tx = QVBoxLayout(); tx.setSpacing(2)
        self.title = QLabel(); self.title.setObjectName("SummaryTitle"); self.title.setWordWrap(True)
        self.sub = QLabel(); self.sub.setObjectName("Hint")
        tx.addStretch(1); tx.addWidget(self.title); tx.addWidget(self.sub); tx.addStretch(1)
        head.addWidget(self.thumb); head.addLayout(tx, 1)
        self.grid = PartsGrid(); self.grid.clicked.connect(self._part_clicked)
        legend = QLabel("&nbsp;&nbsp;".join([_dot(theme.ACCENT, "ready"), _dot(theme.WARN, "rendering"),
                                             _dot(theme.SURFACE3, "waiting")]))
        legend.setObjectName("Hint"); legend.setTextFormat(Qt.RichText)
        self.stats, self.vals = QGridLayout(), {}
        self.stats.setHorizontalSpacing(14); self.stats.setVerticalSpacing(8); self.stats.setColumnStretch(1, 1)
        for i, k in enumerate(("Rendered", "Speed", "Time left", "Output", "Sound")):
            key = QLabel(k.upper()); key.setObjectName("InfoKey")
            val = QLabel("–"); val.setWordWrap(True)
            self.stats.addWidget(key, i, 0, Qt.AlignTop); self.stats.addWidget(val, i, 1)
            self.vals[k] = val
        for w in (label("WATCHING"), self.picker):
            sv.addWidget(w)
        sv.addLayout(head)
        sv.addSpacing(4); sv.addWidget(label("PARTS")); sv.addWidget(self.grid); sv.addWidget(legend)
        sv.addSpacing(4); sv.addWidget(label("DETAILS")); sv.addLayout(self.stats); sv.addStretch(1)
        bh.addWidget(side)

        self.stack.addWidget(self.empty); self.stack.addWidget(body)
        root.addWidget(self.stack, 1)
        self.bridge = QueueBridge(qm); self.bridge.event.connect(lambda *_: self._jobs())
        self.timer = QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self._side)
        thumbs().ready.connect(lambda _p: self._side())
        self._jobs()

    # ---------- jobs ----------
    def _candidates(self) -> list:
        out = [j for j in self.qm.jobs if j.kind == "interpolate" and j.plan.get("segmented")
               and j.status in (Status.RUNNING, Status.PAUSED)]
        cur = self.qm.get(self.view.jid) if self.view.jid else None
        if cur and cur not in out:
            out.insert(0, cur)
        return out

    def _blurb(self, j) -> str:
        return f"{STATE.get(j.status, ('', ''))[0]} · {j.progress * 100:.0f}%"

    def _jobs(self) -> None:
        cands = self._candidates()
        ids = tuple(j.id for j in cands)
        if ids != self._ids:
            self._ids = ids
            self.picker.blockSignals(True); self.picker.clear()
            for j in cands:
                self.picker.add(clip(j.label, 48), j.id, self._blurb(j), STATE.get(j.status, ("", ""))[1])
            if self.view.jid:
                self.picker.select(self.view.jid)
            self.picker.blockSignals(False)
        if not self.view.jid and cands and self.isVisible():
            self.watch(cands[0].id)
            return
        self.stack.setCurrentIndex(1 if (cands or self.view.jid) else 0)

    def watch(self, jid) -> None:
        if not jid:
            return
        self.view.watch(jid)
        self._ids = ()
        self._jobs()
        self.stack.setCurrentIndex(1)
        self._side()

    def _part_clicked(self, i: int) -> None:
        if self.view.mode == "parts" and i < len(self.view.parts):
            self.view.seek(self.view.parts[i][0] + 0.01, quiet=True)

    # ---------- side panel ----------
    def _side(self) -> None:
        job = self.qm.get(self.view.jid) if self.view.jid else None
        if job is None:
            return
        v = self.view
        self.thumb.setPixmap(rounded(thumbs().get(job_source(job)), 112, 63, 8, self.devicePixelRatioF()))
        self.title.setText(clip(job.label, 44)); self.title.setToolTip(job.label)
        self.sub.setText(self._blurb(job))
        if v.mode == "parts":
            rendering = next((i for i, x in enumerate(v.parts) if not x[3]), -1) if job.status == Status.RUNNING else -1
            self.grid.set_parts(v.parts, rendering, v.cur)
        else:
            self.grid.set_parts([(0.0, v.total, "", True)] if v.parts else [], -1, 0 if v.parts else -1)
        end = v.avail_end()
        s, st = job.spec, job.state
        fps = f" · {float(Fraction(st['fps'])):.3f} fps" if st.get("fps") else ""
        a_step = next((x for x in job.steps if x.kind == "audio"), None)
        sound = ("In the final file" if v.mode == "final" else "With sound" if v.audio_path
                 else "No audio" if a_step is None else "Processing…")
        self.vals["Rendered"].setText(f"<b>{fmt_ts(end, False)}</b> of {fmt_ts(v.total, False)}")
        self.vals["Speed"].setText(f"{job.fps} fps" if job.fps and job.status == Status.RUNNING else "–")
        self.vals["Time left"].setText(job.eta or ("Done" if job.status == Status.DONE else "–"))
        self.vals["Output"].setText(f"{s.get('model', '')} · {float(s.get('slowmo', 1)):g}× slow-mo{fps}")
        self.vals["Sound"].setText(sound)
        for i in range(self.picker.count()):
            j = self.qm.get(self.picker.itemData(i))
            if j:
                self.picker.setItemData(i, self._blurb(j), BLURB)
                self.picker.setItemData(i, STATE.get(j.status, ("", ""))[1], TAG)

    def showEvent(self, e):
        super().showEvent(e)
        self.view.set_active(True)
        self.timer.start()
        self._jobs()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.view.set_active(False)
        self.timer.stop()
