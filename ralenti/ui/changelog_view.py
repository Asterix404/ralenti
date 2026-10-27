"""Pretty changelog: one card per release, sections as coloured labels, bullet items."""
import html
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .. import __version__
from ..core.updates import is_newer
from . import theme
from .icons import pixmap
from .widgets.fields import button


def _cat(name: str):
    k = name.lower()
    if k.startswith(("new", "add", "feature")):
        return "sparkle", theme.ACCENT
    if k.startswith(("improv", "better", "perf", "change", "update")):
        return "arrow-up", "#60a5fa"
    if k.startswith(("fix", "bug")):
        return "check-circle", theme.OK
    if k.startswith(("remov", "deprec")):
        return "minus-circle", theme.DANGER
    return "info", theme.MUTED


def _rgba(color: str, a: int) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a})"


def _chip(text: str, color: str) -> QLabel:
    l = QLabel(text)
    l.setStyleSheet(f"background: {_rgba(color, 40)}; color: {color}; border-radius: 6px; padding: 1px 8px; "
                    f"font-size: 8pt; font-weight: 800;")
    return l


def _dot(color: str) -> QPixmap:
    pm = QPixmap(32, 40); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen); p.setBrush(QColor(color)); p.drawEllipse(11, 15, 10, 10); p.end()
    pm.setDevicePixelRatio(2)
    return pm


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"`(.+?)`", rf"<span style='background:{theme.SURFACE3}; font-family:Consolas'>&nbsp;\1&nbsp;</span>", s)


class ReleaseCard(QFrame):
    def __init__(self, e: dict, latest: bool, installed: str):
        super().__init__()
        self.setObjectName("Card")
        v = QVBoxLayout(self); v.setContentsMargins(22, 18, 22, 20); v.setSpacing(10)
        head = QHBoxLayout(); head.setSpacing(8)
        ver = QLabel(f"Ralenti {e['version']}")
        ver.setStyleSheet("font-size: 13.5pt; font-weight: 800;")
        head.addWidget(ver)
        newer = is_newer(e["version"], installed)
        if latest:
            head.addWidget(_chip("Latest", theme.ACCENT))
        if e["version"] == installed:
            head.addWidget(_chip("Installed", theme.OK))
        elif newer and not latest:
            head.addWidget(_chip("New", theme.ACCENT))
        head.addStretch(1)
        if e["date"]:
            d = QLabel(e["date"]); d.setObjectName("Hint")
            head.addWidget(d)
        v.addLayout(head)
        if e["title"]:
            t = QLabel(e["title"]); t.setObjectName("Muted"); t.setWordWrap(True)
            v.addWidget(t)
        for name, items in e["sections"]:
            ic, color = _cat(name)
            row = QHBoxLayout(); row.setSpacing(7); row.setContentsMargins(0, 6, 0, 0)
            i = QLabel(); i.setPixmap(pixmap(ic, 15, color))
            n = QLabel(name.upper()); n.setStyleSheet(f"color: {color}; font-size: 8pt; font-weight: 800;")
            row.addWidget(i); row.addWidget(n); row.addStretch(1)
            v.addLayout(row)
            for it in items:
                ir = QHBoxLayout(); ir.setContentsMargins(2, 0, 0, 0); ir.setSpacing(8)
                dot = QLabel(); dot.setFixedSize(16, 20); dot.setPixmap(_dot(color))
                lbl = QLabel(_inline(it)); lbl.setWordWrap(True); lbl.setTextFormat(Qt.RichText)
                lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                ir.addWidget(dot, 0, Qt.AlignTop); ir.addWidget(lbl, 1)
                v.addLayout(ir)


class ChangelogView(QScrollArea):
    def __init__(self, entries: list, installed: str = __version__, empty: str = "No changelog available."):
        super().__init__()
        self.setWidgetResizable(True); self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(); body.setObjectName("ScrollBody")
        v = QVBoxLayout(body); v.setContentsMargins(0, 0, 6, 0); v.setSpacing(12)
        if not entries:
            l = QLabel(empty); l.setObjectName("Hint"); v.addWidget(l)
        for i, e in enumerate(entries):
            v.addWidget(ReleaseCard(e, i == 0, installed))
        v.addStretch(1)
        self.setWidget(body)


class ChangelogDialog(QDialog):
    def __init__(self, parent, entries: list, title: str = "Changelog", sub: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 720)
        v = QVBoxLayout(self); v.setContentsMargins(26, 22, 26, 18); v.setSpacing(12)
        t = QLabel(title); t.setObjectName("PageTitle")
        v.addWidget(t)
        if sub:
            s = QLabel(sub); s.setObjectName("PageSub"); s.setWordWrap(True)
            v.addWidget(s)
        v.addWidget(ChangelogView(entries), 1)
        row = QHBoxLayout(); row.addStretch(1)
        row.addWidget(button("Close", self.accept, "accent"))
        v.addLayout(row)
