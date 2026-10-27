"""Slow-motion audio: drop, copy, slow with kept pitch, or slow with natural pitch."""
from __future__ import annotations

from enum import Enum


class AudioMode(str, Enum):
    DROP = "drop"
    COPY = "copy"              # only valid at 1x
    KEEP_PITCH = "keep_pitch"  # Premiere "Maintain Audio Pitch" on
    NATURAL = "natural"        # tape-style: slower and deeper


def resolve_mode(mode: AudioMode, slowmo: float, has_audio: bool) -> AudioMode:
    if not has_audio:
        return AudioMode.DROP
    at_1x = abs(slowmo - 1) < 1e-6
    if at_1x and mode in (AudioMode.KEEP_PITCH, AudioMode.NATURAL):
        return AudioMode.COPY          # nothing to slow down: keep the original untouched
    # COPY with slow motion is allowed: the UI warns that audio ends early
    return mode


def atempo_chain(speed: float) -> str:
    parts, t = [], speed
    while t < 0.5:
        parts.append("atempo=0.5"); t /= 0.5
    while t > 2.0:
        parts.append("atempo=2.0"); t /= 2.0
    if abs(t - 1) > 1e-9:
        parts.append(f"atempo={t:.6g}")
    return ",".join(parts) or "anull"


def audio_filter(mode: AudioMode, slowmo: float, sample_rate: int,
                 rubberband: bool = False) -> str | None:
    """None means no filter (copy or drop)."""
    if mode in (AudioMode.DROP, AudioMode.COPY):
        return None
    speed = 1.0 / slowmo
    if mode == AudioMode.NATURAL:
        sr = sample_rate or 48000
        return f"asetrate={round(sr * speed)},aresample={sr}"
    if rubberband:
        return f"rubberband=tempo={speed:.6f}:pitchq=quality"
    return atempo_chain(speed)


def audio_codec_args(ext: str) -> list[str]:
    ext = ext.lower()
    if ext in (".flac", ".mkv"):
        return ["-c:a", "flac"]
    if ext == ".mov":
        return ["-c:a", "pcm_s24le"]
    return ["-c:a", "aac", "-b:a", "256k"]   # .mp4, .m4a
    
MP4_AUDIO = {"aac", "mp3", "ac3", "eac3", "opus", "alac", "flac"}
MOV_AUDIO = MP4_AUDIO | {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"}


def passthrough_ok(acodec: str, ext: str) -> bool:
    ext = ext.lower()
    if ext == ".mkv":
        return True
    if ext == ".mov":
        return acodec in MOV_AUDIO
    if ext == ".mp4":
        return acodec in MP4_AUDIO
    return False


def encode_audio_args(mode: str, info, ext: str) -> list[str] | None:
    """mode: auto (copy if the container allows it) | encode | drop. None = no audio."""
    if not info.has_audio or mode == "drop":
        return None
    if mode in ("auto", "copy") and passthrough_ok(info.acodec, ext):
        return ["-c:a", "copy"]
    return audio_codec_args(ext)
