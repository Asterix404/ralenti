"""Output file names from a template: {name}_{slowmo}x_{res} -> beach_2x_1920x1080."""
from __future__ import annotations

import re
from pathlib import Path

PLACEHOLDERS = {
    "name": "Source file name", "model": "AI model", "slowmo": "Slow-motion factor", "fps": "Output frame rate",
    "res": "Output size, e.g. 1920x1080", "encoder": "Encoder", "vmaf": "VMAF target (cq for constant quality)",
    "date": "Today's date", "time": "Current time", "n": "A number that makes the name unique",
}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


PLACEHOLDERS.update({
    "ai": "AI model, only when Interpolation is on",
    "frames": "Frame-rate change (2x, 60fps), only when there is one",
    "slow": "slow2x, only with slow motion",
    "target": "vmaf95, only with a VMAF target",
})
LABELLED = frozenset({"ai", "frames", "slow", "target"})
AUTO = "{name}_ralenti_{ai}_{frames}_{slow}_{target}"     # the automatic name, as a template


def frames_tag(m) -> str:
    m = m or {}
    fv = float(m.get("fps_value") or 0)
    return {"multiply": f"{fv:g}x", "fixed": f"{fv:g}fps"}.get(m.get("fps_mode") or "keep", "")


def render(template: str, ctx: dict) -> str:
    """Fills the placeholders. A labelled placeholder that doesn't apply disappears with the separator before it."""
    def sub(m):
        k = m.group(1)
        if k == "n" or k not in ctx:
            return m.group(0)
        v = str(ctx[k])
        return "\x00" if k in LABELLED and not v else v

    s = re.sub(r"\{(\w+)\}", sub, template or "")
    had = "\x00" in s
    s = re.sub(r"[_\- ]?\x00", "", s)
    s = _BAD.sub("_", s).strip()
    if had:
        s = s.strip("_- ")
    return s.rstrip(". ")


def resolve(folder, base: str, ext: str, overwrite: bool = False) -> Path:
    folder = Path(folder)
    if "{n}" in base:
        for n in range(1, 100000):
            p = folder / (base.replace("{n}", str(n)) + ext)
            if overwrite or not p.exists():
                return p
    p = folder / (base + ext)
    if overwrite or not p.exists():
        return p
    for n in range(1, 100000):
        c = folder / f"{base} ({n}){ext}"
        if not c.exists():
            return c
    return p
