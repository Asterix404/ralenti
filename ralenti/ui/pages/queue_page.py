import os
import time

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ...core.config import LOGS_DIR
from ...core.jobs import Status
from ...core.progress import fmt_eta
from ..icons import icon, pixmap
from ..util import mono_font, notify, reveal
from ..widgets.anim import SmoothBar
from ..widgets.choice import RichCombo
from ..widgets.segmented import Segmented
from ..widgets.empty import EmptyState
from ..widgets.fields import button, page_header
from ..widgets.job_card import JobCard
from ..winapi import shutdown_now, sleep_now

WHEN_DONE = [("none", "Do nothing", "", "The PC stays on when the queue finishes."),
             ("close", "Close Ralenti", "", "Quits Ralenti after the last job, with 15 seconds to cancel."),
             ("sleep", "Sleep", "", "Puts the PC to sleep after the last job, with 60 seconds to cancel."),
             ("shutdown", "Shut down", "Overnight",
              "Shuts the PC down after the last job, with 60 seconds to cancel.")]


class LogDialog(QDialog):
    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.path = LOGS_DIR / f"{job.id}.log"
        self.setWindowTitle(f"Log · {job.label}"); self.resize(1000, 600)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit(); self.text.setReadOnly(True); self.text.setFont(mono_font())
        row = QHBoxLayout(); row.addStretch(1)
        row.addWidget(button("Refresh", self.load, "ghost", icon="arrow-clockwise"))
        row.addWidget(button("Open in editor", lambda: self.path.exists() and os.startfile(self.path),
                             icon="arrow-square-out"))
        lay.addWidget(self.text); lay.addLayout(row)
        self.load()

    def load(self):
        try:
            data = self.path.read_text(encoding="utf-8", errors="replace")[-400_000:]
        except OSError:
            data = "(no log yet)"
        self.text.setPlainText(data)
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())


class CountdownDialog(QDialog):
    def __init__(self, action: str, parent=None, seconds: int = 60):
        super().__init__(parent)
        self.action, self.left = action, seconds
        self.setWindowTitle("Ralenti"); self.setModal(True); self.setMinimumWidth(440)
        v = QVBoxLayout(self); v.setContentsMargins(24, 22, 24, 20); v.setSpacing(10)
        ic = QLabel(); ic.setPixmap(pixmap({"sleep": "moon", "close": "x-circle"}.get(action, "power"), 40, "accent"))
        self.title = QLabel(); self.title.setObjectName("Section")
        hint = QLabel("The queue has finished. Cancel if you're still using the PC.")
        hint.setObjectName("Hint"); hint.setWordWrap(True)
        row = QHBoxLayout(); row.addStretch(1)
        row.addWidget(button("Cancel", self.reject, "ghost"))
        row.addWidget(button("Do it now", self.accept, "accent"))
        v.addWidget(ic); v.addWidget(self.title); v.addWidget(hint); v.addSpacing(6); v.addLayout(row)
        self.timer = QTimer(self); self.timer.setInterval(1000); self.timer.timeout.connect(self._tick)
        self.timer.start(); self._render()

    def _tick(self):
        self.left -= 1
        if self.left <= 0:
            self.timer.stop(); self.accept()
        else:
            self._render()

    def _render(self):
        verb = {"sleep": "Going to sleep", "close": "Closing Ralenti"}.get(self.action, "Shutting down")
        self.title.setText(f"{verb} in {self.left} s")


