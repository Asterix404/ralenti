"""Disk space prediction: final outputs accumulate, temp files only peak during one job."""
from __future__ import annotations

import os
import shutil
from collections import defaultdict
from pathlib import Path

from .jobs import Job
from .pipeline import work_units

BPP = {"av1_nvenc": 0.015, "hevc_nvenc": 0.02, "h264_nvenc": 0.03, "svtav1": 0.012, "x265": 0.015,
       "prores": 0.45, "ffv1": 1.6, "ref_nvenc": 0.4}
MARGIN, RESERVE = 1.15, 2e9


def drive(path: str) -> str:
    p = Path(path)
    return p.anchor or str(p)


def _size(path: str | None) -> int:
    try:
        return os.path.getsize(path) if path else 0
    except OSError:
        return 0


def estimate(job: Job) -> tuple[int, int, str]:
    """(final output bytes, peak temp bytes, temp drive)"""
    p, s = job.plan, job.spec
    pf = work_units(job) * 1e9
    tdrive = drive(p.get("tmp") or job.output)
    if job.kind == "interpolate":
        out = pf * BPP.get(s.get("encoder"), 0.03)
        tmp = float(p.get("engine_temp") or 0)
        if p.get("two_pass"):
            tmp += pf * BPP.get(p.get("intermediate_enc"), 0.4)
        if p.get("two_pass") or p.get("audio_step"):
            tmp += out
        return int(out), int(tmp), tdrive
    if job.kind == "encode" or (job.kind == "merge" and p.get("reencode")):
        return int(pf * BPP.get(s.get("encoder"), 0.03)), 0, tdrive
    if job.kind == "merge":
        return sum(_size(f) for f in s.get("files", [])), 0, tdrive
    if job.kind == "cut":
        return _size(s.get("src")), 0, tdrive
    return 0, 0, tdrive


def needs(jobs: list[Job]) -> dict[str, float]:
    outputs, peaks = defaultdict(float), defaultdict(float)
    for j in jobs:
        out, tmp, td = estimate(j)
        outputs[drive(j.output)] += out
        peaks[td] = max(peaks[td], tmp)
    total = defaultdict(float)
    for d, v in list(outputs.items()) + list(peaks.items()):
        total[d] += v
    return total


def shortfalls(jobs: list[Job]) -> list[tuple[str, int, int]]:
    res = []
    for d, need in needs(jobs).items():
        try:
            free = shutil.disk_usage(d).free
        except OSError:
            continue
        if need * MARGIN + RESERVE > free:
            res.append((d, int(need), int(free)))
    return res
