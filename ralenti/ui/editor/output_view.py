"""Output workspace: the destination folder with the new file shown where it will land, free space on the drive and
quick ways to change the folder (source folder, recent folders, browse, or navigate into subfolders)."""
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu, QSizePolicy,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from shiboken6 import isValid

from .. import theme
from ..icons import icon
from ..util import notify
from .summary import analyse, preview_output
from .toolbars import ToolRow, tool
from .workspaces import _mb

VIDEO = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".ts", ".mts", ".m2ts", ".wmv", ".flv"}
MAX_ITEMS = 2000
_NUMBERED = re.compile(r"^(.*) [(](\d+)[)](\.[^.]+)$")   # "name (1).mp4"


def _size_txt(n) -> str:
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    return f"{n / 1024:.0f} KB" if n < 1e6 else _mb(n)


def _same(a, b) -> bool:
    try:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except (OSError, ValueError):
        return False


def _existing(p: Path) -> Path:
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def listing(folder):
    """(folders, files, truncated); each entry (name, size, mtime)."""
    dirs, files = [], []
    with os.scandir(folder) as it:
        for d in it:
            if d.name.startswith((".", "$")):
                continue
            try:
                st = d.stat(follow_symlinks=False)
                (dirs if d.is_dir(follow_symlinks=False) else files).append(
                    (d.name, None if d.is_dir(follow_symlinks=False) else st.st_size, st.st_mtime))
            except OSError:
                continue
    dirs.sort(key=lambda x: x[0].lower())
    files.sort(key=lambda x: x[0].lower())
    cut = len(dirs) + len(files) > MAX_ITEMS
    return dirs[:MAX_ITEMS], files[:max(0, MAX_ITEMS - len(dirs))], cut


