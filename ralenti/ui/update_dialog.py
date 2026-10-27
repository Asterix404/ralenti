"""Update window: what's new since your version, then download, install and reopen."""
import subprocess
import threading

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from .. import __version__
from ..core import extensions, updates
from ..core.changelog import parse
from ..core.config import APP_DIR
from ..core.downloads import Cancelled
from ..core.jobs import Status
from . import theme
from .changelog_view import ChangelogView
from .downloads import mb
from .util import resource
from .widgets.anim import SmoothBar
from .widgets.fields import button

NOTES = {
    "installer": "Ralenti downloads the update, closes, installs it and reopens. Your settings, presets, queue "
                 "and extensions are kept.",
    "portable": "This is the portable version: the new zip is downloaded for you. Close Ralenti and unzip it "
                "over this folder.",
    "source": "You're running Ralenti from source: update with git pull instead.",
}


class _Sig(QObject):
    progress = Signal(float, float, float)
    stage = Signal(str)
    finished = Signal(bool, str)


class UpdateDialog(QDialog):
    def __init__(self, win, u):
        super().__init__(win)
        self.win, self.u = win, u
        self.settings, self.qm = win.settings, win.qm
        self.hint = extensions.load().update_hint
        self.mode = updates.install_mode()
        self.asset = u.assets.get(self.mode) if self.mode in ("installer", "portable") else None
        self._cancel, self._path, self._state = None, None, "idle"
        self.sig = _Sig()
        self.sig.progress.connect(self._on_progress)
        self.sig.stage.connect(lambda _s: self.status.setText("Verifying the download…"))
        self.sig.finished.connect(self._on_finished)
        self.setWindowTitle("Update available")
        self.setMinimumSize(720, 600)

        v = QVBoxLayout(self); v.setContentsMargins(26, 24, 26, 20); v.setSpacing(14)
        head = QHBoxLayout(); head.setSpacing(16)
        logo = QLabel()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(str(resource("ralenti.ico")))
        if not pm.isNull():
            pm = pm.scaled(int(56 * dpr), int(56 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
        texts = QVBoxLayout(); texts.setSpacing(3)
        title = QLabel(f"Ralenti {u.version} is available")
        title.setStyleSheet("font-size: 17pt; font-weight: 800;")
        bits = [f"You have {__version__}"]
        if u.date:
            bits.append(f"released {u.date}")
        if self.asset and self.asset.get("size"):
            bits.append(mb(self.asset["size"]))
        sub = QLabel(" · ".join(bits)); sub.setObjectName("Hint")
        texts.addStretch(1); texts.addWidget(title); texts.addWidget(sub); texts.addStretch(1)
        head.addWidget(logo); head.addLayout(texts, 1)
        v.addLayout(head)
        v.addWidget(ChangelogView(parse(u.notes)), 1)

        self.prog = QWidget()
        ph = QHBoxLayout(self.prog); ph.setContentsMargins(0, 0, 0, 0); ph.setSpacing(12)
        self.bar = SmoothBar()
        self.status = QLabel(); self.status.setObjectName("Hint"); self.status.setMinimumWidth(280)
        ph.addWidget(self.bar, 1); ph.addWidget(self.status)
        v.addWidget(self.prog)
        self.prog.hide()

        if self.hint:
            note = self.hint
        elif self.asset or self.mode == "source":
            note = NOTES[self.mode]
        else:
            note = "No download for your version was found in this release. Open the release page to get it."
        self.note = QLabel(note); self.note.setObjectName("Hint"); self.note.setWordWrap(True)
        v.addWidget(self.note)

        row = QHBoxLayout(); row.setSpacing(8)
        self.skip_btn = button("Skip this version", self._skip, "ghost")
        self.later_btn = button("Later", self.reject, "ghost")
        self.cancel_btn = button("Cancel download", self._cancel_dl, "ghost", icon="x")
        self.go_btn = button(self._go_text(), self._go, "accent", icon="download")
        row.addWidget(self.skip_btn); row.addStretch(1)
        for b in (self.later_btn, self.cancel_btn, self.go_btn):
            row.addWidget(b)
        v.addLayout(row)
        self.cancel_btn.hide()

    def _go_text(self) -> str:
        if self.hint or not self.asset:
            return "Open release page"
        return "Update now" if self.mode == "installer" else "Download"

    # ---------- download ----------
    def _go(self) -> None:
        if self._state == "ready":
            self._finish()
            return
        if self.hint or not self.asset:
            QDesktopServices.openUrl(QUrl(self.u.url))
            self.accept()
            return
        self._state = "downloading"
        self._cancel = threading.Event()
        self.prog.show(); self.bar.set_smooth(0); self._status("Starting the download…")
        for b in (self.skip_btn, self.later_btn, self.go_btn):
            b.hide()
        self.cancel_btn.show()
        dest = APP_DIR / "updates" / self.asset["name"]
        threading.Thread(target=self._run, args=(dest,), daemon=True, name="ralenti-update").start()

    def _run(self, dest) -> None:
        try:
            updates.download(self.asset, dest, lambda d, t, s: self.sig.progress.emit(d, t, s),
                             lambda st: self.sig.stage.emit(st), self._cancel)
            self.sig.finished.emit(True, str(dest))
        except Cancelled:
            self.sig.finished.emit(False, "Cancelled")
        except Exception as e:
            self.sig.finished.emit(False, str(e) or type(e).__name__)

    def _status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {theme.DANGER};" if error else "")

    def _on_progress(self, d: float, t: float, speed: float) -> None:
        self.bar.set_smooth(d / t * 1000 if t else 0)
        self._status(f"Downloading {mb(d)}" + (f" of {mb(t)}" if t else "") + (f" · {speed:.1f} MB/s" if speed else ""))

    def _on_finished(self, ok: bool, msg: str) -> None:
        self.cancel_btn.hide()
        self.go_btn.show()
        if ok:
            self._path, self._state = msg, "ready"
            self.bar.set_smooth(1000)
            if self.mode == "installer":
                self._status("Ready to install.")
                self.go_btn.setText("Restart and update")
            else:
                self._status("Downloaded.")
                self.go_btn.setText("Show in folder")
            self.later_btn.show()
            return
        self._state = "idle"
        for b in (self.skip_btn, self.later_btn):
            b.show()
        if msg == "Cancelled":
            self.prog.hide()
            self.go_btn.setText(self._go_text())
        else:
            self._status(f"Download failed: {msg}", error=True)
            self.go_btn.setText("Try again")

    def _cancel_dl(self) -> None:
        if self._cancel:
            self._cancel.set()

    # ---------- install ----------
    def _finish(self) -> None:
        if self.mode == "portable":
            subprocess.Popen(f'explorer /select,"{self._path}"')
            self.accept()
            return
        qm = self.qm
        if qm.is_running:
            r = QMessageBox.question(self, "Ralenti", "The queue is running. Ralenti will stop it to install the "
                                     "update; unfinished jobs resume where they stopped.\n\nInstall now?")
            if r != QMessageBox.StandardButton.Yes:
                return
            running = [j.id for j in qm.jobs if j.status == Status.RUNNING]
            qm.stop(); qm.wait(10)
            for jid in running:
                qm.retry(jid)
        qm.save()
        try:
            updates.launch_installer(self._path)
        except Exception as e:
            self._status(f"Couldn't start the installer: {e}", error=True)
            return
        self.accept()
        self.win.close()
        QApplication.instance().quit()

    def _skip(self) -> None:
        self.settings.skipped_version = self.u.version
        self.settings.save()
        self.reject()

    def reject(self):
        if self._state == "downloading":
            self._cancel_dl()
        super().reject()
