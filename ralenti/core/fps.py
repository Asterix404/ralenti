"""Frame-rate math. Rates stay exact Fractions (29.97 = 30000/1001)."""
from __future__ import annotations

from fractions import Fraction

NTSC_BASES = (24, 30, 48, 60, 120, 240)


def snap_rate(value: float) -> Fraction:
    """59.94 -> 60000/1001, 23.976 -> 24000/1001, 60 -> 60."""
    for n in NTSC_BASES:
        f = Fraction(n * 1000, 1001)
        if abs(value - float(f)) < 0.005:
            return f
    return Fraction(value).limit_denominator(1000)


def target_fps(source: Fraction, mode: str, value: float = 2.0) -> Fraction | None:
    """mode: 'multiply' (2x of 29.97 = 59.94 exactly), 'fixed' (60), 'keep'."""
    if mode == "multiply" and source:
        return source * Fraction(value).limit_denominator(100)
    if mode == "fixed":
        return snap_rate(value)
    return source or None
