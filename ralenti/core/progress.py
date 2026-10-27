"""Parser for ffmpeg '-progress pipe:1' key=value blocks."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Snapshot:
    out_time: float = 0.0
    frame: int = 0
    fps: float = 0.0
    speed: float = 0.0
    done: bool = False


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _hms(v: str) -> float:
    try:
        h, m, s = v.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, AttributeError):
        return 0.0


class ProgressParser:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}

    def feed(self, line: str) -> Snapshot | None:
        key, sep, val = line.strip().partition("=")
        if not sep:
            return None
        if key != "progress":
            self._kv[key] = val.strip()
            return None
        kv, self._kv = self._kv, {}
        us = _num(kv.get("out_time_us"), -1)
        if us < 0:
            us = _num(kv.get("out_time_ms"), -1)   # also microseconds (ffmpeg quirk)
        t = us / 1e6 if us >= 0 else _hms(kv.get("out_time", ""))
        return Snapshot(out_time=max(0.0, t),
                        frame=int(_num(kv.get("frame"))),
                        fps=_num(kv.get("fps")),
                        speed=_num(kv.get("speed", "").rstrip("x")),
                        done=val.strip() == "end")


def fmt_eta(seconds: float | None) -> str:
    if seconds is None or seconds != seconds or seconds < 0:
        return ""
    s = int(seconds)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"