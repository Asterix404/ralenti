import os
import sys

os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.ffmpeg*=false")
os.environ.setdefault("QT_API", "pyside6")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .core.config import Settings, ensure_dirs
from .core.queue import QueueManager
from .core.tools import Toolchain
from .ui import theme
from .ui.main_window import MainWindow
from .ui.setup_dialog import SetupDialog
from .ui.thumbs import thumbs
from .ui.util import resource
from .ui.winapi import TitleBarStyler, style_title_bar


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ralenti.app")
        # lets the installer detect a running Ralenti before upgrading or uninstalling
        main._mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "RalentiSingleInstance")
    migrated = ensure_dirs()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    if (ico := resource("ralenti.ico")).exists():
        app.setWindowIcon(QIcon(str(ico)))
    styler = TitleBarStyler(app)
    app.installEventFilter(styler)
    from .ui.widgets.choice import ComboPolisher
    polisher = ComboPolisher(app)
    app.installEventFilter(polisher)

    settings = Settings.load()
    theme.apply(app, settings.accent)
    tc = Toolchain.resolve(settings)
    thumbs().configure(tc)
    qm = QueueManager(tc, settings)
    qm.load()

    if not settings.setup_done or not tc.ffmpeg:
        SetupDialog(settings, qm).exec()   # standalone welcome window, before the main window

    win = MainWindow(settings, qm)
    win.winId()
    style_title_bar(win)   # dark frame before the first paint: no white flash
    win.show()
    if migrated:
        win.toast("Moved your settings, presets and queue to Ralenti", "info")
    if qm.tc.problems():
        QTimer.singleShot(700, lambda: win.toast("Some tools are missing · Settings → Run setup", "warn"))
    sys.exit(app.exec())