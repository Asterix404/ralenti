"""The clips of the video being edited: thumbnails, drag to reorder, add and remove, undo/redo."""
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QSizePolicy, QToolButton)

from ...core.merge import compat_issues, pick_target
from .. import theme
from ..icons import icon
from ..thumbs import rounded, thumbs
from ..util import fmt_ts
from ..widgets.drop_zone import FILTER
from ..widgets.fields import button

TW, TH = 72, 40


class ClipsBar(QFrame):
    selected = Signal(str)

    def __init__(self, session):
        super().__init__()
        self.s = session
        self.setObjectName("ClipsBar")
        h = QHBoxLayout(self); h.setContentsMargins(10, 8, 10, 8); h.setSpacing(10)
        self.list = QListWidget(); self.list.setObjectName("ClipList")
        self.list.setFlow(QListWidget.LeftToRight); self.list.setWrapping(False)
        self.list.setIconSize(QSize(TW, TH)); self.list.setFixedHeight(TH + 24)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setDragDropMode(QAbstractItemView.InternalMove); self.list.setDefaultDropAction(Qt.MoveAction)
        self.list.setTextElideMode(Qt.ElideMiddle)
        self.list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.list.model().rowsMoved.connect(lambda *_: self._reordered())
        self.list.currentItemChanged.connect(self._sel)
        self.empty = QLabel("No video open. Add one clip, or several to join them into one video.")
        self.empty.setObjectName("Hint")
        self.status = QLabel(); self.status.setObjectName("Hint"); self.status.setWordWrap(True)
        self.status.setMaximumWidth(280)
        self.add_btn = button("Add clips", self.browse, "accent", icon="plus")
        self.rm = self._tb("trash", "Remove the selected clip (Delete)", self._remove)
        self.undo_b = self._tb("undo", "Undo (Ctrl+Z)", session.undo)
        self.redo_b = self._tb("redo", "Redo (Ctrl+Y)", session.redo)
        h.addWidget(self.list, 1); h.addWidget(self.empty, 1); h.addWidget(self.status)
        for w in (self.add_btn, self.rm, self.undo_b, self.redo_b):
            h.addWidget(w)
        sc = QShortcut(QKeySequence(Qt.Key_Delete), self.list); sc.activated.connect(self._remove)
        session.changed.connect(lambda _w: self.refresh())
        thumbs().ready.connect(self._thumb)
        self.refresh()

    def _tb(self, name, tip, fn):
        b = QToolButton(); b.setIcon(icon(name, "muted", active="text", checked=None)); b.setIconSize(QSize(18, 18))
        b.setToolTip(tip); b.setCursor(Qt.PointingHandCursor); b.clicked.connect(lambda: fn())
        return b

    def current(self):
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _icon(self, p) -> QIcon:
        return QIcon(rounded(thumbs().get(p), TW, TH, 6, self.devicePixelRatioF()))

    def _thumb(self, path) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(Qt.UserRole) == path:
                it.setIcon(self._icon(path))

    def refresh(self) -> None:
        s, clips = self.s, self.s.edit.clips
        cur = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]
        if cur != clips:
            sel = self.current()
            self.list.blockSignals(True); self.list.clear()
            for p in clips:
                info = s.info(p)
                it = QListWidgetItem(f"{Path(p).name}\n{fmt_ts(info.duration, False)}")
                it.setData(Qt.UserRole, p); it.setToolTip(p); it.setIcon(self._icon(p))
                it.setSizeHint(QSize(240, TH + 14))
                self.list.addItem(it)
            self.list.blockSignals(False)
            if clips:
                self.list.setCurrentRow(clips.index(sel) if sel in clips else 0)
                self.selected.emit(self.current())
        n = len(clips)
        self.list.setVisible(n > 0); self.empty.setVisible(n == 0); self.rm.setEnabled(n > 0)
        self.add_btn.setText("Add clips" if n else "Open a video")
        self.undo_b.setEnabled(s.can_undo()); self.redo_b.setEnabled(s.can_redo())
        if n == 1:
            i = s.info(clips[0])
            self.status.setText(f"{i.width}×{i.height} · {i.fps_float:.3f} fps · {fmt_ts(i.duration, False)}")
            self.status.setStyleSheet(""); self.status.setToolTip("")
        elif n > 1:
            infos = [s.info(c) for c in clips]
            issues = compat_issues(infos)
            total = fmt_ts(sum(i.duration for i in infos), False)
            if issues:
                t = pick_target(infos, "first")
                self.status.setText(f"{n} clips · {total} · converted to {t.width}×{t.height}, "
                                    f"{float(t.rate):.3f} fps when joined")
                self.status.setStyleSheet(f"color: {theme.WARN};")
                self.status.setToolTip("The clips differ:\n• " + "\n• ".join(issues))
            else:
                self.status.setText(f"{n} clips · {total} · joined losslessly")
                self.status.setStyleSheet(f"color: {theme.OK};"); self.status.setToolTip("")
        else:
            self.status.setText("")

    def _sel(self, cur, _prev) -> None:
        if cur is not None:
            self.selected.emit(cur.data(Qt.UserRole))

    def _reordered(self) -> None:
        self.s.set_clips([self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())])

    def _remove(self) -> None:
        p = self.current()
        if p:
            self.s.set_clips([c for c in self.s.edit.clips if c != p])

    def browse(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add clips", "", FILTER)
        if paths:
            self.window().editor.add_files(paths) if hasattr(self.window(), "editor") else self.s.add_clips(paths)
