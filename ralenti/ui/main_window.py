from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from .. import __version__
from ..core.jobs import Status
from .bridge import QueueBridge
from .icons import icon, pixmap
from .pages.encode_page import EncodePage
from .pages.interpolate_page import InterpolatePage
from .pages.merge_page import MergePage
from .pages.queue_page import QueuePage
from .pages.settings_page import SettingsPage
from .pages.trim_page import TrimPage
from .pages.about_page import AboutPage
from .pages.downloads_page import DownloadsPage
from .pages.live_page import LivePage
from .util import clip, resource
from .widgets.anim import AnimatedStack, Toast
from .widgets.drop_zone import paths_from_mime
from .widgets.queue_strip import QueueStrip
from .widgets.gpu_meter import GpuMeter
from .winapi import TaskbarProgress

NAV = [("Interpolate", "sparkle"), ("Trim", "scissors"), ("Merge", "stack"),
       ("Encode", "gauge"), ("Queue", "list-checks"), ("Live", "eye"), ("Downloads", "download"), ("Settings", "gear-six"), ("About", "info")]
DROP_TEXT = {0: "Drop to add to Interpolate", 1: "Drop to open in Trim",
             2: "Drop to add to Merge", 3: "Drop to add to Encode"}
DRAG_EVENTS = (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop, QEvent.DragLeave)


