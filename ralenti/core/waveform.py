"""Waveform peaks of one audio track, for the Audio workspace."""
from __future__ import annotations

import array
import subprocess

from .tools import NO_WINDOW


def bucket(raw: bytes, buckets: int) -> list[float]:
    """Peak level per bucket of signed 16-bit samples, normalised to the loudest bucket."""
    a = array.array("h")
    a.frombytes(raw[:len(raw) // 2 * 2])
    n = len(a)
    if not n:
        return []
    buckets = max(1, min(buckets, n))
    out = []
    for i in range(buckets):
        seg = a[i * n // buckets:(i + 1) * n // buckets]
        out.append(max(max(seg), -min(seg)) / 32768)
    top = max(out) or 1.0
    return [x / top for x in out]


def peaks(ffmpeg: str, path: str, track: int = 0, buckets: int = 1200, duration: float = 0.0):
    """(peaks, seconds decoded). Empty when the track can't be read."""
    if not ffmpeg:
        return [], 0.0
    rate = 4000 if (duration or 0) < 1800 else 1000
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", path, "-map", f"0:a:{int(track)}",
           "-vn", "-sn", "-dn", "-ac", "1", "-ar", str(rate), "-acodec", "pcm_s16le", "-f", "s16le", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return [], 0.0
    return bucket(r.stdout, buckets), (len(r.stdout) // 2) / rate



def listen_cmd(ffmpeg: str, path, track: int, ranges, flt, out) -> list[str]:
    """The kept parts of one audio track, back to back, optionally processed (slowed), as a stereo WAV to listen to."""
    k = len(ranges)
    src = f"[0:a:{int(track)}]"
    parts, labels = [], ""
    if k > 1:
        parts.append(src + f"asplit={k}" + "".join(f"[i{i}]" for i in range(k)))
    for i, (a, b) in enumerate(ranges):
        inp = f"[i{i}]" if k > 1 else src
        parts.append(f"{inp}atrim=start={float(a):.3f}:end={float(b):.3f},asetpts=PTS-STARTPTS[s{i}]")
        labels += f"[s{i}]"
    last = "[s0]"
    if k > 1:
        parts.append(f"{labels}concat=n={k}:v=0:a=1[c]")
        last = "[c]"
    if flt:
        parts.append(f"{last}{flt}[o]")
        last = "[o]"
    return [ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-i", str(path), "-filter_complex",
            ";".join(parts), "-map", last, "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(out)]
