"""Main window: sidebar (edit steps, app pages), page stack, queue bar, toasts, drop anywhere,
taskbar progress, updates, setup."""
from PySide6.QtCore import QByteArray, QEasingCurve, QEvent, QPointF, QSize, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from .. import __version__
from ..core import extensions
from ..core.jobs import Status
from . import theme
from .bridge import QueueBridge
from .editor.page import EditorPage
from .icons import icon, pixmap
from .pages.about_page import AboutPage
from .pages.downloads_page import DownloadsPage
from .pages.live_page import LivePage
from .pages.queue_page import QueuePage
from .pages.settings_page import SettingsPage
from .util import clip, resource
from .widgets.anim import AnimatedStack, Toast
from .widgets.drop_zone import paths_from_mime
from .widgets.gpu_meter import GpuMeter
from .widgets.queue_strip import QueueStrip
from .winapi import TaskbarProgress

NAV = [  # key, label, icon, group
    ("trim", "Trim", "scissors", "edit"), ("crop", "Transform", "crop", "edit"),
    ("motion", "Interpolation", "sparkle", "edit"), ("audio", "Audio", "speaker-high", "edit"),
    ("encode", "Encode", "gauge", "edit"), ("output", "Output", "export", "edit"),
    ("queue", "Queue", "list-checks", "app"), ("live", "Live", "eye", "app"),
    ("downloads", "Downloads", "download", "app"), ("settings", "Settings", "gear-six", "app"),
    ("about", "About", "info", "app"),
]
KEYS = [n[0] for n in NAV]
EDIT_STEPS = {"trim", "crop", "motion", "audio", "encode", "output"}
GROUPS = [("edit", "EDIT"), ("app", "APP")]
DRAG_EVENTS = (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop, QEvent.DragLeave)
SIDE_W, SIDE_MIN = 232, 72


