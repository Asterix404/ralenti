"""QListWidget with thumbnails (from item UserRole path) and a built-in empty state."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QListWidget

from ..thumbs import rounded, thumbs
from .empty import EmptyState

THUMB = QSize(96, 54)


class FileList(QListWidget):
    add_requested = Signal()

    def __init__(self, title: str, hint: str, icon_name: str = "film-strip", action: str = "Add videos"):
        super().__init__()
        self.setObjectName("FileList")
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setIconSize(THUMB)
        self.setSpacing(2)
        self.setTextElideMode(Qt.ElideMiddle)
        self._icons: dict[str, QIcon] = {}
        self._empty = EmptyState(icon_name, title, hint, action, self.viewport())
        self._empty.action.connect(self.add_requested.emit)
        m = self.model()
        m.rowsInserted.connect(self._inserted)
        m.rowsRemoved.connect(lambda *_: self._sync())
        m.modelReset.connect(self._sync)
        thumbs().ready.connect(self._thumb_ready)
        self._sync()

    def _icon_for(self, path: str) -> QIcon:
        if path in self._icons:
            return self._icons[path]
        pm = thumbs().get(path)
        ic = QIcon(rounded(pm, THUMB.width(), THUMB.height(), 7, self.devicePixelRatioF()))
        if pm is not None:
            self._icons[path] = ic
        return ic

    def _inserted(self, _parent, first, last):
        for r in range(first, last + 1):
            it = self.item(r)
            if it and (p := it.data(Qt.UserRole)):
                it.setIcon(self._icon_for(p))
        self._sync()

    def _thumb_ready(self, path: str):
        self._icons.pop(path, None)
        for i in range(self.count()):
            it = self.item(i)
            if it.data(Qt.UserRole) == path:
                it.setIcon(self._icon_for(path))

    def clear(self):
        super().clear()
        self._sync()

    def _sync(self):
        self._empty.setGeometry(self.viewport().rect())
        self._empty.setVisible(self.count() == 0)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._empty.setGeometry(self.viewport().rect())