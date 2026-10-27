"""Interpolate presets: built-in + user (JSON) + last used + default."""
from __future__ import annotations

import json

from .config import PRESETS_DIR
from .encoders import ENCODERS

USER_FILE = PRESETS_DIR / "user.json"
LAST_FILE = PRESETS_DIR / "last_v2.json"
DEFAULT_FILE = PRESETS_DIR / "default.json"

DEFAULT = dict(model="auto", fps_mode="keep", fps_value=0.0, slowmo=2.0, audio="natural", encoder="av1_nvenc",
               vmaf=95.0, q=None, crop="auto", scale=1.0)
DEFAULT_NAME = "Slow-mo 2×"
_SAME_SPEED = dict(slowmo=1.0, audio="copy")

# name: (options, tag, description)
BUILTIN: dict[str, tuple[dict, str, str]] = {
    "Slow-mo 2×": (DEFAULT, "Recommended", "Half speed, source frame rate, natural-pitch audio, AV1 at VMAF 95."),
    "Slow-mo 4×": ({**DEFAULT, "slowmo": 4.0}, "", "Quarter speed for dramatic shots."),
    "Super slow-mo 8×": ({**DEFAULT, "slowmo": 8.0}, "", "Extreme slow motion. Best on sharp or high-fps footage."),
    "Smooth 2× frame rate": ({**DEFAULT, **_SAME_SPEED, "fps_mode": "multiply", "fps_value": 2.0}, "",
                             "Same speed, twice the frames: 29.97 → 59.94, 30 → 60."),
    "Film to 60 fps": ({**DEFAULT, **_SAME_SPEED, "fps_mode": "multiply", "fps_value": 2.5}, "",
                       "23.976 fps film to 59.94 without judder."),
    "Quick 60 fps": ({**DEFAULT, **_SAME_SPEED, "fps_mode": "fixed", "fps_value": 60.0, "vmaf": 0.0}, "Fast",
                     "Constant quality, no sample test. Good for long files and drafts."),
    "Edit-ready ProRes · 2× slow-mo": ({**DEFAULT, "encoder": "prores", "vmaf": 0.0}, "Editing",
                                       "For Premiere, Resolve or Final Cut. Very large files."),
}


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def describe(o: dict) -> str:
    slow = float(o.get("slowmo", 1))
    enc = ENCODERS.get(o.get("encoder", ""))
    parts = [f"{slow:g}× slow-mo" if slow != 1 else "Real-time", enc.label if enc else "",
             f"VMAF {o['vmaf']:g}" if o.get("vmaf") else "constant quality"]
    return " · ".join(p for p in parts if p)


def user_presets() -> dict[str, dict]:
    return _read(USER_FILE, {})


def all_presets() -> list[tuple[str, dict, bool, str, str]]:
    """(name, options, builtin, tag, description)"""
    out = [(n, o, True, tag, d) for n, (o, tag, d) in BUILTIN.items()]
    out += [(n, o, False, "Yours", describe(o)) for n, o in sorted(user_presets().items())]
    return out


def get(name: str) -> dict | None:
    if name in BUILTIN:
        return dict(BUILTIN[name][0])
    return user_presets().get(name)


def save_user(name: str, opts: dict) -> None:
    data = user_presets(); data[name] = opts; _write(USER_FILE, data)


def delete_user(name: str) -> None:
    data = user_presets(); data.pop(name, None); _write(USER_FILE, data)


def load_last() -> dict | None:
    return _read(LAST_FILE, None)


def save_last(opts: dict) -> None:
    _write(LAST_FILE, opts)


def get_default() -> str | None:
    d = _read(DEFAULT_FILE, {})
    name = d.get("name") if isinstance(d, dict) else None
    return name if name and get(name) else None


def set_default(name: str | None) -> None:
    _write(DEFAULT_FILE, {"name": name})