class QueuePage(QWidget):
    add_requested = Signal()

    def __init__(self, qm, bridge):
        super().__init__()
        self.qm, self.cards = qm, {}
        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(12)

        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(page_header("Queue", "Jobs run one after another. Failed jobs resume from the "
                                   "step that failed."), 1)
        whenbox = QVBoxLayout(); whenbox.setSpacing(4)
        lbl = QLabel("WHEN DONE"); lbl.setObjectName("FieldLabel")
        self.when = RichCombo(popup_width=380); self.when.setMinimumWidth(170)
        for key, title, tag, blurb in WHEN_DONE:
            self.when.add(title, key, blurb, tag)
        whenbox.addWidget(lbl); whenbox.addWidget(self.when)
        self.start_btn = button("Start", qm.start, "accent", icon="play-fill")
        self.pause_btn = button("Pause", self._pause_toggle, icon="pause")
        self.stop_btn = button("Stop", lambda: None, icon="stop")
        from ..widgets.queue_strip import stop_menu
        self.stop_btn.setMenu(stop_menu(self, qm))
        clear = button("Clear finished", qm.clear_finished, "ghost", icon="broom")
        head.addLayout(whenbox)
        conc = QVBoxLayout(); conc.setSpacing(4)
        clbl = QLabel("AT ONCE"); clbl.setObjectName("FieldLabel")
        self.conc = Segmented([("Auto", "auto", "Measures speed and system load, and runs as many jobs at once as "
                                "actually helps"), ("1", 1, ""), ("2", 2, ""), ("3", 3, ""), ("4", 4, "")])
        self.conc.setFixedWidth(240)
        _c = str(getattr(qm.settings, "concurrency", "auto") or "auto")
        self.conc.set_value("auto" if _c == "auto" else int(_c))
        self.conc.changed.connect(self._set_conc)
        conc.addWidget(clbl); conc.addWidget(self.conc)
        head.addLayout(conc)
        for b in (self.start_btn, self.pause_btn, self.stop_btn, clear):
            head.addWidget(b, 0, Qt.AlignBottom)
        root.addLayout(head)

        self.overview = QFrame(); self.overview.setObjectName("Overview")
        ov = QVBoxLayout(self.overview); ov.setContentsMargins(18, 14, 18, 14); ov.setSpacing(8)
        top = QHBoxLayout()
        self.ov_title = QLabel(); self.ov_title.setStyleSheet("font-weight: 700; font-size: 11pt;")
        self.ov_detail = QLabel(); self.ov_detail.setObjectName("Muted")
        top.addWidget(self.ov_title); top.addStretch(1); top.addWidget(self.ov_detail)
        self.ov_bar = SmoothBar()
        ov.addLayout(top); ov.addWidget(self.ov_bar)
        root.addWidget(self.overview)

        self.empty = EmptyState("list-checks", "Nothing in the queue",
                                "Add videos on Interpolate, Trim, Merge or Encode. Jobs show up here "
                                "with live progress.", "Add videos")
        self.empty.action.connect(self.add_requested.emit)
        root.addWidget(self.empty, 1)

        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        body = QWidget(); body.setObjectName("ScrollBody")
        self.list = QVBoxLayout(body); self.list.setSpacing(10); self.list.setContentsMargins(0, 0, 6, 0)
        self.list.addStretch(1)
        self.scroll.setWidget(body); root.addWidget(self.scroll, 1)

        self.ticker = QTimer(self); self.ticker.setInterval(1000)
        self.ticker.timeout.connect(self._update_overview)

        for j in qm.jobs:
            self._add(j)
        bridge.event.connect(self.on_event)
        self._sync()

    def _add(self, job):
        card = JobCard(job); card.action.connect(self.on_action)
        self.list.insertWidget(self.list.count() - 1, card)
        self.cards[job.id] = card

    def _reorder(self):
        for i, j in enumerate(self.qm.jobs):
            if card := self.cards.get(j.id):
                self.list.removeWidget(card); self.list.insertWidget(i, card)

    def on_event(self, ev, job):
        if ev == "added":
            self._add(job)
        elif ev == "removed":
            if card := self.cards.pop(job.id, None):
                card.deleteLater()
        elif job is not None and job.id in self.cards:
            self.cards[job.id].update_job(job)
        self._sync()
        if ev == "idle":
            self._maybe_power()

    def _sync(self):
        running = self.qm.is_running
        queued = any(j.status == Status.QUEUED for j in self.qm.jobs)
        self.start_btn.setEnabled(not running and queued)
        self.stop_btn.setEnabled(running)
        self.pause_btn.setVisible(running)
        _p = self.qm.all_paused
        self.pause_btn.setText("Resume" if _p else "Pause")
        self.pause_btn.setIcon(icon("play-fill" if _p else "pause", "text", checked=None))
        has = bool(self.cards)
        self.empty.setVisible(not has); self.scroll.setVisible(has)
        if running and not self.ticker.isActive():
            self.ticker.start()
        elif not running:
            self.ticker.stop()
        self._update_overview()

    def _update_overview(self):
        frac, done, n = self.qm.run_progress()
        if n == 0:
            self.overview.hide(); return
        self.overview.show()
        run = self.qm.run_jobs()
        failed = sum(j.status == Status.FAILED for j in run)
        running = self.qm.is_running
        queued = any(j.status == Status.QUEUED for j in run)
        if running:
            title = f"Processing · {done} of {n} done"
        elif queued:
            title = f"Ready · {done} of {n} done"
        else:
            title = f"Finished · {n} job{'s' if n != 1 else ''}"
        bits = []
        if running:
            rem, complete = self.qm.remaining_estimate()
            if rem:
                end = time.strftime("%H:%M", time.localtime(time.time() + rem))
                bits += [f"≈ {fmt_eta(rem)}{'' if complete else '+'} left", f"done around {end}"]
            else:
                bits.append("Estimating time…")
        if failed:
            bits.append(f"{failed} failed")
        bits.append(f"{frac * 100:.0f}%")
        self.ov_title.setText(title)
        self.ov_detail.setText("  ·  ".join(bits))
        self.ov_bar.set_smooth(frac * 1000)

    def _maybe_power(self):
        action = self.when.currentData()
        if action == "none" or self.qm.user_stopped or any(j.status == Status.QUEUED for j in self.qm.jobs):
            return
        QApplication.alert(self.window())
        if CountdownDialog(action, self, 15 if action == "close" else 60).exec() == QDialog.Accepted:
            self.when.setCurrentIndex(0)
            self.qm.save()
            if action == "close":
                self.window().close()
            else:
                sleep_now() if action == "sleep" else shutdown_now(30)
        else:
            notify(self, "Cancelled · the PC stays on", "info")

    def on_action(self, key, job_id):
        job = self.qm.get(job_id)
        if not job:
            return
        if key == "log":
            LogDialog(job, self).exec()
        elif key == "folder":
            reveal(job.output)
        elif key == "play":
            if os.path.exists(job.output):
                os.startfile(job.output)
            else:
                notify(self, "The output file was moved or deleted", "warn")
        elif key == "copy":
            QApplication.clipboard().setText(job.output)
            notify(self, "Output path copied", "ok")
        elif key == "live":
            from ..live_view import open_live
            open_live(self, self.qm, job)
        elif key == "pause":
            self.qm.pause(job_id)
        elif key == "resume" and job.status == Status.PAUSED:
            self.qm.resume(job_id)
        elif key in ("resume", "restart"):
            self.qm.retry(job_id, from_scratch=key == "restart")
            if not self.qm.is_running:
                self.qm.start()
        elif key == "cancel" and job.status in (Status.RUNNING, Status.QUEUED):
            self.qm.cancel_job(job_id)
        elif key in ("up", "down"):
            self.qm.move(job_id, -1 if key == "up" else 1); self._reorder()
        elif key == "remove" and not self.qm.remove(job_id):
            QMessageBox.information(self, "Ralenti", "Stop the job before removing it.")
        self._sync()



def _pause_toggle(self) -> None:
    self.qm.resume_all() if self.qm.all_paused else self.qm.pause_all()
    self._sync()


def _set_conc(self, v) -> None:
    self.qm.settings.concurrency = str(v)
    self.qm.settings.save()
    self.qm.scaler.reset()
    notify(self, "Ralenti will adjust how many jobs run at once" if v == "auto" else f"Up to {v} at once", "info")


QueuePage._pause_toggle = _pause_toggle
QueuePage._set_conc = _set_conc
