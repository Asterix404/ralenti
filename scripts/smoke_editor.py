"""Smoke test for the editor. Builds the window offscreen, visits every step, checks names the editor relies on and,
with a video, runs a real edit: cuts, a preset applied through the preset bar, undo, compile_job for Copy, re-encode
and interpolation. Nothing is queued, and your autosaved edit is put back at the end.
   python scripts\\smoke_editor.py [video.mp4]"""
import os
import sys
import traceback
from collections import Counter
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
PKG = str(ROOT / "ralenti").lower()
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import qInstallMessageHandler  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

results, slot_errors = [], []
painter_warn, empty_icons, qt_other = Counter(), Counter(), Counter()


def origin(n=4) -> str:
    fr = [f for f in traceback.extract_stack() if f.filename.lower().startswith(PKG)]
    return " <- ".join(f"{f.filename[len(PKG) + 1:]}:{f.lineno} {f.name}" for f in reversed(fr[-n:]))


def qt_handler(mode, ctx, msg):
    cat = getattr(ctx, "category", "") or ""
    if msg.startswith("QPainter::begin"):
        painter_warn[origin() or "(painted from the event loop, see zero-size icons below)"] += 1
    elif msg.startswith(("QPainter::", "QFontDatabase", "Note that Qt no longer ships fonts", "class QRhi")) \
            or cat.startswith("qt.multimedia"):
        pass
    else:
        qt_other[msg[:140]] += 1


def patch_qtawesome():
    """Remembers where each icon was created and counts requests for a zero-size pixmap."""
    try:
        from qtawesome.iconic_font import CharIconEngine
    except Exception as ex:
        results.append(("info", "qtawesome probe", f"unavailable ({ex})"))
        return
    init, pix, clone = CharIconEngine.__init__, CharIconEngine.pixmap, getattr(CharIconEngine, "clone", None)

    def _init(self, *a, **k):
        init(self, *a, **k)
        self._ralenti_origin = origin()

    def _pix(self, size, mode, state):
        if size.width() <= 0 or size.height() <= 0:
            empty_icons[getattr(self, "_ralenti_origin", "") or "(unknown)"] += 1
        return pix(self, size, mode, state)

    CharIconEngine.__init__, CharIconEngine.pixmap = _init, _pix
    if clone:
        def _clone(self):
            c = clone(self)
            c._ralenti_origin = getattr(self, "_ralenti_origin", "")
            return c
        CharIconEngine.clone = _clone


def hook(t, v, tb):
    slot_errors.append("".join(traceback.format_exception(t, v, tb)).strip().splitlines())


def check(label, fn):
    try:
        r = fn()
        results.append(("ok  ", label, "" if r is None else str(r)[:120]))
        return r
    except Exception as ex:
        tb = traceback.extract_tb(ex.__traceback__)
        where = next((f"{Path(f.filename).name}:{f.lineno}" for f in reversed(tb)), "")
        results.append(("FAIL", label, f"{type(ex).__name__}: {str(ex)[:160]}  [{where}]"))
        return None


def expect(cond, detail):
    if not cond:
        raise AssertionError(detail)
    return detail


def has(label, obj, names):
    missing = [n for n in names if not hasattr(obj, n)]
    results.append(("ok  " if not missing else "FAIL", label, ", ".join(missing) or "all present"))


def pump(app, n=6):
    for _ in range(n):
        app.processEvents()


