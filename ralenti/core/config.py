"""Settings and app data paths (%APPDATA%\\Ralenti), plus one-time migration from the old name.
Unknown keys (from another build or an extension) are kept when saving."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APPDATA = Path(os.environ.get("APPDATA", Path.home()))
APP_DIR = APPDATA / "Ralenti"
LEGACY_DIRS = [APPDATA / ("T" "ween")]
SETTINGS_FILE = APP_DIR / "settings.json"
QUEUE_FILE = APP_DIR / "queue.json"
PRESETS_DIR = APP_DIR / "presets"
LOGS_DIR = APP_DIR / "logs"
TEMP_DIR = APP_DIR / "temp"
THUMB_DIR = APP_DIR / "thumbs"
TOOLS_DIR = APP_DIR / "tools"
EXT_DIR = APP_DIR / "extensions"


@dataclass
class Settings:
    full_ffmpeg: str = ""
    full_ffprobe: str = ""
    rife_exe: str = ""
    temp_dir: str = str(TEMP_DIR)
    gpu_device: str = "0"
    accent: str = "#2dd4bf"
    last_output_dir: str = ""
    intermediate: str = "ref_nvenc"
    setup_done: bool = False
    check_updates: bool = True
    update_repo: str = "Asterix404/ralenti"
    skipped_version: str = ""
    preview_length: float = 3.0
    window_geometry: str = ""
    collapsed_sections: str = ""
    ffmpeg_repo: str = ""
    rife_repo: str = ""
    sidebar_collapsed: bool = False
    concurrency: str = "auto"
    last_version: str = ""

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        known = {f.name for f in fields(cls)}
        s = cls(**{k: v for k, v in data.items() if k in known})
        s._extra = {k: v for k, v in data.items() if k not in known}
        return s

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        data = {**getattr(self, "_extra", {}), **asdict(self)}
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)


def _swap_paths(obj, old: str, new: str):
    if isinstance(obj, str):
        return new + obj[len(old):] if obj.lower().startswith(old.lower()) else obj
    if isinstance(obj, dict):
        return {k: _swap_paths(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_swap_paths(v, old, new) for v in obj]
    return obj


def migrate_legacy() -> bool:
    if APP_DIR.exists():
        return False
    legacy = next((d for d in LEGACY_DIRS if d.is_dir()), None)
    if legacy is None:
        return False
    try:
        shutil.move(str(legacy), str(APP_DIR))
    except OSError:
        shutil.copytree(legacy, APP_DIR, dirs_exist_ok=True)
    for name in ("settings.json", "queue.json"):
        f = APP_DIR / name
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        f.write_text(json.dumps(_swap_paths(data, str(legacy), str(APP_DIR)), indent=1), encoding="utf-8")
    shutil.rmtree(APP_DIR / "theme", ignore_errors=True)
    return True


def ensure_dirs() -> bool:
    """Creates app folders. Returns True if data was migrated from the old name."""
    migrated = migrate_legacy()
    for d in (APP_DIR, PRESETS_DIR, LOGS_DIR, TEMP_DIR, THUMB_DIR):
        d.mkdir(parents=True, exist_ok=True)
    return migrated
