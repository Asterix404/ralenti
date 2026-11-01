"""Checks a Ralenti extension against this version of Ralenti: leftovers from removed pages, how it patches Ralenti,
the engine contract (rotation/flip, scene cuts, parts), its Settings and setup hooks and, with --video, that an edit
and a preview using each of its models still compile. Nothing is rendered.
   python scripts\check_extension.py [EXT_FOLDER] [--video FILE]"""
import inspect
import os
import re
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NEEDLES = ("interpolate_page", "trim_page", "merge_page", "encode_page", "InterpolatePage", "TrimPage", "MergePage",
           "EncodePage", "old_interp", "old_trim", "old_merge", "old_encode")
PATCH = re.compile(r"^\s*((?!self\.)[A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)+|[A-Za-z_]\w*$$[^$$]+$$)\s*=[^=]")
SKIP = {".git", "__pycache__", ".venv", "venv", "build", "dist", ".pytest_cache"}
results = []


def add(status, label, detail=""):
    results.append((status, label, str(detail)[:150]))


def scan(folder: Path) -> None:
    hits, patches = [], []
    for p in sorted(folder.rglob("*.py")):
        if any(x in SKIP for x in p.relative_to(folder).parts):
            continue
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if any(x in line for x in NEEDLES):
                hits.append(f"{p.relative_to(folder)}:{n}: {line.strip()}")
            if PATCH.match(line) and not line.startswith((" " * 8, "\t\t")):
                patches.append(f"{p.relative_to(folder)}:{n}: {line.strip()}")
    add("ok  " if not hits else "FAIL", f"removed pages referenced in {folder.name}",
        "none" if not hits else f"{len(hits)} line(s), listed below")
    for h in hits[:30]:
        add("    ", "", h)
    if patches:
        add("info", "assignments into Ralenti (check these still exist)", f"{len(patches)} line(s)")
        for h in patches[:40]:
            add("    ", "", h)


def main():
    args = sys.argv[1:]
    video = None
    if "--video" in args:
        i = args.index("--video")
        video = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv[:1])
    from ralenti.core import extensions
    from ralenti.core.config import Settings, ensure_dirs
    from ralenti.core.queue import QueueManager
    from ralenti.core.tools import Toolchain

    ensure_dirs()
    folders = [Path(a) for a in args] or [Path(r"C:\Users\byte\Dev\ralenti-ext")]
    try:
        pkg = Path(extensions.pkg_dir())
        add("info", "installed extension folder", f"{pkg} · installed={extensions.installed()}")
        if pkg.exists() and not any(pkg.resolve() == f.resolve() for f in folders if f.exists()):
            folders.append(pkg)
    except Exception as ex:
        add("warn", "installed extension folder", ex)
    for f in folders:
        if f.exists():
            scan(f)
        else:
            add("skip", f"source scan {f}", "folder not found")

    api = extensions.load()
    add("ok  " if not api.error else "FAIL", "extension loads", api.error or (api.name or "(no name)"))
    add("info", "provides", f"{len(api.engines)} engine(s), {len(api.settings_sections)} Settings section(s), "
                            f"{len(getattr(api, 'setup_rows', []) or [])} setup row(s)")
    s = Settings.load()
    tc = Toolchain.resolve(s)
    qm = QueueManager(tc, s)
    expected = ("r", "tc", "ctl", "enc", "out", "q", "span")
    models = []
    for eng in api.engines:
        tag = f"engine {getattr(eng, 'name', '?')}"
        try:
            avail = eng.available(tc)
            ms = eng.models(tc) if avail else []
            models += [(eng, m) for m in ms[:2]]
            add("ok  ", f"{tag}: available / models", f"{avail} · {', '.join(m.code for m in ms[:6]) or 'none'}")
        except Exception as ex:
            add("FAIL", f"{tag}: available / models", f"{type(ex).__name__}: {ex}")
        try:
            params = list(inspect.signature(eng.render).parameters)
            ok = all(p in params for p in expected) or any(
                inspect.signature(eng.render).parameters[p].kind == inspect.Parameter.VAR_KEYWORD for p in params)
            add("ok  " if ok else "FAIL", f"{tag}: render() signature", ", ".join(params))
        except (TypeError, ValueError) as ex:
            add("warn", f"{tag}: render() signature", ex)
        try:
            src = inspect.getsource(type(eng))
        except (OSError, TypeError):
            src = ""
        if src:
            add("ok  " if "transform" in src else "FAIL", f"{tag}: applies Render.transform",
                "yes" if "transform" in src else "no: rotation and flip from Transform are lost in its renders, and "
                                                 "previews won't line up")
            add("ok  " if "hold_end" in src else "warn", f"{tag}: honours Render.hold_end",
                "yes" if "hold_end" in src else "no: frames may blend across a scene cut at a part boundary")
            seg = bool(getattr(eng, "supports_segments", False))
            if seg:
                add("ok  " if "limit" in src else "FAIL", f"{tag}: honours Render.limit (parts)",
                    "yes" if "limit" in src else "supports_segments is True but limit isn't used")
            else:
                add("info", f"{tag}: renders in one piece", "no resume by parts, no live view for this engine")
        else:
            add("warn", f"{tag}: source", "couldn't read the source to check the contract")

    from ralenti.ui.pages.settings_page import SettingsPage
    try:
        page = SettingsPage(qm, s)
        for fac in api.settings_sections:
            try:
                key, title, _ic, _w = fac(page)
                add("ok  ", f"Settings section '{title}'", key)
            except Exception:
                add("FAIL", "Settings section", traceback.format_exc().strip().splitlines()[-1])
    except Exception:
        add("FAIL", "Settings page with the extension", traceback.format_exc().strip().splitlines()[-1])
    try:
        from ralenti.ui.setup_dialog import SetupDialog
        SetupDialog(s, qm)
        add("ok  ", "setup dialog with the extension")
    except Exception:
        add("FAIL", "setup dialog with the extension", traceback.format_exc().strip().splitlines()[-1])

    if video and models:
        from ralenti.core import pipeline
        from ralenti.core.edit import Edit, compile_job
        from ralenti.core.encoders import ENCODERS
        enc = next((k for k, x in ENCODERS.items() if not x.hidden and tc.binary_for(x.codec)), None)
        for eng, m in models:
            e = Edit()
            e.clips = [os.path.abspath(video)]
            e.motion.update(on=True, model=m.code, slowmo=2.0)
            e.crop.update(on=True, crop="none", rotate=90)
            e.encode.update(video="encode", encoder=enc)
            try:
                job = compile_job(tc, s, e)
                add("ok  ", f"edit with {m.code} (rotated 90°)", " · ".join(st.kind for st in job.steps))
            except Exception as ex:
                add("FAIL", f"edit with {m.code}", f"{type(ex).__name__}: {ex}")
            try:
                job = pipeline.make_preview_job(tc, s, os.path.abspath(video), 1.0, 2.0, model=m.code,
                                                transform={"rotate": 90, "hflip": False, "vflip": False})
                add("ok  ", f"preview with {m.code}", " · ".join(st.kind for st in job.steps))
            except Exception as ex:
                add("FAIL", f"preview with {m.code}", f"{type(ex).__name__}: {ex}")
    elif video:
        add("skip", "edit / preview compile", "no extension model is available")

    print()
    for st, label, detail in results:
        print(f"{st}  {label:<44} {detail}")
    fails = sum(r[0] == "FAIL" for r in results)
    print(f"\n{fails} failure(s)" if fails else "\nall good")
    app.quit()


if __name__ == "__main__":
    main()
