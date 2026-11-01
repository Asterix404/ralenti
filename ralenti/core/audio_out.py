"""Audio for an edit: keep the original, convert it to a codec that fits the container, or remove it.
Slow motion slows the audio (natural or kept pitch). Audio is a tiny share of a video file, so the
recommendations lean towards quality."""
from __future__ import annotations

from .audio import AudioMode, audio_filter, passthrough_ok

CODECS = {
    "aac": {"label": "AAC", "blurb": "Plays everywhere. Transparent for stereo at 192 kbps.",
            "kbps": {"transparent": 256, "high": 192, "small": 128}},
    "opus": {"label": "Opus", "blurb": "The best quality per megabyte. Some older players and TVs can't play it.",
             "kbps": {"transparent": 192, "high": 160, "small": 96}},
    "flac": {"label": "FLAC", "blurb": "Lossless and compressed, about half the size of PCM. MKV only.", "kbps": None},
    "pcm": {"label": "PCM", "blurb": "Uncompressed, the standard for editing. MOV or MKV; large.", "kbps": None},
}
QUALITIES = {"transparent": "Transparent", "high": "High", "small": "Small"}
ALL = frozenset(CODECS)
CONTAINER_OK = {".mp4": frozenset({"aac", "opus"}), ".mov": frozenset({"aac", "pcm"}), ".mkv": ALL}


def fits(codec: str, ext: str) -> bool:
    return codec in CONTAINER_OK.get(ext.lower(), ALL)


def auto_codec(ext: str, encoder: str = "") -> str:
    ext = ext.lower()
    if encoder == "ffv1":
        return "flac"
    if encoder == "prores" or ext == ".mov":
        return "pcm"
    if ext == ".mkv":
        return "opus"
    return "aac"


def codec_args(codec: str, quality: str, channels: int) -> tuple[list[str], int | None]:
    scale = max(1.0, (channels or 2) / 2)
    if codec in ("aac", "opus"):
        kbps = int(CODECS[codec]["kbps"].get(quality, CODECS[codec]["kbps"]["high"]) * scale)
        return ["-c:a", "aac" if codec == "aac" else "libopus", "-b:a", f"{kbps}k"], kbps
    if codec == "flac":
        return ["-c:a", "flac", "-compression_level", "8"], None
    return ["-c:a", "pcm_s16le" if quality == "small" else "pcm_s24le"], None


def _label(codec: str, quality: str, kbps) -> str:
    if codec == "flac":
        return "FLAC, lossless"
    if codec == "pcm":
        return "PCM " + ("16-bit" if quality == "small" else "24-bit")
    return f"{CODECS[codec]['label']} {kbps} kbps"


def plan_audio(a: dict, info, ext: str, slowmo: float, tc, encoder: str = "") -> dict:
    """What the audio step does. mode: drop | copy | encode."""
    ext, a = ext.lower(), a or {}
    if not info.has_audio:
        return {"mode": "drop", "label": "No audio track", "warning": ""}
    mode = a.get("mode") or "auto"
    if mode == "remove":
        return {"mode": "drop", "label": "Removed", "warning": ""}
    slow = abs(float(slowmo or 1) - 1) > 1e-6
    warning = ""
    if mode in ("auto", "copy") and not slow and passthrough_ok(info.acodec or "", ext):
        return {"mode": "copy", "codec": info.acodec, "label": f"Original {(info.acodec or 'audio').upper()}, copied",
                "warning": ""}
    if mode == "copy":
        warning = ("Slow motion changes the audio, so it's converted instead of copied." if slow
                   else f"The original {info.acodec or 'audio'} can't go into {ext} as is, so it's converted.")
    codec = (a.get("codec") or "auto") if mode == "convert" else "auto"
    if codec not in CODECS:
        codec = auto_codec(ext, encoder)
    if not fits(codec, ext):
        new = auto_codec(ext, encoder)
        warning = f"{CODECS[codec]['label']} can't be stored in {ext}: using {CODECS[new]['label']} instead."
        codec = new
    if codec == "opus" and not tc.binary_for("libopus"):
        codec = "aac"
        warning = warning or "Your FFmpeg build has no Opus encoder: using AAC instead."
    q = a.get("quality") if a.get("quality") in QUALITIES else "high"
    args, kbps = codec_args(codec, q, info.channels)
    flt, slow_mode = None, ""
    if slow:
        slow_mode = "keep_pitch" if a.get("slow") == "keep_pitch" else "natural"
        flt = audio_filter(AudioMode(slow_mode), float(slowmo), info.sample_rate, rubberband=tc.has_rubberband)
    label = _label(codec, q, kbps) + {"natural": " · slowed, natural pitch",
                                       "keep_pitch": " · slowed, pitch kept"}.get(slow_mode, "")
    return dict(mode="encode", codec=codec, quality=q, args=args, kbps=kbps, filter=flt, slow=slow_mode,
                label=label, warning=warning)


def bytes_per_second(plan: dict, info) -> float | None:
    """Estimated audio size per second of output (None = same as the original, unknown)."""
    if plan.get("mode") == "drop":
        return 0.0
    if plan.get("mode") == "copy":
        return None
    if plan.get("kbps"):
        return plan["kbps"] * 1000 / 8
    sr, ch = info.sample_rate or 48000, info.channels or 2
    if plan.get("codec") == "flac":
        return sr * ch * 2 * 0.6
    return sr * ch * (2 if plan.get("quality") == "small" else 3)
