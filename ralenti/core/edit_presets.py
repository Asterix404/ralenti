"""Presets for whole edits: each step's settings. Applying shows exactly what changes and can be limited to
some steps. What belongs to one video (cuts, a drawn crop, chosen tracks, folder, file name) is never touched."""
from __future__ import annotations

import copy
import json

from .config import PRESETS_DIR
from .edit import DEFAULTS, LABELS, STEPS, step_recipe
from .encoders import ENCODERS

USER_FILE = PRESETS_DIR / "edit_presets.json"
DEFAULT_FILE = PRESETS_DIR / "edit_default.json"

FIELDS = {
    "crop": {"on": "Transform", "crop": "Crop", "rotate": "Rotation", "hflip": "Flip horizontally",
             "vflip": "Flip vertically", "scale": "Scale", "max_h": "Fit within"},
    "motion": {"on": "Interpolation", "model": "AI model", "fps_mode": "Frame rate", "fps_value": "Frame rate value",
               "slowmo": "Slow motion", "scene": "Scene cuts", "engine_opts": "Engine options"},
    "audio": {"mode": "Sound", "codec": "Codec", "quality": "Quality", "slow": "Slow-motion audio"},
    "encode": {"video": "Video", "encoder": "Encoder", "vmaf": "VMAF target", "q": "Constant quality"},
    "output": {"container": "Container", "exists": "If the file exists"},
}
_INTERP = {"on": True, "model": "auto", "fps_mode": "keep", "fps_value": 0.0, "slowmo": 2.0, "scene": "normal"}
_AV1 = {"video": "encode", "encoder": "av1_nvenc", "vmaf": 95.0, "q": None}
BUILTIN: dict[str, tuple[dict, str, str]] = {
    "Slow-mo 2×": ({"motion": _INTERP, "audio": {"mode": "auto", "slow": "natural"}, "encode": _AV1},
                   "Recommended", "Half speed with the newest model, natural-pitch audio, AV1 at VMAF 95."),
    "Slow-mo 4×": ({"motion": {**_INTERP, "slowmo": 4.0}, "audio": {"mode": "auto", "slow": "natural"},
                    "encode": _AV1}, "", "Quarter speed for dramatic shots."),
    "Smooth 2× frame rate": ({"motion": {**_INTERP, "slowmo": 1.0, "fps_mode": "multiply", "fps_value": 2.0},
                              "audio": {"mode": "auto"}, "encode": _AV1}, "",
                             "Same speed, twice the frames: 29.97 → 59.94, 30 → 60."),
    "Film to 60 fps": ({"motion": {**_INTERP, "slowmo": 1.0, "fps_mode": "multiply", "fps_value": 2.5},
                        "audio": {"mode": "auto"}, "encode": _AV1}, "", "23.976 fps film to 59.94 without judder."),
    "Shrink · AV1 VMAF 93": ({"motion": {"on": False}, "audio": {"mode": "auto"},
                              "encode": {**_AV1, "vmaf": 93.0}}, "No AI",
                             "Re-encode only: much smaller files that still look like the original."),
    "Lossless trim": ({"motion": {"on": False}, "crop": {"on": False}, "audio": {"mode": "auto"},
                       "encode": {"video": "copy"}}, "Instant", "Only cuts and joins: no re-encode, no quality loss."),
    "Edit-ready ProRes": ({"encode": {"video": "encode", "encoder": "prores", "vmaf": None, "q": None},
                           "audio": {"mode": "convert", "codec": "pcm", "quality": "high"},
                           "output": {"container": "mov"}}, "Editing",
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


def clean(recipe: dict) -> dict:
    return {k: step_recipe(k, v) for k, v in (recipe or {}).items() if k in STEPS and k != "trim"}


def from_legacy(o: dict) -> dict:
    """An old Interpolate preset as a whole-edit recipe."""
    au = o.get("audio", "natural")
    audio = ({"mode": "remove"} if au == "drop" else {"mode": "copy"} if au == "copy"
             else {"mode": "auto", "slow": au if au in ("natural", "keep_pitch") else "natural"})
    scale = float(o.get("scale") or 1.0)
    return clean({
        "motion": {"on": True, "model": o.get("model", "auto"), "fps_mode": o.get("fps_mode", "keep"),
                   "fps_value": float(o.get("fps_value") or 0), "slowmo": float(o.get("slowmo") or 1),
                   "scene": o.get("scene", "normal"), "engine_opts": dict(o.get("engine_opts") or {})},
        "audio": audio,
        "encode": {"video": "encode", "encoder": o.get("encoder", "av1_nvenc"), "vmaf": o.get("vmaf") or None,
                   "q": o.get("q")},
        "crop": {"on": o.get("crop") == "auto" or scale < 0.999,
                 "crop": o.get("crop") if o.get("crop") in ("auto", "none") else "auto", "scale": scale},
    })


def user_presets() -> dict[str, dict]:
    if not USER_FILE.exists():
        try:
            from . import presets as old
            data = {n: from_legacy(o) for n, o in old.user_presets().items()}
        except Exception:
            data = {}
        _write(USER_FILE, data)
        return data
    return _read(USER_FILE, {})


def describe(recipe: dict) -> str:
    m, v = recipe.get("motion") or {}, recipe.get("encode") or {}
    bits = []
    if m.get("on") is False:
        bits.append("No interpolation")
    elif m.get("slowmo"):
        bits.append(f"{float(m['slowmo']):g}× slow-mo" if float(m["slowmo"]) != 1 else "Real-time")
    if v.get("video") == "copy":
        bits.append("copy, no re-encode")
    elif v.get("encoder") in ENCODERS:
        bits.append(ENCODERS[v["encoder"]].label + (f" · VMAF {v['vmaf']:g}" if v.get("vmaf") else ""))
    return " · ".join(bits) or "Custom settings"


def all_presets() -> list[tuple[str, dict, bool, str, str]]:
    """(name, recipe, builtin, tag, description)"""
    out = [(n, copy.deepcopy(r), True, tag, d) for n, (r, tag, d) in BUILTIN.items()]
    out += [(n, r, False, "Yours", describe(r)) for n, r in sorted(user_presets().items())]
    return out


def get(name: str) -> dict | None:
    if name in BUILTIN:
        return copy.deepcopy(BUILTIN[name][0])
    r = user_presets().get(name)
    return copy.deepcopy(r) if r is not None else None


def save_user(name: str, recipe: dict) -> None:
    data = user_presets(); data[name] = clean(recipe); _write(USER_FILE, data)


def delete_user(name: str) -> None:
    data = user_presets(); data.pop(name, None); _write(USER_FILE, data)


def get_default() -> str | None:
    d = _read(DEFAULT_FILE, {})
    name = d.get("name") if isinstance(d, dict) else None
    return name if name and get(name) is not None else None


def set_default(name: str | None) -> None:
    _write(DEFAULT_FILE, {"name": name})


def fmt(step: str, key: str, val, model_label=None) -> str:
    if isinstance(val, bool):
        return "On" if val else "Off"
    if val is None:
        return "Off"
    if key == "encoder":
        return ENCODERS[val].label if val in ENCODERS else str(val)
    if key == "model":
        return model_label(val) if model_label and val != "auto" else ("Best installed" if val == "auto" else str(val))
    if key == "slowmo":
        return f"{float(val):g}×"
    if key == "vmaf":
        return f"{float(val):g}" if val else "Off (constant quality)"
    if key == "rotate":
        return f"{int(val)}°"
    if key == "scale":
        return f"{round(float(val) * 100)}%"
    if key == "max_h":
        return f"{int(val)}p" if val else "Original"
    if key == "engine_opts":
        return ", ".join(f"{k}={v}" for k, v in (val or {}).items()) or "Defaults"
    if isinstance(val, float):
        return f"{val:g}"
    return str(val)


def diff(edit, recipe: dict, model_label=None) -> list[dict]:
    """[{step, title, changes: [(field, old, new)]}] for every step the preset would change."""
    out = []
    for step, r in clean(recipe).items():
        cur = getattr(edit, step)
        changes = [(FIELDS.get(step, {}).get(k, k), fmt(step, k, cur.get(k), model_label), fmt(step, k, v, model_label))
                   for k, v in r.items() if cur.get(k) != v]
        if changes:
            out.append({"step": step, "title": LABELS[step], "changes": changes})
    return out


def apply(edit, recipe: dict, steps=None):
    edit.apply_recipe(clean(recipe), steps)
    return edit