class NavButton(QPushButton):
    def __init__(self, label: str, ic: str):
        super().__init__(f"  {label}")
        self.dot = False
        self.setObjectName("Nav"); self.setCheckable(True); self.setCursor(Qt.PointingHandCursor)
        self.setIcon(icon(ic, "muted", active="text", checked="accent")); self.setIconSize(QSize(18, 18))

    def set_dot(self, on: bool) -> None:
        if on != self.dot:
            self.dot = on
            self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.dot:
            p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen); p.setBrush(QColor(theme.ACCENT))
            c = QPointF(self.width() - 22, self.height() / 2) if self.text() else QPointF(self.width() / 2 + 11, 11)
            p.drawEllipse(c, 3.5, 3.5)


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
        self._toasts, self._cur, self.queue_index = [], "motion", "queue"
        ext = extensions.load()
        self.setWindowTitle(f"Ralenti {__version__}" + (f" · {ext.name}" if ext.name else ""))
        self.resize(1320, 860); self.setMinimumSize(1040, 680)
        self.setAcceptDrops(True)
        self.bridge = QueueBridge(qm)
        self.taskbar = TaskbarProgress(self)

        self.editor = EditorPage(qm, settings)
        self.queue_page = QueuePage(qm, self.bridge)
        self.live_page = LivePage(qm, settings)
        self.downloads_page = DownloadsPage(qm, settings)
        self.settings_page = SettingsPage(qm, settings)
        self.pages = {"editor": self.editor, "queue": self.queue_page, "live": self.live_page,
                      "downloads": self.downloads_page, "settings": self.settings_page,
                      "about": AboutPage(qm, settings)}
        self.settings_page.saved.connect(self.editor.reload_options)
        self.queue_page.add_requested.connect(lambda: (self.go("motion"), self.editor.browse()))
        self.settings_page.setup_requested.connect(self.run_setup)
        self.settings_page.open_downloads.connect(self.go_downloads)
        self.editor.dots_changed.connect(self._update_dots)
        from .downloads import downloads
        downloads(settings).finished.connect(self._tools_changed)

        root = QWidget(); root.setObjectName("Root")
        h = QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        self.sidebar = QFrame(); self.sidebar.setObjectName("Sidebar")
        sv = QVBoxLayout(self.sidebar); sv.setContentsMargins(0, 0, 0, 12); sv.setSpacing(2)
        brand = QWidget()
        self.brand_row = bl = QHBoxLayout(brand); bl.setContentsMargins(20, 22, 20, 12); bl.setSpacing(12)
        logo = QLabel()
        ico, dpr = resource("ralenti.ico"), self.devicePixelRatioF()
        if ico.exists():
            pm = QPixmap(str(ico)).scaled(int(34 * dpr), int(34 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
        names = QWidget(); nl = QVBoxLayout(names); nl.setContentsMargins(0, 0, 0, 0); nl.setSpacing(0)
        t = QLabel("Ralenti"); t.setObjectName("Brand")
        st = QLabel("Motion & encoding studio"); st.setObjectName("BrandSub")
        nl.addWidget(t); nl.addWidget(st)
        bl.addWidget(logo); bl.addWidget(names, 1)
        sv.addWidget(brand)
        self._side_hide = [names]

        self.nav = QButtonGroup(self); self.nav.setExclusive(True)
        self.stack = AnimatedStack()
        self._index = {}
        for key, page in self.pages.items():
            self._index[key] = self.stack.count()
            self.stack.addWidget(page)
        for group, header in GROUPS:
            if group == "app":
                sv.addStretch(1)
            hl = QLabel(header); hl.setObjectName("NavHeader")
            sv.addWidget(hl); self._side_hide.append(hl)
            for i, (key, label, ic, g) in enumerate(NAV):
                if g == group:
                    b = NavButton(label, ic)
                    self.nav.addButton(b, i); sv.addWidget(b)
        self.nav.idClicked.connect(lambda i: self.go(KEYS[i]))
        self.gpu_meter = GpuMeter(qm)
        gw = QWidget(); gl = QVBoxLayout(gw); gl.setContentsMargins(12, 8, 12, 0); gl.addWidget(self.gpu_meter)
        ver = QLabel(f"v{__version__}"); ver.setObjectName("Hint"); ver.setContentsMargins(24, 8, 0, 0)
        sv.addWidget(gw); sv.addWidget(ver)
        self._side_hide += [gw, ver]
        self.side_btn = QPushButton(); self.side_btn.setObjectName("Nav"); self.side_btn.setIconSize(QSize(18, 18))
        self.side_btn.setCursor(Qt.PointingHandCursor); self.side_btn.clicked.connect(self.toggle_sidebar)
        sv.addWidget(self.side_btn)

        content = QWidget()
        cv = QVBoxLayout(content); cv.setContentsMargins(0, 0, 0, 0); cv.setSpacing(0)
        self.strip = QueueStrip(qm, self.bridge)
        self.strip.open_queue.connect(lambda: self.go("queue"))
        cv.addWidget(self.stack, 1); cv.addWidget(self.strip)
        h.addWidget(self.sidebar); h.addWidget(content, 1)
        self.setCentralWidget(root)

        self.overlay = DropOverlay(self)
        self._hide_timer = QTimer(self); self._hide_timer.setSingleShot(True); self._hide_timer.setInterval(80)
        self._hide_timer.timeout.connect(self.overlay.hide)
        QApplication.instance().installEventFilter(self)

        self._side_anim = QVariantAnimation(self); self._side_anim.setDuration(180)
        self._side_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._side_anim.valueChanged.connect(lambda v: self.sidebar.setFixedWidth(int(v)))
        self._side_anim.finished.connect(lambda: None if self._collapsed else self._side_texts(False))
        self._collapsed = bool(getattr(settings, "sidebar_collapsed", False))
        self.sidebar.setFixedWidth(SIDE_MIN if self._collapsed else SIDE_W)
        QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self.toggle_sidebar)

        self.bridge.event.connect(self._on_event)
        self._side_texts(self._collapsed)
        self.go("motion")
        self._update_dots()
        self._restore_geometry()
        QTimer.singleShot(600, self._whats_new)
        QTimer.singleShot(2500, lambda: self.check_updates(manual=False))

    # ---------- navigation ----------
    def go(self, target) -> None:
        key = KEYS[target] if isinstance(target, int) else target
        if key not in KEYS:
            return
        self._cur = key
        self.nav.button(KEYS.index(key)).setChecked(True)
        self.stack.fade_to(self._index["editor" if key in EDIT_STEPS else key])
        if key in EDIT_STEPS:
            self.editor.show_step(key)
        if key == "queue" and not self.qm.is_running:
            self.taskbar.clear()

    def go_downloads(self) -> None:
        self.go("downloads")

    def show_live(self, jid) -> None:
        self.go("live")
        self.live_page.watch(jid)

    def _update_dots(self) -> None:
        d = self.editor.dots()
        for i, key in enumerate(KEYS):
            if key in EDIT_STEPS:
                self.nav.button(i).set_dot(d.get(key, False))

    def _update_nav(self) -> None:
        n = sum(j.status in (Status.QUEUED, Status.RUNNING, Status.PAUSED) and j.kind != "preview"
                for j in self.qm.jobs)
        b = self.nav.button(KEYS.index("queue"))
        label = "Queue" + (f"   ·  {n}" if n else "")
        b.setText("" if self._collapsed else f"  {label}")
        b.setToolTip(label if self._collapsed else "")

    # ---------- sidebar ----------
    def toggle_sidebar(self) -> None:
        self._collapsed = not self._collapsed
        self.settings.sidebar_collapsed = self._collapsed
        self.settings.save()
        if self._collapsed:
            self._side_texts(True)
        self._side_anim.stop()
        self._side_anim.setStartValue(self.sidebar.width())
        self._side_anim.setEndValue(SIDE_MIN if self._collapsed else SIDE_W)
        self._side_anim.start()

    def _side_texts(self, c: bool) -> None:
        for w in self._side_hide:
            w.setVisible(not c)
        m = 19 if c else 20
        self.brand_row.setContentsMargins(m, 22, m, 12)
        for i, (_k, label, _ic, _g) in enumerate(NAV):
            b = self.nav.button(i)
            b.setText("" if c else f"  {label}")
            b.setToolTip(label if c else "")
            b.setProperty("collapsed", c); b.style().unpolish(b); b.style().polish(b)
        s = self.side_btn
        s.setProperty("collapsed", c); s.style().unpolish(s); s.style().polish(s)
        s.setText("" if c else "  Collapse")
        s.setIcon(icon("caret-double-right" if c else "caret-double-left", "muted", active="text", checked=None))
        s.setToolTip("Expand the sidebar (Ctrl+B)" if c else "Collapse the sidebar (Ctrl+B)")
        self._update_nav()

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
            return False
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
        self.overlay.title.setText("Drop to add clips to your video")
        self.overlay.setGeometry(self.centralWidget().geometry().adjusted(14, 14, -14, -14))
        self.overlay.raise_(); self.overlay.show()

    def _route(self, paths) -> None:
        if self._cur not in EDIT_STEPS:
            self.go("motion")
        self.editor.add_files(paths)

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
    def _on_event(self, ev, job) -> None:
        self._update_nav()
        if self.qm.is_running:
            frac, _d, _n = self.qm.run_progress()
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

    # ---------- tools, setup, updates ----------
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

    def run_setup(self) -> None:
        from .setup_dialog import SetupDialog
        SetupDialog(self.settings, self.qm, self).exec()
        self.settings_page.sync_from_settings()

    def check_updates(self, manual: bool = False) -> None:
        from ..core import updates
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
        entries = [e for e in changelog.load() if is_newer(e["version"], prev) and not is_newer(e["version"], __version__)]
        if entries:
            ChangelogDialog(self, entries, f"What's new in Ralenti {__version__}", f"Updated from {prev}.").exec()

    # ---------- window state ----------
    def _restore_geometry(self) -> None:
        g = getattr(self.settings, "window_geometry", "")
        if g and self.restoreGeometry(QByteArray.fromBase64(g.encode())):
            return
        scr = self.screen().availableGeometry()
        if scr.width() >= 1920 and scr.height() >= 1000:
            self.setWindowState(Qt.WindowMaximized)
        else:
            self.resize(int(scr.width() * 0.92), int(scr.height() * 0.92))

    def closeEvent(self, e):
        if self.qm.is_running:
            r = QMessageBox.question(self, "Ralenti", "A job is running. Stop and quit?\n"
                                     "It resumes from where it was next time.")
            if r != QMessageBox.StandardButton.Yes:
                e.ignore(); return
            running = [j.id for j in self.qm.jobs if j.status == Status.RUNNING]
            self.qm.stop(); self.qm.wait(10)
            for jid in running:
                self.qm.retry(jid)
        fw = QApplication.focusWidget()
        if fw is not None:
            fw.clearFocus()  # commits a field being typed in (e.g. the file name) before the edit is saved
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.save()
        self.editor.session.save()
        self.taskbar.clear()
        self.qm.save()
        e.accept()
