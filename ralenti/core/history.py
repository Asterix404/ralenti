"""Undo/redo of whole-edit snapshots. Rapid changes to the same setting count as one step."""
from __future__ import annotations

import time


class History:
    def __init__(self, limit: int = 100, window: float = 1.0):
        self.undo, self.redo, self.limit, self.window = [], [], limit, window
        self._key, self._t = None, 0.0

    def record(self, before, key=None, now=None) -> None:
        now = time.monotonic() if now is None else now
        if key is not None and key == self._key and now - self._t < self.window:
            self._t = now
            return
        self.undo.append(before)
        del self.undo[:-self.limit]
        self.redo.clear()
        self._key, self._t = key, now

    def back(self, current):
        if not self.undo:
            return None
        self.redo.append(current)
        self._key = None
        return self.undo.pop()

    def forward(self, current):
        if not self.redo:
            return None
        self.undo.append(current)
        self._key = None
        return self.redo.pop()
