"""Keyframe timestamps from packet flags (no decoding, fast even on long files)."""
from __future__ import annotations

from bisect import bisect_left, bisect_right

from .tools import run


def keyframes(ffprobe: str, path: str) -> list[float]:
    r = run([ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", path], timeout=300)
    out = set()
    for line in r.stdout.splitlines():
        pts, _, flags = line.partition(",")
        if "K" in flags:
            try:
                out.add(float(pts))
            except ValueError:
                pass
    return sorted(out)


def prev_key(kfs: list[float], t: float, eps: float = 0.005) -> float:
    """Keyframe at or before t: where a lossless (-c copy) cut really starts."""
    i = bisect_right(kfs, t + eps) - 1
    return kfs[i] if i >= 0 else 0.0


def key_before(kfs: list[float], t: float, eps: float = 0.01) -> float | None:
    i = bisect_left(kfs, t - eps) - 1
    return kfs[i] if i >= 0 else None


def key_after(kfs: list[float], t: float, eps: float = 0.01) -> float | None:
    i = bisect_right(kfs, t + eps)
    return kfs[i] if i < len(kfs) else None