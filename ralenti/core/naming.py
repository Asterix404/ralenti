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


def render(template: str, ctx: dict) -> str:
    def sub(m):
        k = m.group(1)
        return str(ctx[k]) if k in ctx and k != "n" else m.group(0)

    return _BAD.sub("_", re.sub(r"\{(\w+)\}", sub, template or "")).strip().rstrip(". ")


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