def run(app, video):
    from ralenti.core import edit_presets as P
    from ralenti.core.config import Settings, ensure_dirs
    from ralenti.core.queue import QueueManager
    from ralenti.core.tools import Toolchain
    from ralenti.ui import theme
    from ralenti.ui.widgets import pickers

    ensure_dirs()
    s = Settings.load()
    tc = Toolchain.resolve(s)
    qm = QueueManager(tc, s)
    check("theme.apply", lambda: theme.apply(app, getattr(s, "accent", None)))
    has("names in edit_presets", P, ["BUILTIN", "all_presets", "get", "diff", "apply", "save_user", "delete_user",
                                     "get_default", "set_default"])
    has("names in QueueManager", qm, ["settings", "tc", "rates", "add", "start"])
    has("names in Settings", s, ["name_template", "edit_preset_noconfirm", "sidebar_collapsed"])
    has("QualityPicker attrs", pickers.QualityPicker(), ["seg", "custom", "_sync", "set_encoder"])

    from ralenti.ui.main_window import MainWindow
    win = check("MainWindow()", lambda: MainWindow(s, qm))
    if win is None:
        return
    win.show(); pump(app)
    has("PlayerPanel attrs", win.editor.player, ["empty_text", "ph_btn", "keys", "autoplay", "_pending_seek", "view"])
    for key in ("trim", "crop", "motion", "audio", "encode", "output", "queue", "live", "downloads", "settings",
                "about", "motion"):
        check(f"go({key})", lambda k=key: (win.go(k), pump(app))[1])
    if not video:
        results.append(("skip", "real edit", "pass a video path to test cuts, presets and compile_job"))
        return

    ed, ses = win.editor, win.editor.session
    from ralenti.core.edit import compile_job
    from ralenti.core.segments import add_cut, kept_ranges, toggle

    check("clear clips", lambda: (ses.set_clips([]), pump(app))[1])
    check("clearing resets the cuts", lambda: expect(not (ses.edit.trim.get("cuts") or [])
                                                     and not ses.edit.trim.get("on"), ses.edit.trim))
    check("add_files", lambda: (ed.add_files([os.path.abspath(video)]), pump(app, 10), ses.edit.clips)[2])
    m = check("media()", lambda: ses.media())
    if not m:
        return
    d = m.duration
    win.go("trim"); pump(app)
    check("add 2 cuts", lambda: ed._set_trim(add_cut(add_cut(ses.edit.trim, d / 3, d), 2 * d / 3, d)))
    pump(app)
    check("segment bar shows 3 segments", lambda: expect(len(ed.segbar.segs) == 3, ed.segbar.segs))
    check("remove the middle segment", lambda: ed._set_trim(toggle(ses.edit.trim, 1, d)))
    pump(app)
    check("2 kept ranges", lambda: expect(len(kept_ranges(ses.edit.trim, d)) == 2, kept_ranges(ses.edit.trim, d)))
    check("side panel lists 3 rows", lambda: expect(ed.panels["trim"].rows.count() == 3,
                                                    ed.panels["trim"].rows.count()))
    for key in ("crop", "motion", "audio", "encode", "output"):
        check(f"go({key}) with video", lambda k=key: (win.go(k), pump(app))[1])
    out = ed.panels["output"]
    check("Output: insert {slowmo} chip", lambda: (out._insert("slowmo"), pump(app),
                                                   out.preview.text().splitlines()[0])[2])
    check("Output: container MKV", lambda: (ses.set("output", container="mkv"), pump(app),
                                            expect(out.preview.text().splitlines()[0].endswith(".mkv"),
                                                   out.preview.text().splitlines()[0]))[2])

    bar = ed.presets
    pick = next((g[0] for g in P.all_presets() if P.diff(ses.edit, P.get(g[0]), lambda c: c)), None)
    if pick is None:
        results.append(("info", "preset apply", "every preset already matches this edit"))
    else:
        flag = getattr(s, "edit_preset_noconfirm", False)
        s.edit_preset_noconfirm = True  # in memory only: skips the dialog for the test
        try:
            before = ses.edit.to_dict()
            check(f"apply '{pick}' via preset bar", lambda: (bar._pick(bar.combo.findData(pick)), pump(app),
                                                             bar.combo.currentText())[2])
            check("preset bar keeps the name", lambda: expect(bar._name == pick, bar._name))
            check("Ctrl+Z restores the edit", lambda: (ses.undo(), pump(app),
                                                       expect(ses.edit.to_dict() == before, "edit differs"))[2])
            check("picker after undo", lambda: bar.combo.currentText())
        finally:
            s.edit_preset_noconfirm = flag

    def kinds():
        return [st.kind for st in compile_job(tc, s, ses.edit).steps]

    ses.set("motion", on=False); ses.set("crop", on=False); ses.set("encode", video="copy"); pump(app)
    k = check("compile_job: Copy, 2 segments", kinds)
    if k is not None and {"interpolate", "encode", "vmaf_search"} & set(k):
        results.append(("warn", "Copy still lists AI/encode steps", "tell me: they may be skipped at run time"))
    ses.set("encode", video="encode"); pump(app)
    check("compile_job: re-encode", kinds)
    ses.set("motion", on=True); pump(app)
    k = check("compile_job: interpolation", kinds)
    if k is not None:
        check("interpolation has an interpolate step", lambda: expect("interpolate" in k, k))

    n = 0
    while ses.can_undo() and n < 300:
        ses.undo(); n += 1
    pump(app)
    results.append(("ok  ", "undo all", f"{n} steps"))

    from shiboken6 import delete
    ses._timer.stop()
    delete(ses)
    before = len(slot_errors)
    out.name.editingFinished.emit(); pump(app)
    check("field commit after the editor is gone", lambda: expect(len(slot_errors) == before, "raised"))


def report():
    print()
    for status, label, detail in results:
        print(f"{status}  {label:<40} {detail}")
    for title, c in (("zero-size icon requests (icon created at)", empty_icons),
                     ("QPainter::begin on a null pixmap (Ralenti code on the stack)", painter_warn),
                     ("other Qt messages", qt_other)):
        if c:
            print(f"\n{title}:")
            for k, v in c.most_common(12):
                print(f"  {v:4}x  {k}")
    for err in slot_errors:
        print("\nFAIL  exception in a slot:\n  " + "\n  ".join(err[-5:]))
    fails = sum(r[0] == "FAIL" for r in results) + len(slot_errors)
    print(f"\n{fails} failure(s)" if fails else "\nall good")


def main():
    qInstallMessageHandler(qt_handler)
    patch_qtawesome()
    sys.excepthook = hook
    app = QApplication(sys.argv[:1])
    from ralenti.ui.editor import session as session_mod
    autosave = Path(session_mod.AUTOSAVE)
    backup = autosave.read_bytes() if autosave.exists() else None
    try:
        run(app, sys.argv[1] if len(sys.argv) > 1 else None)
    except Exception:
        results.append(("FAIL", "smoke test itself", traceback.format_exc().strip().splitlines()[-1]))
    finally:
        if backup is not None:
            autosave.write_bytes(backup)
        elif autosave.exists():
            autosave.unlink()
    report()


if __name__ == "__main__":
    main()