"""DownloadManager: runs tool installs on background threads and reports progress via Qt signals."""
import shutil
import threading
import time

from PySide6.QtCore import QObject, Signal

from ..core import downloads as core


def mb(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


class DownloadManager(QObject):
    progress = Signal(str, str, float, float)   # key, stage, done, total
    finished = Signal(str, bool, str)            # key, ok, message
    latest_ready = Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._cancel: dict[str, threading.Event] = {}
        self._t0: dict[str, tuple[float, float]] = {}
        self.stage: dict[str, tuple] = {}
        self.latest: dict[str, dict] = {}
        self._checking = False
        shutil.rmtree(core.STAGING, ignore_errors=True)

    def repo(self, key: str) -> str:
        return (getattr(self.settings, f"{key}_repo", "") or "").strip() or core.TOOLS[key].repo

    def busy(self, key: str | None = None) -> bool:
        return key in self._cancel if key else bool(self._cancel)

    def install(self, key: str) -> None:
        if key in self._cancel:
            return
        ev = threading.Event()
        self._cancel[key] = ev
        self._t0.pop(key, None)
        self.stage[key] = ("download", 0.0, 0.0, 0.0)
        self.progress.emit(key, "download", 0.0, 0.0)

        def run():
            try:
                info = core.install(key, self.repo(key), lambda s, d, t: self._prog(key, s, d, t), ev)
                ok, msg = True, f"{core.TOOLS[key].name} {info['version']} installed"
            except core.Cancelled:
                ok, msg = False, "Cancelled"
            except Exception as e:
                ok, msg = False, str(e) or type(e).__name__
            self._cancel.pop(key, None)
            self.stage.pop(key, None)
            self.finished.emit(key, ok, msg)

        threading.Thread(target=run, daemon=True, name=f"ralenti-dl-{key}").start()

    def _prog(self, key: str, stage: str, done: float, total: float) -> None:
        speed = 0.0
        if stage == "download":
            now = time.monotonic()
            t0, d0 = self._t0.setdefault(key, (now, done))
            if now - t0 > 1:
                speed = (done - d0) / (now - t0) / 1e6
        self.stage[key] = (stage, done, total, speed)
        self.progress.emit(key, stage, done, total)

    def describe(self, key: str):
        """(fraction, text) for an install in progress, or None."""
        st = self.stage.get(key)
        if not st:
            return None
        stage, d, t, speed = st
        if stage == "download":
            text = f"Downloading {mb(d)}" + (f" of {mb(t)}" if t else "") + (f" · {speed:.1f} MB/s" if speed else "")
            return (d / t if t else 0.0), text
        if stage == "verify":
            return 1.0, "Verifying the download…"
        return (d / t if t else 1.0), "Unpacking…" + (f" {d / t * 100:.0f}%" if t else "")

    def cancel(self, key: str) -> None:
        if ev := self._cancel.get(key):
            ev.set()

    def remove(self, key: str) -> None:
        core.remove(key)
        self.finished.emit(key, True, f"{core.TOOLS[key].name} removed")

    def check_latest(self, force: bool = False) -> None:
        keys = [k for k in core.TOOLS if force or k not in self.latest]
        if not keys or self._checking:
            return
        self._checking = True

        def run():
            try:
                for k in keys:
                    try:
                        self.latest[k] = core.release_info(k, self.repo(k))
                    except Exception as e:
                        self.latest[k] = {"error": str(e)}
                    self.latest_ready.emit(k)
            finally:
                self._checking = False

        threading.Thread(target=run, daemon=True, name="ralenti-dl-check").start()


_mgr: DownloadManager | None = None


def downloads(settings=None) -> DownloadManager:
    global _mgr
    if _mgr is None:
        _mgr = DownloadManager(settings)
    elif settings is not None:
        _mgr.settings = settings
    return _mgr