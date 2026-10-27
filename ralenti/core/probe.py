"""ffprobe wrapper. fps is kept as an exact Fraction (e.g. 30000/1001)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from fractions import Fraction

from .tools import run


def _rate(s: str | None) -> Fraction:
    try:
        f = Fraction(s or "0")
        return f if f > 0 else Fraction(0)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def _clean(v) -> str:
    return "" if v in (None, "unknown", "unspecified", "reserved") else str(v)


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: Fraction = field(default_factory=Fraction)
    pix_fmt: str = ""
    vcodec: str = ""
    color_space: str = ""
    color_primaries: str = ""
    color_trc: str = ""
    color_range: str = ""
    has_audio: bool = False
    acodec: str = ""
    sample_rate: int = 0
    channels: int = 0

    @property
    def bit_depth(self) -> int:
        for d in (16, 12, 10):
            if f"p{d}" in self.pix_fmt or f"p0{d}" in self.pix_fmt:
                return d
        return 8

    @property
    def fps_float(self) -> float:
        return float(self.fps) if self.fps else 0.0


def probe(ffprobe: str, path: str) -> MediaInfo:
    info = MediaInfo(path=path)
    r = run([ffprobe, "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height,pix_fmt,"
             "avg_frame_rate,r_frame_rate,color_space,color_primaries,color_transfer,"
             "color_range,sample_rate,channels,duration",
             "-of", "json", path], timeout=30)
    try:
        data = json.loads(r.stdout or "{}")
    except ValueError:
        return info

    info.duration = float(data.get("format", {}).get("duration") or 0)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)

    if video:
        info.width, info.height = int(video.get("width", 0)), int(video.get("height", 0))
        info.fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
        info.pix_fmt = video.get("pix_fmt", "")
        info.vcodec = video.get("codec_name", "")
        info.color_space = _clean(video.get("color_space"))
        info.color_primaries = _clean(video.get("color_primaries"))
        info.color_trc = _clean(video.get("color_transfer"))
        info.color_range = _clean(video.get("color_range"))
        if not info.duration:
            info.duration = float(video.get("duration") or 0)
    if audio:
        info.has_audio = True
        info.acodec = audio.get("codec_name", "")
        info.sample_rate = int(audio.get("sample_rate") or 0)
        info.channels = int(audio.get("channels") or 0)
    return info