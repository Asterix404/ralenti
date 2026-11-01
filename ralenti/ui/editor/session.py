"""The open video and its edit: clips, steps, undo/redo and autosave."""
import json
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ...core.config import APP_DIR
from ...core.edit import Edit
from ...core.history import History
from ...core.probe import probe

AUTOSAVE = APP_DIR / "editor.json"


class EditSession(QObject):
    changed = Signal(str)          # 'clips' | a step name | 'all'

    def __init__(self, qm):
        super().__init__()
        self.qm, self.edit, self.infos, self.hist = qm, Edit(), {}, History()
        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(800)
        self._timer.timeout.connect(self.save)

    def info(self, path=None):
        path = path or (self.edit.clips[0] if self.edit.clips else None)
        if not path:
            return None
        if path not in self.infos:
            self.infos[path] = probe(self.qm.tc.ffprobe, path)
        return self.infos[path]

    def media(self):
        """Info of the video being edited (several clips: the first clip's format, summed duration)."""
        if not self.edit.clips:
            return None
        first = self.info()
        if len(self.edit.clips) == 1:
            return first
        return replace(first, duration=sum(self.info(c).duration for c in self.edit.clips))

    def clip_offset(self, path) -> float:
        t = 0.0
        for c in self.edit.clips:
            if c == path:
                return t
            t += self.info(c).duration
        return 0.0

    def set(self, step: str, **kw) -> None:
        cur = getattr(self.edit, step)
        if all(cur.get(k) == v for k, v in kw.items()):
            return
        self.hist.record(self.edit.to_dict(), f"{step}:{','.join(sorted(kw))}")
        cur.update(kw)
        self._emit(step)

    def add_clips(self, paths) -> list[str]:
        bad, new = [], list(self.edit.clips)
        for p in paths:
            if p in new:
                continue
            i = self.info(p)
            if i and i.width:
                new.append(p)
            else:
                bad.append(Path(p).name)
        self.set_clips(new)
        return bad

    def set_clips(self, clips) -> None:
        clips = list(clips)
        if clips == self.edit.clips:
            return
        self.hist.record(self.edit.to_dict())
        self.edit.clips = clips
        self._emit("clips")

    def can_undo(self) -> bool:
        return bool(self.hist.undo)

    def can_redo(self) -> bool:
        return bool(self.hist.redo)

    def undo(self) -> None:
        self._jump(self.hist.back(self.edit.to_dict()))

    def redo(self) -> None:
        self._jump(self.hist.forward(self.edit.to_dict()))

    def _jump(self, st) -> None:
        if st is not None:
            self.edit = Edit.from_dict(st)
            self._emit("all")

    def _emit(self, what: str) -> None:
        self.changed.emit(what)
        self._timer.start()

    def save(self) -> None:
        try:
            AUTOSAVE.write_text(json.dumps(self.edit.to_dict(), indent=1), encoding="utf-8")
        except OSError:
            pass

    def restore(self) -> None:
        try:
            d = json.loads(AUTOSAVE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        e = Edit.from_dict(d)
        e.clips = [c for c in e.clips if Path(c).is_file() and (self.info(c) or None) and self.info(c).width]
        self.edit = e
        self._emit("all")
