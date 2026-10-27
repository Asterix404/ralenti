"""Merge analysis + re-encode merge for clips that don't match."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .audio import audio_codec_args
from .commands import _head, _video_out
from .encoders import Encoder
from .probe import MediaInfo
from .tools import Toolchain


def compat_issues(infos: list[MediaInfo]) -> list[str]:
    """Empty list = safe for lossless concat (-c copy)."""
    issues = []

    def check(label, key):
        vals = [key(i) for i in infos]
        if len(set(vals)) > 1:
            issues.append(f"{label}: " + " / ".join(map(str, dict.fromkeys(vals))))

    check("Video codec", lambda i: i.vcodec)
    check("Resolution", lambda i: f"{i.width}×{i.height}")
    check("Pixel format", lambda i: i.pix_fmt)
    check("Frame rate", lambda i: f"{i.fps_float:.3f}")
    check("Audio track", lambda i: "yes" if i.has_audio else "none")
    if all(i.has_audio for i in infos):
        check("Audio codec", lambda i: i.acodec)
        check("Sample rate", lambda i: i.sample_rate)
        check("Channels", lambda i: i.channels)
    return issues


@dataclass
class MergeTarget:
    width: int
    height: int
    fps: str
    audio: bool
    sample_rate: int = 48000

    @property
    def rate(self) -> Fraction:
        return Fraction(self.fps)


def pick_target(infos: list[MediaInfo], res: str = "first") -> MergeTarget:
    if res == "largest":
        ref = max(infos, key=lambda i: i.width * i.height)
        fps = max((i.fps for i in infos if i.fps), default=Fraction(30))
    else:
        ref, fps = infos[0], infos[0].fps or Fraction(30)
    return MergeTarget(ref.width // 2 * 2, ref.height // 2 * 2, str(fps),
                       any(i.has_audio for i in infos))


def build_merge_reencode(tc: Toolchain, infos: list[MediaInfo], t: MergeTarget,
                         enc: Encoder, output: str, q: int | None = None) -> list[str]:
    W, H = t.width, t.height
    fr = f"{t.rate.numerator}/{t.rate.denominator}"
    cmd = _head(tc.binary_for(enc.codec))
    for i in infos:
        cmd += ["-i", i.path]
    parts, pads = [], ""
    for k, i in enumerate(infos):
        parts.append(f"[{k}:v:0]setpts=PTS-STARTPTS,"
                     f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                     f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                     f"fps={fr},format={enc.pix_fmt}[v{k}]")
        pads += f"[v{k}]"
        if t.audio:
            if i.has_audio:
                parts.append(f"[{k}:a:0]asetpts=PTS-STARTPTS,aresample={t.sample_rate},"
                             f"aformat=sample_fmts=fltp:channel_layouts=stereo[a{k}]")
            else:   # silent filler so every segment has audio
                parts.append(f"anullsrc=r={t.sample_rate}:cl=stereo,"
                             f"atrim=duration={i.duration:.3f}[a{k}]")
            pads += f"[a{k}]"
    a = 1 if t.audio else 0
    parts.append(f"{pads}concat=n={len(infos)}:v=1:a={a}" + ("[v][a]" if a else "[v]"))
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]"]
    if a:
        cmd += ["-map", "[a]", *audio_codec_args(Path(output).suffix)]
    return cmd + _video_out(enc, q, infos[0], output)