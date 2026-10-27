from PySide6.QtCore import QObject, Signal

from ..core.queue import QueueManager


class QueueBridge(QObject):
    """Re-emits QueueManager events as a Qt signal. Events raised on the worker
    thread are delivered to the GUI thread automatically (queued connection)."""
    event = Signal(str, object)

    def __init__(self, qm: QueueManager):
        super().__init__()
        qm.subscribe(lambda ev, job: self.event.emit(ev, job))