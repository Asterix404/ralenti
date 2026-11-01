"""Multi-segment trim. Cuts split the timeline into segments; 'drop' lists the removed segments by index.
Kept segments are joined in timeline order. A legacy single range (start/end) is still understood."""
from __future__ import annotations

EPS = 1e-3


def _cuts(trim: dict, duration: float) -> list[float]:
    return sorted({round(float(c), 3) for c in (trim.get("cuts") or []) if EPS < float(c) < duration - EPS})


def to_cuts(trim: dict, duration: float) -> dict:
    """Converts a legacy start/end range into cuts + dropped segments."""
    t = dict(trim)
    if t.get("cuts") or t.get("drop"):
        return t
    a, b = t.get("start"), t.get("end")
    cuts, drop = [], []
    if a and float(a) > EPS:
        cuts.append(float(a)); drop.append(0)
    if b is not None and float(b) < duration - EPS:
        cuts.append(float(b)); drop.append(len(cuts))
    t.update(cuts=cuts, drop=drop, start=None, end=None)
    return t


def segments(trim: dict, duration: float) -> list[tuple[float, float, bool]]:
    edges = [0.0, *_cuts(trim, duration), float(duration)]
    drop = {int(d) for d in (trim.get("drop") or [])}
    return [(edges[i], edges[i + 1], i not in drop) for i in range(len(edges) - 1)]


def kept_ranges(trim: dict, duration: float) -> list[tuple[float, float]]:
    """Source ranges to keep, in order. Adjacent kept segments are merged."""
    duration = float(duration or 0)
    if not trim.get("on"):
        return [(0.0, duration)]
    if trim.get("cuts") or trim.get("drop"):
        out: list[tuple[float, float]] = []
        for a, b, keep in segments(trim, duration):
            if not keep or b - a <= EPS:
                continue
            if out and abs(out[-1][1] - a) <= EPS:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
        return out
    a = max(0.0, float(trim.get("start") or 0.0))
    b = min(duration, float(trim["end"]) if trim.get("end") is not None else duration)
    return [(a, b)] if b - a > EPS else []


def add_cut(trim: dict, t: float, duration: float) -> dict:
    tr = to_cuts(trim, duration)
    tr["on"] = True
    cuts, t = _cuts(tr, duration), round(float(t), 3)
    if not EPS < t < duration - EPS or any(abs(c - t) < 0.05 for c in cuts):
        tr["cuts"] = cuts
        return tr
    i = sum(1 for c in cuts if c < t)                 # the segment being split
    drop = {int(d) for d in tr.get("drop") or []}
    new = {d + 1 if d > i else d for d in drop}
    if i in drop:
        new.add(i + 1)                                # both halves of a removed segment stay removed
    tr["cuts"], tr["drop"] = sorted(cuts + [t]), sorted(new)
    return tr


def remove_cut(trim: dict, k: int, duration: float) -> dict:
    """Removes cut k: segments k and k+1 merge (kept if either was kept)."""
    tr = to_cuts(trim, duration)
    cuts = _cuts(tr, duration)
    if not 0 <= k < len(cuts):
        return tr
    drop = {int(d) for d in tr.get("drop") or []}
    both_removed = k in drop and k + 1 in drop
    new = {d if d < k else d - 1 for d in drop if d < k or d > k + 1}
    if both_removed:
        new.add(k)
    cuts.pop(k)
    tr["cuts"], tr["drop"] = cuts, sorted(new)
    return tr


def toggle(trim: dict, i: int, duration: float) -> dict:
    tr = to_cuts(trim, duration)
    tr["on"] = True
    tr["drop"] = sorted({int(d) for d in tr.get("drop") or []} ^ {int(i)})
    return tr


def boundaries(ranges) -> list[float]:
    """Where the joined segments meet, in output time."""
    out, t = [], 0.0
    for a, b in ranges[:-1]:
        t += b - a
        out.append(round(t, 3))
    return out


def source_to_output(t: float, ranges) -> float:
    acc = 0.0
    for a, b in ranges:
        if t < a:
            return acc
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return acc


def output_to_source(t: float, ranges) -> float:
    acc = 0.0
    for a, b in ranges:
        if t <= acc + (b - a):
            return a + (t - acc)
        acc += b - a
    return ranges[-1][1] if ranges else t
