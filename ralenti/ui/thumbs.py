"""Thumbnail service: background extraction (max 3 at once), in-memory cache, rounded rendering."""
from PySide6.QtCore import QObject, QRectF, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap

from ..core.thumbs import make_thumb
from . import theme
from .icons import pixmap as icon_pixmap
from .workers import run_async


class ThumbService(QObject):
    ready = Signal(str)   # source path

    def __init__(self):
        super().__init__()
        self.tc = None
        self._cache: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)

    def configure(self, tc) -> None:
        self.tc = tc
        self._failed.clear()

    def get(self, path: str | None) -> QPixmap | None:
        if not path:
            return None
        if (pm := self._cache.get(path)) is not None:
            return pm
        if path not in self._pending and path not in self._failed and self.tc and self.tc.any_ffmpeg:
            self._pending.add(path)
            ff, fp = self.tc.any_ffmpeg, self.tc.ffprobe
            run_async(lambda: make_thumb(ff, fp, path), lambda out, p=path: self._done(p, out),
                      lambda _m, p=path: self._fail(p), pool=self.pool)
        return None

    def _done(self, path: str, out) -> None:
        self._pending.discard(path)
        pm = QPixmap(out) if out else QPixmap()
        if pm.isNull():
            self._failed.add(path)
            return
        self._cache[path] = pm
        self.ready.emit(path)

    def _fail(self, path: str) -> None:
        self._pending.discard(path)
        self._failed.add(path)


_service: ThumbService | None = None


def thumbs() -> ThumbService:
    global _service
    if _service is None:
        _service = ThumbService()
    return _service


def rounded(pm: QPixmap | None, w: int, h: int, radius: float = 8, dpr: float = 1.0) -> QPixmap:
    W, H = int(w * dpr), int(h * dpr)
    out = QPixmap(W, H)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, W, H), radius * dpr, radius * dpr)
    p.setClipPath(path)
    if pm is None or pm.isNull():
        p.fillRect(out.rect(), QColor(theme.SURFACE3))
        s = int(min(W, H) * 0.42)
        ic = icon_pixmap("film-strip", s, "faint")
        p.drawPixmap((W - s) // 2, (H - s) // 2, s, s, ic)
    else:
        sc = pm.scaled(W, H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        p.drawPixmap((W - sc.width()) // 2, (H - sc.height()) // 2, sc)
    p.end()
    out.setDevicePixelRatio(dpr)
    return out