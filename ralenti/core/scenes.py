"""Scene-cut detection with FFmpeg's scdet filter. Cuts are frame indices relative to the clip start
(frame n = the first frame of a new shot)."""
from __future__ import annotations

import re

LEVELS = {"normal": 10.0, "sensitive": 6.0}       # scdet scores run 0-100
_TIME = re.compile(r"lavfi\.scd\.time:\s*([0-9.]+)")


def detect_cmd(ffmpeg: str, path: str, start, dur, rate, threshold: float) -> list[str]:
    seek = (["-ss", f"{start:.3f}"] if start else []) + (["-t", f"{dur:.3f}"] if dur else [])
    r = f"{rate.numerator}/{rate.denominator}" if hasattr(rate, "numerator") else str(rate)
    return [ffmpeg, "-hide_banner", "-nostdin", "-y", "-nostats", "-progress", "pipe:1", *seek, "-i", path,
            "-map", "0:v:0", "-an", "-sn", "-vf", f"fps={r},scale=480:-2,scdet=threshold={threshold:g}",
            "-f", "null", "-"]


def parse(text: str, src_fps: float, total: int, min_gap: int) -> list[int]:
    out: list[int] = []
    for m in _TIME.finditer(text or ""):
        f = int(round(float(m.group(1)) * src_fps))
        if f <= 1 or f >= total - 1:
            continue
        if out and f - out[-1] < min_gap:          # flashes and strobes: keep the first
            continue
        out.append(f)
    return out


def apply_manual(cuts, add_s, remove_s, src_fps: float, total: int, tol: int = 3) -> list[int]:
    """Adds cuts given in seconds, removes detected cuts near the given seconds."""
    out = set(cuts)
    for t in remove_s or []:
        f = round(t * src_fps)
        out = {c for c in out if abs(c - f) > tol}
    for t in add_s or []:
        f = round(t * src_fps)
        if 1 < f < total - 1:
            out.add(f)
    return sorted(out)
