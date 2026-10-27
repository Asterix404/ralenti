"""Builds the real window offscreen, records every icon it asks for, and checks extras
used only in menus/dialogs. Run: python scripts/check_icons.py"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia*=false;qt.qpa.fonts=false")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)
from ralenti.core.config import Settings  # noqa: E402
from ralenti.core.queue import QueueManager  # noqa: E402
from ralenti.core.tools import Toolchain  # noqa: E402
from ralenti.ui import icons, theme  # noqa: E402
from ralenti.ui.main_window import MainWindow  # noqa: E402

EXTRA = ["dots-three", "file-text", "folder-open", "copy", "arrow-clockwise", "arrow-counter-clockwise",
         "stop", "arrow-up", "arrow-down", "trash", "play-fill", "pause-fill", "check-circle", "warning",
         "x-circle", "info", "moon", "power", "warning-circle", "speaker-high", "speaker-slash",
         "caret-down", "caret-right", "caret-left", "arrow-square-out", "check", "graphics-card",
         "magic-wand", "timer", "star-fill", "tray-arrow-down", "broom", "list-checks", "film-strip",
         "plus", "plus-circle", "minus-circle", "floppy-disk", "crop", "play-circle", "crosshair-simple",
         "skip-back", "skip-forward", "x", "mark-in", "mark-out", "sparkle", "scissors", "stack",
         "gauge", "gear-six", "speaker-low", "eraser", "export", "sliders", "wave", "seal-check", "film-slate", "arrows-out", "arrows-in", "github", "bug", "star", "split-h", "columns", "versus", "scroll", "download", "palette", "wrench", "package", "pulse", "minus-circle", "view-left", "view-right", "view-split", "view-side", "caret-double-left", "caret-double-right"]

theme.apply(app)
s = Settings.load()
qm = QueueManager(Toolchain.resolve(s), s, path=Path(tempfile.gettempdir()) / "ralenti_icon_check.json")
win = MainWindow(s, qm)

names = sorted(set(icons._resolved) | set(EXTRA))
bad = []
for n in names:
    full = icons.resolve(n)
    print(f"  {'OK ' if full else 'MISSING'}  {n:<24} -> {full or '-'}")
    if not full:
        bad.append(n)
print(f"\nChecked {len(names)} icons. " + ("All resolve." if not bad else f"{len(bad)} missing: {', '.join(bad)}"))