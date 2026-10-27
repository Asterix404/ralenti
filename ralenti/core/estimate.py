"""Processing-time estimates: from your own finished jobs when available, otherwise from the benchmark."""
from __future__ import annotations

import json

from .config import APP_DIR
from .encoders import ENCODERS
from .fps import target_fps

BENCH_FILE = APP_DIR / "benchmark.json"
_cache: dict = {"mtime": None, "data": {}}


def load_bench() -> dict:
    try:
        m = BENCH_FILE.stat().st_mtime
    except OSError:
        return {}
    if _cache["mtime"] != m:
        try:
            _cache["data"] = json.loads(BENCH_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache["data"] = {}
        _cache["mtime"] = m
    return _cache["data"]


def save_bench(d: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    BENCH_FILE.write_text(json.dumps(d, indent=1), encoding="utf-8")


def bench_util(model):
    m = (load_bench().get("models") or {}).get(model or "") or {}
    return m.get("util")


def estimate(info, o: dict, size, start=None, end=None, rates=None, bench=None):
    """(seconds, 'past' | 'benchmark') for one interpolation job running alone, or None."""
    model = o.get("model")
    if not model or info is None or not info.fps:
        return None
    tgt = target_fps(info.fps, o.get("fps_mode", "keep"), o.get("fps_value") or 2.0) or info.fps
    d = max(0.0, (end if end is not None else info.duration) - (start or 0.0))
    slow = float(o.get("slowmo") or 1)
    w, h = size
    mpf = w * h * d * slow * float(tgt) / 1e6          # output megapixel-frames
    vm = bool(o.get("vmaf"))
    rate = (rates or {}).get(f"interpolate:{model}:{'vmaf' if vm else 'direct'}")
    if rate:
        return rate * mpf / 1000, "past"
    b = load_bench() if bench is None else bench
    m = (b.get("models") or {}).get(model) or {}
    if not m.get("mpfs"):
        return None
    enc = ENCODERS.get(o.get("encoder") or "")
    em = (b.get("encoders") or {}).get(o.get("encoder") or "")
    t = mpf / m["mpfs"]
    if vm and enc and em and b.get("vmaf"):
        sample = d * slow if d * slow <= 30 else 20.0
        smpf = w * h * sample * float(tgt) / 1e6
        lo, hi = enc.q_range
        iters = (hi - lo + 1).bit_length()
        t += iters * (smpf / em + smpf / b["vmaf"]) + mpf / em
    elif em:
        t = max(t, mpf / em)                           # AI and encoder run as one pipeline
    return t + 5, "benchmark"


def describe(est) -> str | None:
    from .progress import fmt_eta
    if est is None:
        return None
    secs, src = est
    return f"≈ <b>{fmt_eta(secs)}</b> · " + ("from your past jobs" if src == "past" else "from your benchmark")
