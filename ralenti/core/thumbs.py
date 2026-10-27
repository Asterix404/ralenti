"""One cached JPEG thumbnail per file, keyed by path + size + modified time."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .config import THUMB_DIR
from .tools import run


def thumb_path(src: str, width: int = 320) -> Path:
    st = os.stat(src)
    key = f"{os.path.abspath(src).lower()}|{st.st_size}|{st.st_mtime_ns}|{width}"
    return THUMB_DIR / f"{hashlib.sha1(key.encode()).hexdigest()[:20]}.jpg"


def _duration(ffprobe: str, src: str) -> float:
    if not ffprobe:
        return 0.0
    r = run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", src], timeout=20)
    try:
        return float(json.loads(r.stdout or "{}").get("format", {}).get("duration") or 0)
    except ValueError:
        return 0.0


def make_thumb(ffmpeg: str, ffprobe: str, src: str, width: int = 320) -> str | None:
    out = thumb_path(src, width)
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dur = _duration(ffprobe, src)
    seeks = [dur * 0.1, 0.0] if dur > 1 else [0.0]
    for t in seeks:
        run([ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", src,
             "-frames:v", "1", "-an", "-sn", "-vf", f"scale={width}:-2:flags=bicubic",
             "-q:v", "4", str(out)], timeout=30)
        if out.exists() and out.stat().st_size > 0:
            return str(out)
    return None