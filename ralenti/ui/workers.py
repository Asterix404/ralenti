"""run_async(fn, on_done, on_error, pool): run fn on a pool thread, callbacks on the GUI thread."""
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

_alive: set = set()


class _Relay(QObject):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, on_done, on_error):
        super().__init__()
        self._ok, self._err = on_done, on_error
        self.done.connect(self._finish_ok)
        self.failed.connect(self._finish_err)

    @Slot(object)
    def _finish_ok(self, result):
        _alive.discard(self)
        self._ok(result)

    @Slot(str)
    def _finish_err(self, msg):
        _alive.discard(self)
        (self._err or print)(msg)


class _Task(QRunnable):
    def __init__(self, fn, relay):
        super().__init__()
        self.fn, self.relay = fn, relay

    def run(self):
        try:
            result, err = self.fn(), None
        except Exception as e:
            result, err = None, str(e) or type(e).__name__
        try:
            if err is None:
                self.relay.done.emit(result)
            else:
                self.relay.failed.emit(err)
        except RuntimeError:
            pass   # app closed while the task was running


def run_async(fn, on_done, on_error=None, pool: QThreadPool | None = None) -> None:
    relay = _Relay(on_done, on_error)
    _alive.add(relay)
    (pool or QThreadPool.globalInstance()).start(_Task(fn, relay))