class DropOverlay(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("DropOverlay")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        v = QVBoxLayout(self); v.setAlignment(Qt.AlignCenter); v.setSpacing(10)
        ic = QLabel(); ic.setPixmap(pixmap("tray-arrow-down", 56, "accent")); ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet("background: transparent;")
        self.title = QLabel(); self.title.setObjectName("DropTitle"); self.title.setAlignment(Qt.AlignCenter)
        hint = QLabel("Video files and whole folders are accepted"); hint.setObjectName("Hint")
        hint.setAlignment(Qt.AlignCenter); hint.setStyleSheet("background: transparent;")
        v.addWidget(ic); v.addWidget(self.title); v.addWidget(hint)
        self.hide()


class MainWindow(QMainWindow):
    def __init__(self, settings, qm):
        super().__init__()
        self.settings, self.qm = settings, qm
        self._toasts: list[Toast] = []
        self.setWindowTitle(f"Ralenti {__version__}")
        from ..core import extensions
        if extensions.load().name:
            self.setWindowTitle(f"Ralenti {__version__} · {extensions.load().name}")
        self.resize(1320, 860); self.setMinimumSize(1040, 680)
        self.setAcceptDrops(True)
        self.bridge = QueueBridge(qm)
        self.taskbar = TaskbarProgress(self)

        self.interp = InterpolatePage(qm, settings)
        self.trim_page = TrimPage(qm, settings)
        self.merge_page = MergePage(qm, settings)
        self.encode_page = EncodePage(qm, settings)
        self.queue_page = QueuePage(qm, self.bridge)
        self.settings_page = SettingsPage(qm, settings)
        self.downloads_page = DownloadsPage(qm, settings)
        self.live_page = LivePage(qm, settings)
        for pg in (self.interp, self.merge_page, self.encode_page):
            self.settings_page.saved.connect(pg.reload_options)
        self.queue_page.add_requested.connect(lambda: (self.go(0), self.interp.browse()))
        self.settings_page.setup_requested.connect(self.run_setup)
        self.settings_page.open_downloads.connect(lambda: self.go([n for n, _ in NAV].index("Downloads")))
        from .downloads import downloads
        downloads(settings).finished.connect(self._tools_changed)
        pages = [self.interp, self.trim_page, self.merge_page, self.encode_page,
                 self.queue_page, self.live_page, self.downloads_page, self.settings_page, AboutPage(qm, settings)]
        self.queue_index = 4

        root = QWidget(); root.setObjectName("Root")
        h = QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        sidebar = QFrame(); sidebar.setObjectName("Sidebar"); sidebar.setFixedWidth(224)
        sv = QVBoxLayout(sidebar); sv.setContentsMargins(0, 0, 0, 14); sv.setSpacing(2)

        brand = QWidget()
        bl = QHBoxLayout(brand); bl.setContentsMargins(20, 22, 20, 20); bl.setSpacing(12)
        logo = QLabel()
        ico = resource("ralenti.ico")
        dpr = self.devicePixelRatioF()
        if ico.exists():
            pm = QPixmap(str(ico)).scaled(int(34 * dpr), int(34 * dpr), Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
        else:
            logo.setPixmap(pixmap("sparkle", 30, "accent"))
        names = QVBoxLayout(); names.setSpacing(0)
        t = QLabel("Ralenti"); t.setObjectName("Brand")
        s = QLabel("Motion & encoding studio"); s.setObjectName("BrandSub")
        names.addWidget(t); names.addWidget(s)
        bl.addWidget(logo); bl.addLayout(names, 1)
        sv.addWidget(brand)

        self.nav = QButtonGroup(self); self.nav.setExclusive(True)
        self.stack = AnimatedStack()
        for i, ((name, ic), page) in enumerate(zip(NAV, pages)):
            if name == "Downloads":
                sv.addStretch(1)
            btn = QPushButton(f"  {name}"); btn.setObjectName("Nav"); btn.setCheckable(True)
            btn.setIcon(icon(ic, "muted", active="text", checked="accent"))
            btn.setIconSize(QSize(18, 18)); btn.setCursor(Qt.PointingHandCursor)
            self.nav.addButton(btn, i); sv.addWidget(btn); self.stack.addWidget(page)
        self.nav.idClicked.connect(self.go)
        self.gpu_meter = GpuMeter(qm)
        gw = QWidget(); gl = QVBoxLayout(gw); gl.setContentsMargins(12, 6, 12, 0)
        gl.addWidget(self.gpu_meter); sv.addWidget(gw)
        ver = QLabel(f"v{__version__}"); ver.setObjectName("Hint"); ver.setContentsMargins(24, 10, 0, 0)
        sv.addWidget(ver)

        content = QWidget()
        cv = QVBoxLayout(content); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(0)
        self.strip = QueueStrip(qm, self.bridge)
        self.strip.open_queue.connect(lambda: self.go(self.queue_index))
        cv.addWidget(self.stack, 1); cv.addWidget(self.strip)
        h.addWidget(sidebar); h.addWidget(content, 1)
        self.setCentralWidget(root)

        # drop anywhere
        self.overlay = DropOverlay(self)
        self._hide_timer = QTimer(self); self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(80); self._hide_timer.timeout.connect(self.overlay.hide)
        QApplication.instance().installEventFilter(self)

        self.bridge.event.connect(self._on_event)
        self.nav.button(0).setChecked(True); self.stack.setCurrentIndex(0)
        self._update_nav()
        QTimer.singleShot(2500, lambda: self.check_updates(manual=False))
        QTimer.singleShot(600, self._whats_new)
        self._restore_geometry()
        self._init_sidebar()

    def go(self, i: int) -> None:
        self.nav.button(i).setChecked(True)
        self.stack.fade_to(i)
        if i == self.queue_index and not self.qm.is_running:
            self.taskbar.clear()

    # ---------- drop anywhere ----------
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t not in DRAG_EVENTS or not isinstance(obj, QWidget) or obj.window() is not self:
            return False
        if t == QEvent.DragLeave:
            self._hide_timer.start()
            return False
        mime = ev.mimeData()
        if not (mime.hasUrls() and any(u.isLocalFile() for u in mime.urls())):
            return False   # e.g. reordering inside the Merge list
        if t in (QEvent.DragEnter, QEvent.DragMove):
            self._hide_timer.stop()
            if not self.overlay.isVisible():
                self._show_overlay()
            ev.acceptProposedAction()
            return True
        self._hide_timer.stop(); self.overlay.hide()
        ev.acceptProposedAction()
        paths = paths_from_mime(mime)
        if paths:
            QTimer.singleShot(0, lambda p=paths: self._route(p))
        else:
            self.toast("No video files in that drop", "warn")
        return True

    def _show_overlay(self) -> None:
        self.overlay.title.setText(DROP_TEXT.get(self.stack.currentIndex(), DROP_TEXT[0]))
        self.overlay.setGeometry(self.centralWidget().geometry().adjusted(14, 14, -14, -14))
        self.overlay.raise_(); self.overlay.show()

    def _route(self, paths: list[str]) -> None:
        i = self.stack.currentIndex()
        if i not in DROP_TEXT:
            self.go(0); i = 0
        if i == 1:
            self.trim_page.open(paths[0])
            if len(paths) > 1:
                self.toast("Trim opens one file at a time. Opened the first one.", "info")
        else:
            {0: self.interp, 2: self.merge_page, 3: self.encode_page}[i].add_files(paths)

    # ---------- toasts ----------
    def toast(self, text: str, kind: str = "info") -> None:
        t = Toast(self, text, kind)
        t.closed.connect(self._toast_closed)
        self._toasts.append(t)
        self._layout_toasts()
        t.pop_in()

    def _toast_closed(self, t) -> None:
        if t in self._toasts:
            self._toasts.remove(t)
        self._layout_toasts()

    def _layout_toasts(self) -> None:
        y = self.height() - self.strip.height() - 16
        for t in reversed(self._toasts):
            y -= t.height()
            t.move(self.width() - t.width() - 22, y)
            t.raise_()
            y -= 8

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_toasts()
        if self.overlay.isVisible():
            self.overlay.setGeometry(self.centralWidget().geometry().adjusted(14, 14, -14, -14))

    # ---------- queue events ----------
    def _on_event(self, ev, job):
        self._update_nav()
        if self.qm.is_running:
            frac, _done, _n = self.qm.run_progress()
            self.taskbar.set(max(frac, 0.01))
        elif ev == "idle":
            failed = any(j.status == Status.FAILED for j in self.qm.run_jobs())
            self.taskbar.error() if failed and not self.qm.user_stopped else self.taskbar.clear()
            if not self.isActiveWindow():
                QApplication.alert(self)
        if ev == "finished" and job is not None and job.kind != "preview":
            name = clip(job.label, 48)
            if job.status == Status.DONE:
                self.toast(f"Finished · {name}", "ok")
            elif job.status == Status.FAILED:
                self.toast(f"Failed · {name}. Open the queue for details.", "error")

    def _update_nav(self):
        n = sum(j.status in (Status.QUEUED, Status.RUNNING) for j in self.qm.jobs)
        self.nav.button(self.queue_index).setText("  Queue" + (f"   ·  {n}" if n else ""))

    def closeEvent(self, e):
        if self.qm.is_running:
            r = QMessageBox.question(self, "Ralenti", "A job is running. Stop and quit?\n"
                                     "It resumes from the step it was on next time.")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore(); return
            running = [j.id for j in self.qm.jobs if j.status == Status.RUNNING]
            self.qm.stop(); self.qm.wait(10)
            for jid in running:
                self.qm.retry(jid)
        self._save_geometry()
        self.taskbar.clear()
        self.qm.save()
        e.accept()

    def run_setup(self) -> None:
        from .setup_dialog import SetupDialog
        SetupDialog(self.settings, self.qm, self).exec()
        self.settings_page.sync_from_settings()

    def check_updates(self, manual: bool = False) -> None:
        from ..core import updates
        from .workers import run_async
        from ..core import extensions
        if extensions.load().disable_updates:
            if manual:
                self.toast("Update checks are off in this build: update it from its own source.", "info")
            return
        s = self.settings
        if not manual and not s.check_updates:
            return
        if not s.update_repo:
            if manual:
                self.toast("Set the release source in Settings → Updates first", "warn")
            return

        def done(u):
            if u is None:
                if manual:
                    self.toast(f"You're up to date (v{__version__})", "ok")
            elif manual or u.version != s.skipped_version:
                self._offer_update(u)

        run_async(lambda: updates.check(s.update_repo, __version__), done,
                  lambda m: manual and self.toast(f"Update check failed: {m}", "warn"))

    def _offer_update(self, u) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        box = QMessageBox(self)
        box.setWindowTitle("Update available")
        box.setIconPixmap(pixmap("sparkle", 40, "accent"))
        box.setText(f"<b>Ralenti {u.version}</b> is available. You have {__version__}.")
        if u.notes:
            box.setInformativeText(u.notes)
        from ..core import extensions
        hint = extensions.load().update_hint
        if hint:
            box.setInformativeText(((u.notes + "\n\n") if u.notes else "") + hint)
        get = box.addButton("Open release page" if hint else "Download", QMessageBox.AcceptRole)
        skip = box.addButton("Skip this version", QMessageBox.DestructiveRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is get:
            QDesktopServices.openUrl(QUrl(u.url))
        elif box.clickedButton() is skip:
            s = self.settings
            s.skipped_version = u.version
            s.save()

    def _restore_geometry(self) -> None:
        from PySide6.QtCore import QByteArray
        g = getattr(self.settings, "window_geometry", "")
        if g and self.restoreGeometry(QByteArray.fromBase64(g.encode())):
            return
        scr = self.screen().availableGeometry()
        if scr.width() >= 1920 and scr.height() >= 1000:
            self.setWindowState(Qt.WindowMaximized)
        else:
            self.resize(int(scr.width() * 0.92), int(scr.height() * 0.92))

    def _save_geometry(self) -> None:
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.save()

    def _tools_changed(self, key: str, ok: bool, msg: str) -> None:
        from ..core.tools import Toolchain
        from .thumbs import thumbs
        if ok:
            self.qm.tc = Toolchain.resolve(self.settings)
            thumbs().configure(self.qm.tc)
            self.settings_page.sync_from_settings()
            self.toast(msg, "ok")
        elif msg != "Cancelled":
            self.toast(f"Download failed: {msg}", "error")
        self.downloads_page.refresh_all()

    def go_downloads(self) -> None:
        self.go([n for n, _ in NAV].index("Downloads"))



# ===================== v0.8.2: collapsible sidebar =====================
from PySide6.QtCore import QEasingCurve, QVariantAnimation  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402

SIDE_MIN = 72


def _init_sidebar(self) -> None:
    self._side = self.findChild(QFrame, "Sidebar")
    if self._side is None:
        return
    self._side_w = max(self._side.width(), 224)
    self._brand = [w for w in (self._side.findChild(QLabel, "Brand"), self._side.findChild(QLabel, "BrandSub")) if w]
    self._brand_row = self._brand[0].parentWidget().layout() if self._brand else None
    self._side_extra = [l for l in self._side.findChildren(QLabel)
                        if l.objectName() == "Hint" and l.text().startswith("v")]
    gm = getattr(self, "gpu_meter", None)
    if gm is not None:
        self._side_extra.append(gm.parentWidget() if gm.parentWidget() is not self._side else gm)
    self.side_btn = QPushButton(); self.side_btn.setObjectName("Nav")
    self.side_btn.setCursor(Qt.PointingHandCursor); self.side_btn.setIconSize(QSize(18, 18))
    self.side_btn.clicked.connect(self.toggle_sidebar)
    self._side.layout().addWidget(self.side_btn)
    self._side_anim = QVariantAnimation(self); self._side_anim.setDuration(180)
    self._side_anim.setEasingCurve(QEasingCurve.OutCubic)
    self._side_anim.valueChanged.connect(lambda v: self._side.setFixedWidth(int(v)))
    self._side_anim.finished.connect(self._side_done)
    sc = QShortcut(QKeySequence("Ctrl+B"), self); sc.activated.connect(self.toggle_sidebar)
    self._collapsed = bool(getattr(self.settings, "sidebar_collapsed", False))
    self._side.setFixedWidth(SIDE_MIN if self._collapsed else self._side_w)
    self._side_texts(self._collapsed)


def toggle_sidebar(self) -> None:
    if getattr(self, "_side", None) is None:
        return
    self._collapsed = not self._collapsed
    self.settings.sidebar_collapsed = self._collapsed
    self.settings.save()
    if self._collapsed:
        self._side_texts(True)
    self._side_anim.stop()
    self._side_anim.setStartValue(self._side.width())
    self._side_anim.setEndValue(SIDE_MIN if self._collapsed else self._side_w)
    self._side_anim.start()


def _side_done(self) -> None:
    if not self._collapsed:
        self._side_texts(False)


def _side_texts(self, c: bool) -> None:
    for w in self._brand + self._side_extra:
        w.setVisible(not c)
    if self._brand_row:
        m = 19 if c else 20
        self._brand_row.setContentsMargins(m, 22, m, 20)
    for i, (name, _ic) in enumerate(NAV):
        b = self.nav.button(i)
        if b is None:
            continue
        b.setText("" if c else f"  {name}")
        b.setToolTip(name if c else "")
        b.setProperty("collapsed", c); b.style().unpolish(b); b.style().polish(b)
    s = self.side_btn
    s.setProperty("collapsed", c); s.style().unpolish(s); s.style().polish(s)
    s.setText("" if c else "  Collapse")
    s.setIcon(icon("caret-double-right" if c else "caret-double-left", "muted", active="text", checked=None))
    s.setToolTip("Expand the sidebar (Ctrl+B)" if c else "Collapse the sidebar (Ctrl+B)")
    self._update_nav()


_update_nav_v81 = MainWindow._update_nav


def _update_nav_v82(self) -> None:
    _update_nav_v81(self)
    if getattr(self, "_collapsed", False):
        b = self.nav.button(self.queue_index)
        b.setToolTip(b.text().strip())
        b.setText("")


for _name, _fn in {"_init_sidebar": _init_sidebar, "toggle_sidebar": toggle_sidebar, "_side_done": _side_done,
                   "_side_texts": _side_texts, "_update_nav": _update_nav_v82}.items():
    setattr(MainWindow, _name, _fn)



# ===================== 0.9.4: in-app updates and "what's new" =====================
def _check_updates_v094(self, manual: bool = False) -> None:
    from ..core import extensions, updates
    from .update_dialog import UpdateDialog
    from .workers import run_async
    if extensions.load().disable_updates:
        if manual:
            self.toast("Update checks are off in this build.", "info")
        return
    s = self.settings
    if not manual and not s.check_updates:
        return
    if not s.update_repo:
        if manual:
            self.toast("Set the release source in Settings → Updates first", "warn")
        return

    def done(u):
        if u is None:
            if manual:
                self.toast(f"You're up to date (v{__version__})", "ok")
        elif manual or u.version != s.skipped_version:
            UpdateDialog(self, u).exec()

    def fail(m):
        if manual:
            self.toast(f"Update check failed: {m}", "warn")

    run_async(lambda: updates.check(s.update_repo, __version__), done, fail)


def _whats_new(self) -> None:
    import shutil
    from ..core import changelog
    from ..core.config import APP_DIR
    from ..core.updates import is_newer
    from .changelog_view import ChangelogDialog
    shutil.rmtree(APP_DIR / "updates", ignore_errors=True)
    s = self.settings
    prev = getattr(s, "last_version", "") or ""
    if prev == __version__:
        return
    s.last_version = __version__
    s.save()
    if not prev:
        return
    entries = [e for e in changelog.load()
               if is_newer(e["version"], prev) and not is_newer(e["version"], __version__)]
    if entries:
        ChangelogDialog(self, entries, f"What's new in Ralenti {__version__}", f"Updated from {prev}.").exec()


MainWindow.check_updates = _check_updates_v094
MainWindow._whats_new = _whats_new



def _show_live(self, jid) -> None:
    self.go([n for n, _ in NAV].index("Live"))
    self.live_page.watch(jid)


MainWindow.show_live = _show_live
