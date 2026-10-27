"""Optional extensions. A package named ralenti_ext, installed into %APPDATA%\\Ralenti\\extensions
(Settings > Extensions), is loaded at startup and its register(api) called once. A broken extension
never stops Ralenti: the error is shown in Settings and written to logs\\extensions.log."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

from . import config


class ExtensionAPI:
    def __init__(self):
        self.name = ""
        self.engines = []
        self.settings_sections = []   # factory(settings_page) -> (key, title, icon, widget)
        self.setup_rows = []          # factory(setup_dialog) -> widget with refresh()
        self.disable_updates = False
        self.update_hint = ""         # shown instead of the Download button when an update is found
        self.error = ""

    def register_engine(self, engine) -> None:
        self.engines.append(engine)

    def add_settings_section(self, factory) -> None:
        self.settings_sections.append(factory)

    def add_setup_row(self, factory) -> None:
        self.setup_rows.append(factory)


_api: ExtensionAPI | None = None


def pkg_dir() -> Path:
    return config.EXT_DIR / "ralenti_ext"


def _is_link(p: Path) -> bool:
    try:
        return os.path.islink(p) or getattr(os.path, "isjunction", lambda _x: False)(p)
    except OSError:
        return False


def installed() -> bool:
    try:
        return (pkg_dir() / "__init__.py").is_file()
    except OSError:
        return False


def _log_error() -> str:
    traceback.print_exc()
    msg = traceback.format_exc().strip().splitlines()[-1]
    try:
        config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.LOGS_DIR / "extensions.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}\n{traceback.format_exc()}")
    except OSError:
        pass
    return msg


def load() -> ExtensionAPI:
    global _api
    if _api is not None:
        return _api
    _api = ExtensionAPI()
    if os.environ.get("RALENTI_NO_EXTENSIONS"):
        return _api
    if _is_link(pkg_dir()):
        _api.error = "The extension folder is a link, which Windows may refuse to open. Reinstall it with Install."
        return _api
    if installed() and str(config.EXT_DIR) not in sys.path:
        sys.path.append(str(config.EXT_DIR))     # an in-tree copy (development) still takes precedence
    try:
        import ralenti_ext
        ralenti_ext.register(_api)
    except ModuleNotFoundError as e:
        if e.name != "ralenti_ext":
            _api = ExtensionAPI(); _api.error = _log_error()
    except Exception:
        _api = ExtensionAPI(); _api.error = _log_error()
    return _api


def _find_pkg(root: Path) -> Path | None:
    if root.name == "ralenti_ext" and (root / "__init__.py").is_file():
        return root
    for p in [root / "ralenti_ext", *root.glob("*/ralenti_ext")]:
        if (p / "__init__.py").is_file():
            return p
    return None


def remove() -> None:
    p = pkg_dir()
    if _is_link(p):
        os.rmdir(p) if not os.path.islink(p) else os.unlink(p)
    elif p.exists():
        shutil.rmtree(p)


def install(src: str) -> None:
    """Copies an extension (a folder containing ralenti_ext, the ralenti_ext folder itself, or a .zip)."""
    config.EXT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=config.EXT_DIR, prefix=".staging-"))
    try:
        s = Path(src)
        if s.is_file() and s.suffix.lower() == ".zip":
            with zipfile.ZipFile(s) as z:
                z.extractall(tmp / "zip")
            s = tmp / "zip"
        pkg = _find_pkg(s)
        if pkg is None:
            raise RuntimeError("No ralenti_ext folder with an __init__.py was found there.")
        new = tmp / "ralenti_ext"
        shutil.copytree(pkg, new, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        remove()
        new.rename(pkg_dir())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
