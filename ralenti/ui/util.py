import os
import subprocess
from pathlib import Path

from PySide6.QtGui import QFont


def reveal(path: str) -> None:
    p = Path(path)
    if p.exists():
        subprocess.Popen(f'explorer /select,"{p}"')
    elif p.parent.exists():
        os.startfile(p.parent)


def mono_font(size: int = 9) -> QFont:
    f = QFont("Cascadia Mono", size)
    f.setStyleHint(QFont.Monospace)
    return f


def muted(label):
    label.setObjectName("Muted")
    return label


def clip(text: str, n: int = 90) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"
    
def fmt_ts(sec, ms: bool = True) -> str:
    if sec is None:
        return "--:--"
    sec = max(0.0, float(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if ms:
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
    return f"{int(h)}:{int(m):02d}:{int(s):02d}"


def parse_ts(text: str) -> float:
    """'1:02.5', '00:01:02.500', '62.5' → seconds. Raises ValueError."""
    sec = 0.0
    for part in text.strip().replace(",", ".").split(":"):
        sec = sec * 60 + float(part)
    return sec
    
import sys


def resource(name: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS) / "ralenti"
    else:
        base = Path(__file__).resolve().parents[1]
    return base / "resources" / name
    
def notify(widget, text: str, kind: str = "info") -> None:
    win = widget.window()
    if hasattr(win, "toast"):
        win.toast(text, kind)