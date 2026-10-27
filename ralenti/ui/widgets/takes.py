"""Previews tab: length (incl. whole clip), render button and the take list (newest first).
The ⇄ button on a card compares that take against the one in the player."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ...core.jobs import Status
from .. import theme
from ..icons import icon
from ..thumbs import rounded
from .anim import SmoothBar
from .empty import EmptyState
from .fields import Field, button
from .panel import options_scroll
from .segmented import Segmented

LENGTHS = [("2 s", 2.0, "2 seconds of source"), ("3 s", 3.0, "3 seconds of source"),
           ("5 s", 5.0, "5 seconds of source"), ("10 s", 10.0, "10 seconds of source"),
           ("Whole", 0.0, "The whole clip, or the trimmed range if you set one")]
TW, TH = 112, 63


class TakeCard(QFrame):
    clicked = Signal(str)
    deleted = Signal(str)
    rerender = Signal(str)
    versus = Signal(str)

    def __init__(self, jid: str):
        super().__init__()
        self.jid, self._thumb = jid, None
        self.setObjectName("TakeCard"); self.setCursor(Qt.PointingHandCursor)
        h = QHBoxLayout(self); h.setContentsMargins(8, 8, 6, 8); h.setSpacing(10)
        self.thumb = QLabel(); self.thumb.setFixedSize(TW, TH)
        texts = QVBoxLayout(); texts.setSpacing(2)
        self.title = QLabel(); self.title.setObjectName("TakeTitle")
        self.sub = QLabel(); self.sub.setObjectName("Hint")
        self.state = QLabel(); self.state.setObjectName("TakeState")
        self.bar = SmoothBar()
        for w in (self.title, self.sub, self.state, self.bar):
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            texts.addWidget(w)
        texts.addStretch(1)
        btns = QVBoxLayout(); btns.setSpacing(2)
        self.vs_btn = self._tool("versus", "Compare against the take in the player", self.versus)
        self.re_btn = self._tool("arrow-clockwise", "Re-render with the current settings", self.rerender)
        self.del_btn = self._tool("trash", "Delete this take", self.deleted)
        for b in (self.vs_btn, self.re_btn, self.del_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        h.addWidget(self.thumb); h.addLayout(texts, 1); h.addLayout(btns)

    def _tool(self, name, tip, sig):
        b = QToolButton(); b.setIcon(icon(name, "muted", active="text", checked=None))
        b.setIconSize(QSize(16, 16)); b.setToolTip(tip); b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda: sig.emit(self.jid))
        return b

    def update_take(self, job, title: str, sub: str, stale: bool, active: bool, versus: bool = False) -> None:
        self.title.setText(title); self.sub.setText(sub)
        self.setToolTip(f"{title}\n{sub}")
        st = job.status
        busy = st in (Status.QUEUED, Status.RUNNING)
        self.bar.setVisible(busy)
        if busy:
            self.bar.set_smooth(job.progress * 1000)
            text, color = ("Waiting…" if st == Status.QUEUED else f"Rendering {job.progress * 100:.0f}%"), theme.ACCENT
        elif st == Status.FAILED:
            text, color = "Failed · see the Queue log", theme.DANGER
        elif st == Status.CANCELLED:
            text, color = "Cancelled", theme.WARN
        elif versus:
            text, color = "Left side of the comparison", theme.ACCENT
        elif stale:
            text, color = "Settings changed", theme.WARN
        else:
            text, color = ("Showing in the player" if active else "Ready · click to view"), theme.OK
        self.state.setText(text); self.state.setStyleSheet(f"color: {color};")
        self.re_btn.setVisible(stale or st in (Status.FAILED, Status.CANCELLED))
        self.vs_btn.setVisible(st == Status.DONE and not active)
        self.vs_btn.setToolTip("Stop comparing" if versus else "Compare against the take in the player")
        thumb = job.state.get("thumb")
        if thumb != self._thumb or self.thumb.pixmap().isNull():
            self._thumb = thumb
            pm = QPixmap(thumb) if thumb else None
            self.thumb.setPixmap(rounded(pm if pm and not pm.isNull() else None, TW, TH, 7, self.devicePixelRatioF()))
        for prop, val in (("active", active), ("versus", versus)):
            if self.property(prop) != val:
                self.setProperty(prop, val)
                self.style().unpolish(self); self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.jid)


class TakePanel(QWidget):
    render_requested = Signal(float)
    length_changed = Signal(float)
    take_clicked = Signal(str)
    take_deleted = Signal(str)
    take_rerender = Signal(str)
    take_versus = Signal(str)

    def __init__(self, length: float = 3.0):
        super().__init__()
        self.setObjectName("OptionsBody"); self.setAttribute(Qt.WA_StyledBackground)
        self.cards: dict[str, TakeCard] = {}
        self._has_file = False
        v = QVBoxLayout(self); v.setContentsMargins(18, 14, 18, 16); v.setSpacing(12)
        self.len = Segmented(LENGTHS)
        if not self.len.set_value(float(length)):
            self.len.set_value(3.0)
        self.len.changed.connect(self._len_changed)
        self.len_field = Field("Length", self.len)
        v.addWidget(self.len_field)
        self.render_btn = button("Render at playhead", lambda: self.render_requested.emit(self.length()),
                                 "accent", icon="play-circle")
        self.render_btn.setMinimumHeight(38)
        v.addWidget(self.render_btn)
        self.status = QLabel(); self.status.setObjectName("Hint"); self.status.setWordWrap(True)
        v.addWidget(self.status)
        self.empty = EmptyState("sparkle", "No previews yet",
                                "Move the playhead to an interesting moment, then render a few seconds. "
                                "Each render is kept as a take you can compare in the player.")
        v.addWidget(self.empty, 1)
        body = QWidget(); body.setObjectName("OptionsBody"); body.setAttribute(Qt.WA_StyledBackground)
        self.list = QVBoxLayout(body); self.list.setContentsMargins(0, 0, 4, 0); self.list.setSpacing(8)
        self.list.addStretch(1)
        self.scroll = options_scroll(body); self.scroll.hide()
        v.addWidget(self.scroll, 1)
        self._len_changed(self.length())
        self.set_has_file(False)

    def length(self) -> float:
        v = self.len.value()
        return 3.0 if v is None else float(v)

    def _len_changed(self, v) -> None:
        whole = float(v) <= 0
        self.render_btn.setText("Render the whole clip" if whole else "Render at playhead")
        self.len_field.set_hint("Renders the whole clip, or your trimmed range. Long clips take a while."
                                if whole else "Seconds of source to render. Slow motion makes the take longer.")
        self.length_changed.emit(float(v))

    def set_has_file(self, on: bool) -> None:
        self._has_file = on
        self.render_btn.setEnabled(on)
        self._status([])

    def set_takes(self, items) -> None:
        """items: [(job, title, sub, stale, active[, versus])] in render order. Shown newest first."""
        ids = {it[0].id for it in items}
        for jid in [j for j in self.cards if j not in ids]:
            self.cards.pop(jid).deleteLater()
        for it in items:
            job = it[0]
            card = self.cards.get(job.id)
            if card is None:
                card = TakeCard(job.id)
                card.clicked.connect(self.take_clicked.emit)
                card.deleted.connect(self.take_deleted.emit)
                card.rerender.connect(self.take_rerender.emit)
                card.versus.connect(self.take_versus.emit)
                self.list.insertWidget(0, card)
                self.cards[job.id] = card
            card.update_take(*it)
        self.scroll.setVisible(bool(items))
        self.empty.setVisible(not items)
        self._status(items)

    def _status(self, items) -> None:
        if not self._has_file:
            text = "Open a video to preview it."
        elif not items:
            text = ""
        else:
            busy = sum(1 for it in items if it[0].status in (Status.QUEUED, Status.RUNNING))
            n = len(items)
            text = (f"{n} take{'s' if n != 1 else ''} · marked under the timeline. "
                    f"Use ⇄ on a take to compare it with the one in the player.")
            if busy:
                text += f" Rendering {busy}."
        self.status.setText(text)
        self.status.setVisible(bool(text))