from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QVBoxLayout

from ..icons import pixmap
from ..util import muted

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".m2ts",
             ".ts", ".wmv", ".mxf", ".flv", ".mpg", ".mpeg"}
FILTER = "Videos (" + " ".join(f"*{e}" for e in sorted(VIDEO_EXT)) + ");;All files (*)"


def paths_from_mime(mime) -> list[str]:
    paths = []
    for u in mime.urls():
        if not u.isLocalFile():
            continue
        p = Path(u.toLocalFile())
        if p.is_dir():
            paths += sorted(str(f) for f in p.rglob("*") if f.suffix.lower() in VIDEO_EXT)
        elif p.suffix.lower() in VIDEO_EXT:
            paths.append(str(p))
    return paths


class DropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self, text="Drop videos or folders here", sub="or click to browse",
                 min_height: int = 130):
        super().__init__()
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(min_height)
        lay = QVBoxLayout(self); lay.setAlignment(Qt.AlignCenter); lay.setSpacing(4)
        ic = QLabel(); ic.setPixmap(pixmap("plus-circle", 26, "accent")); ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet("background: transparent;")
        title = QLabel(text); title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 10.5pt; font-weight: 600; background: transparent;")
        hint = muted(QLabel(sub)); hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("background: transparent;")
        lay.addWidget(ic); lay.addWidget(title); lay.addWidget(hint)
        self.setMinimumHeight(max(min_height, lay.sizeHint().height() + 6))

    def browse(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add videos", "", FILTER)
        if files:
            self.files_dropped.emit(files)

    def _hover(self, on: bool) -> None:
        self.setProperty("hover", on)
        self.style().unpolish(self); self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction(); self._hover(True)

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        self._hover(False)
        if paths := paths_from_mime(e.mimeData()):
            self.files_dropped.emit(paths)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.browse()