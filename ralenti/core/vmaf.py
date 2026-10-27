"""ab-av1 style quality search: encode samples, score with libvmaf, binary search CQ/CRF."""
from __future__ import annotations

import os
import re
from typing import Callable

_SCORE = re.compile(r"VMAF score[:=]\s*([0-9]+(?:\.[0-9]+)?)")


def model_for(width: int, height: int) -> str:
    return "vmaf_4k_v0.6.1" if width * height > 2560 * 1440 else "vmaf_v0.6.1"


def sample_windows(duration: float, count: int = 5, length: float = 4.0,
                   whole_below: float = 30.0) -> list[tuple[float, float]]:
    """Short clips are scored whole; longer ones use evenly spread samples."""
    if duration <= whole_below:
        return [(0.0, duration)]
    span = duration / count
    return [(max(0.0, span * (i + 0.5) - length / 2), length) for i in range(count)]


def build_score(ffmpeg: str, distorted: str, reference: str, start: float,
                duration: float, model: str, fps=None, threads: int | None = None) -> list[str]:
    """Frames are renumbered by index on both sides, so container timestamp
    rounding (MKV = 1 ms) can never pair frame N with frame N-1."""
    threads = threads or max(2, os.cpu_count() or 4)
    tb = f"{fps.denominator}/{fps.numerator}" if fps else "1/30"
    norm = f"settb={tb},setpts=N,format=yuv420p10le"
    ref_seek = (["-ss", f"{start:.3f}"] if start else []) + ["-t", f"{duration:.3f}"]
    graph = (f"[0:v]{norm}[d];[1:v]{norm}[r];"
             f"[d][r]libvmaf=model=version={model}:n_threads={threads}:shortest=1")
    return [ffmpeg, "-hide_banner", "-nostdin", "-y", "-nostats", "-progress", "pipe:1",
            "-i", distorted, *ref_seek, "-i", reference,
            "-lavfi", graph, "-f", "null", "-"]

def parse_score(stderr: str) -> float:
    hits = _SCORE.findall(stderr)
    if not hits:
        raise RuntimeError("VMAF score not found in ffmpeg output")
    return float(hits[-1])


def quality_search(score_at: Callable[[int], float], lo: int, hi: int, target: float,
                   max_iter: int = 8) -> tuple[int | None, dict[int, float]]:
    """Higher q = smaller file. Returns the highest q whose score >= target."""
    best, scores = None, {}
    for _ in range(max_iter):
        if lo > hi:
            break
        q = (lo + hi) // 2
        scores[q] = score_at(q)
        if scores[q] >= target:
            best, lo = q, q + 1
        else:
            hi = q - 1
    return best, scores