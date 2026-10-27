"""Auto black-bar detection: sample across the video, union the boxes (never over-crops)."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .probe import MediaInfo
from .tools import run

_CROP = re.compile(r"crop=(-?\d+):(-?\d+):(-?\d+):(-?\d+)")


@dataclass(frozen=True)
class Crop:
    w: int
    h: int
    x: int
    y: int

    @property
    def filter(self) -> str:
        return f"crop={self.w}:{self.h}:{self.x}:{self.y}"


def _sample(ffmpeg: str, path: str, t: float):
    r = run([ffmpeg, "-hide_banner", "-nostdin", "-ss", f"{t:.3f}", "-i", path,
             "-t", "1.5", "-an", "-sn", "-dn",
             "-vf", "cropdetect=limit=0.094:round=2:reset=0", "-f", "null", "-"], timeout=60)
    hits = _CROP.findall(r.stderr)
    if not hits:
        return None
    w, h, x, y = map(int, hits[-1])
    if w <= 0 or h <= 0:       # fully black sample
        return None
    return x, y, x + w, y + h


def detect_crop(ffmpeg: str, info: MediaInfo, samples: int = 8,
                symmetric: bool = True) -> Crop | None:
    if not info.duration or not info.width:
        return None
    if info.duration < 4:
        times = [0.0]
    else:
        times = [info.duration * (0.05 + 0.9 * i / (samples - 1)) for i in range(samples)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        boxes = [b for b in ex.map(lambda t: _sample(ffmpeg, info.path, t), times) if b]
    if not boxes:
        return None

    x1 = min(b[0] for b in boxes); y1 = min(b[1] for b in boxes)
    x2 = max(b[2] for b in boxes); y2 = max(b[3] for b in boxes)
    if symmetric:   # real bars come in pairs; one-sided "bars" are usually dark content
        lr = min(x1, info.width - x2)
        tb = min(y1, info.height - y2)
        x1, x2, y1, y2 = lr, info.width - lr, tb, info.height - tb
    x1 += x1 % 2; y1 += y1 % 2
    w = min(x2 - x1, info.width - x1) // 2 * 2
    h = min(y2 - y1, info.height - y1) // 2 * 2
    if w >= info.width - 8 and h >= info.height - 8:
        return None
    return Crop(w, h, x1, y1)