class OutputView(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page, self.s = page, page.session
        self._cache, self._folder, self._target = {}, None, None
        self.new_item = None
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        card = QFrame(); card.setObjectName("Card")
        cv = QVBoxLayout(card); cv.setContentsMargins(16, 14, 16, 14); cv.setSpacing(10)
        head = QHBoxLayout()
        lab = QLabel("DESTINATION"); lab.setObjectName("FieldLabel")
        self.path = QLabel(); self.path.setObjectName("OutPath")
        self.path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        head.addWidget(lab); head.addSpacing(10); head.addWidget(self.path, 1)
        cv.addLayout(head)
        self.bar = ToolRow()
        self.b_up = tool("", "arrow-up", "Parent folder", lambda: self._up())
        self.b_src = tool("Source folder", "film-strip", "Save next to the source file", lambda: self._src())
        self.b_recent = tool("Recent ▾", None, "Folders you added jobs to recently")
        self.recent_menu = QMenu(self.b_recent)
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self.b_recent.setMenu(self.recent_menu); self.b_recent.setPopupMode(QToolButton.InstantPopup)
        self.b_choose = tool("Choose…", "folder-open", "Pick another folder", lambda: self._choose())
        self.b_open = tool("", "arrow-square-out", "Open this folder in Explorer", lambda: self._open())
        self.b_refresh = tool("", "arrow-clockwise", "Refresh", lambda: self.refresh(force=True))
        self.bar.add(self.b_up); self.bar.sep(); self.bar.add(self.b_src, self.b_recent, self.b_choose)
        self.bar.sep(); self.bar.add(self.b_open, self.b_refresh); self.bar.finish()
        self.tree = QTreeWidget(); self.tree.setObjectName("FolderView")
        self.tree.setColumnCount(3); self.tree.setHeaderLabels(["Name", "Size", "Modified"])
        self.tree.setRootIsDecorated(False); self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(16, 16))
        hdr = self.tree.header(); hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemDoubleClicked.connect(self._activate)
        self.foot = QLabel(); self.foot.setObjectName("Hint"); self.foot.setWordWrap(True)
        self.foot.setTextFormat(Qt.RichText)
        cv.addWidget(self.bar); cv.addWidget(self.tree, 1); cv.addWidget(self.foot)
        root.addWidget(card, 1)

    # ---------- rows ----------
    def _row(self, name, size, mtime, kind):
        when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else ""
        it = QTreeWidgetItem([name, size, when])
        it.setIcon(0, icon({"dir": "folder-open", "video": "film-strip"}.get(kind, "file-text"), "muted",
                           checked=None))
        it.setData(0, Qt.UserRole, kind)
        it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        if kind == "dir":
            it.setToolTip(0, "Double-click to save in this folder")
        self.tree.addTopLevelItem(it)
        return it

    def _new(self, name, est):
        it = QTreeWidgetItem([name, f"≈ {_mb(est)}" if est else "", "will be created"])
        it.setIcon(0, icon("export", "accent", checked=None))
        it.setData(0, Qt.UserRole, "new")
        it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        f = QFont(self.tree.font()); f.setBold(True); f.setItalic(True)
        for c in range(3):
            it.setForeground(c, QColor(theme.ACCENT)); it.setFont(c, f)
        it.setToolTip(0, "Your new file. It appears here when the job finishes.")
        self.tree.addTopLevelItem(it)
        self.new_item = it

    # ---------- refresh ----------
    def refresh(self, force: bool = False) -> None:
        if not isValid(self.s):
            return
        s, e = self.s, self.s.edit
        an = analyse(s)
        self.tree.clear()
        self.new_item = None
        btns = (self.b_up, self.b_src, self.b_recent, self.b_choose, self.b_open, self.b_refresh)
        if not an:
            self.path.setText("Open a video first.")
            self.bar.note.setText(""); self.foot.setText("")
            for b in btns:
                b.setEnabled(False)
            return
        for b in btns:
            b.setEnabled(True)
        target = preview_output(s, an)
        folder, src = target.parent, Path(e.clips[0]).parent
        self._folder, self._target = folder, target
        self.path.setText(str(folder) + ("   (next to the source)" if _same(folder, src) else ""))
        self.path.setToolTip(str(folder))
        self.b_src.setEnabled(not _same(folder, src))
        self.b_up.setEnabled(folder.parent != folder)
        overwrite = e.output.get("exists") == "overwrite"
        est = self.page.encode_view._size(an)[0]
        notes = []
        dirs, files, cut = [], [], False
        if folder.is_dir():
            try:
                key = (str(folder), os.stat(folder).st_mtime_ns)
                if force or key not in self._cache:
                    self._cache = {key: listing(folder)}
                dirs, files, cut = self._cache[key]
            except OSError as ex:
                notes.append(f"<span style='color:{theme.DANGER}'>This folder can't be read: {ex}</span>")
        else:
            notes.append(f"<span style='color:{theme.WARN}'>This folder doesn't exist yet: it's created when you "
                         "add the job.</span>")
        name = target.name
        replaced = overwrite and target.exists()
        for n, _sz, mt in dirs:
            self._row(n, "", mt, "dir")
        placed = False
        for n, sz, mt in files:
            if not replaced and not placed and n.lower() > name.lower():
                self._new(name, est); placed = True
            kind = "video" if Path(n).suffix.lower() in VIDEO else "file"
            it = self._row(n, _size_txt(sz), mt, kind)
            if replaced and n.lower() == name.lower():
                it.setText(2, "will be replaced")
                for c in range(3):
                    it.setForeground(c, QColor(theme.WARN))
                self.new_item = it
        if not replaced and not placed:
            self._new(name, est)
        if self.new_item is not None:
            self.tree.setCurrentItem(self.new_item)
            self.tree.scrollToItem(self.new_item, QTreeWidget.PositionAtCenter)
        if replaced:
            notes.append(f"<span style='color:{theme.WARN}'>A file with this name exists and is replaced when the job "
                         "finishes.</span>")
        else:
            m = _NUMBERED.match(name)
            if m and (folder / (m.group(1) + m.group(3))).exists():
                notes.append("A file with this name already exists, so a number is added. Pick Overwrite in the "
                             "panel to replace it instead.")
        if cut:
            notes.append(f"Only the first {MAX_ITEMS} items are shown.")
        try:
            free = shutil.disk_usage(str(_existing(folder))).free
            low = est * 1.15 > free
            self.bar.note.setText(f"<span style='color:{theme.DANGER if low else theme.FAINT}'>{_mb(free)} free · "
                                  f"≈ {_mb(max(0.0, free - est))} after this file"
                                  + (" · probably not enough" if low else "") + "</span>")
        except (OSError, ValueError):
            self.bar.note.setText("")
        notes.append("Double-click a folder to save there. Change the file name in the panel.")
        self.foot.setText("<br>".join(notes))

    # ---------- actions ----------
    def _go(self, p) -> None:
        e = self.s.edit
        if not e.clips:
            return
        p = Path(p)
        self.s.set("output", out_dir="" if _same(p, Path(e.clips[0]).parent) else str(p))

    def _activate(self, it, _col) -> None:
        kind = it.data(0, Qt.UserRole)
        if kind == "dir" and self._folder is not None:
            self._go(self._folder / it.text(0))
        elif kind in ("video", "file") and self._folder is not None:
            try:
                os.startfile(str(self._folder / it.text(0)))
            except OSError as ex:
                notify(self.page, f"Couldn't open it: {ex}", "warn")
        elif kind == "new":
            notify(self.page, "This file is created when the job finishes.", "info")

    def _up(self) -> None:
        if self._folder is not None and self._folder.parent != self._folder:
            self._go(self._folder.parent)

    def _src(self) -> None:
        if self.s.edit.clips:
            self._go(Path(self.s.edit.clips[0]).parent)

    def _choose(self) -> None:
        start = str(_existing(self._folder)) if self._folder else ""
        p = QFileDialog.getExistingDirectory(self, "Save to", start)
        if p:
            self._go(p)

    def _open(self) -> None:
        if self._folder is not None:
            try:
                os.startfile(str(_existing(self._folder)))
            except OSError:
                pass

    def _fill_recent(self) -> None:
        m = self.recent_menu
        m.clear()
        recs = [x for x in (getattr(self.page.settings, "recent_out_dirs", "") or "").splitlines() if x.strip()]
        if not recs:
            a = m.addAction("No recent folders yet"); a.setEnabled(False)
            return
        for x in recs:
            ok = Path(x).is_dir()
            a = m.addAction(x if ok else f"{x}  (missing)")
            a.setEnabled(ok)
            a.triggered.connect(lambda _=False, x=x: self._go(x))
