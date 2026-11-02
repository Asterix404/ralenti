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
    print(f"  ... {label}", flush=True)
    try:
        r = fn()
        results.append(("ok  ", label, "" if r is None else str(r)[:120]))
        return r
    except Exception as ex:
        tb = traceback.extract_tb(ex.__traceback__)
        where = next((f"{Path(f.filename).name}:{f.lineno}" for f in reversed(tb)), "")
        results.append(("FAIL", label, f"{type(ex).__name__}: {str(ex)[:160]}  [{where}]"))
        return None


def expect(cond, detail, fail=None):
    if not cond:
        raise AssertionError(fail if fail is not None else detail)
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
    MainWindow._whats_new = lambda self: None                  # modal dialogs would block an offscreen test
    MainWindow.check_updates = lambda self, manual=False: None
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
                                                       expect(ses.edit.to_dict() == before, "identical", "edit differs"))[2])
            check("picker after undo", lambda: bar.combo.currentText())
        finally:
            s.edit_preset_noconfirm = flag

    # ---- batch 3: tool rows, live transform, chips, scene cuts (starts from a clean transform) ----
    win.go("crop"); pump(app)
    check("crop tool row is showing", lambda: expect(ed.tool_stack.currentWidget() is ed.rows["crop"]
                                                       and not ed.tool_stack.isHidden(), "yes", "wrong or hidden"))
    check("reset transform first", lambda: (ed._reset_transform(), pump(app),
                                            expect(int(ses.edit.crop.get("rotate") or 0) == 0
                                                   and not ses.edit.crop.get("hflip"), "clean", str(ses.edit.crop)))[2])
    check("rotate right (toolbar)", lambda: (ed.c_rr.click(), pump(app),
                                             expect(int(ses.edit.crop.get("rotate") or 0) % 360 == 90, "90°",
                                                    str(ses.edit.crop)))[2])
    check("player picture is rotated", lambda: expect(abs(ed.player.view.holder.transform().m11()) < 0.5,
                                                      "rotated", "not rotated"))
    check("flip horizontally (toolbar)", lambda: (ed.c_hf.click(), pump(app),
                                                  expect(ses.edit.crop.get("hflip") is True, "on",
                                                         str(ses.edit.crop)))[2])
    check("Before shows the source", lambda: (ed.c_before.click(), pump(app),
                                              expect(abs(ed.player.view.holder.transform().m11() - 1) < 1e-6,
                                                     "untouched", "still transformed"), ed.c_before.click())[2])
    win.go("motion"); pump(app)
    check("chips show Trim and Transform", lambda: expect({"trim", "crop"} <= {x[0] for x in ed._chip_items},
                                                          [x[2] for x in ed._chip_items], "missing chips"))
    check("Interpolation keeps the rotation", lambda: expect(abs(ed.player.view.holder.transform().m11()) < 0.5,
                                                             "rotated", "not rotated"))
    ses.set("motion", scene="normal"); pump(app)
    check("scene cut toggle adds", lambda: (ed._goto(6.0, play=False), pump(app, 20), ed._toggle_scene(), pump(app),
                                            expect(len(ses.edit.motion.get("cuts_add") or []) == 1,
                                                   ses.edit.motion.get("cuts_add"), "nothing added"))[4])
    check("timeline shows the mark", lambda: expect(any(k == "added" for _t, k in ed.player.timeline.marks),
                                                    ed.player.timeline.marks, "no mark"))
    check("scene cut toggle removes", lambda: (ed._toggle_scene(), pump(app),
                                               expect(not ses.edit.motion.get("cuts_add"), "removed",
                                                      str(ses.edit.motion.get("cuts_add"))))[2])

    # ---- batch 3b: previews ----
    win.go("motion"); pump(app)
    check("compare controls on Interpolation", lambda: expect(not ed.player.cmp.isHidden(), "visible", "hidden"))
    check("Previews tab", lambda: (ed._motion_tab("previews"), pump(app),
                                   expect(ed.m_pages.currentIndex() == 1, "shown", "not shown"),
                                   ed._motion_tab("settings"))[2])
    from ralenti.core.engines import resolve_model
    if resolve_model(tc, ses.edit.motion.get("model")):
        check("preview job follows Transform",
              lambda: (lambda j: expect(j.spec["transform"]["rotate"] == 90 and j.spec["transform"]["hflip"],
                                        [st.kind for st in j.steps], str(j.spec["transform"])))(
                  ed.previews.build_job(ed.cur_clip, 1.0, 2.0)))
    else:
        results.append(("skip", "preview job", "no AI engine installed"))
    win.go("audio"); pump(app)
    check("compare controls hidden elsewhere", lambda: expect(ed.player.cmp.isHidden(), "hidden", "visible"))

    # ---- batch 4a: Audio and Encode workspaces ----
    from ralenti.core.config import TEMP_DIR as _TMP
    win.go("audio"); pump(app, 10)
    check("Audio replaces the player", lambda: expect(ed.work.currentWidget() is ed.audio_view, "yes", "player shown"))
    if ses.info(ed.cur_clip).has_audio:
        check("audio track rows", lambda: expect(len(ed.audio_view.rows) >= 1, len(ed.audio_view.rows), "no rows"))
        from ralenti.core.waveform import peaks as _peaks
        check("waveform", lambda: (lambda r: expect(bool(r[0]), f"{len(r[0])} peaks · {r[1]:.1f} s", "empty"))(
            _peaks(tc.ffmpeg, ed.cur_clip, 0, 400, ses.media().duration)))
    else:
        results.append(("skip", "audio tracks", "this video has no sound"))
    win.go("encode"); pump(app)
    check("Encode replaces the player", lambda: expect(ed.work.currentWidget() is ed.encode_view, "yes", "player"))
    check("encoder comparison rows", lambda: expect(len(ed.encode_view.enc_rows) > 0,
                                                    list(ed.encode_view.enc_rows), "none"))
    check("size estimate", lambda: expect(ed.encode_view.st_size.value.text() not in ("", "–"),
                                          ed.encode_view.st_size.value.text(), "empty"))
    from ralenti.core.encoders import ENCODERS as _ENC
    from ralenti.core.quality_test import run_test as _rt
    _e = next((x for x in _ENC.values() if not x.hidden and x.has_quality and tc.binary_for(x.codec)), None)
    if _e:
        check(f"quality test (1.5 s, {_e.label})",
              lambda: (lambda r: f"VMAF {r['vmaf']} · {r['bps'] * 8 / 1e6:.1f} Mb/s · stills {r['width']}x{r['height']}")(
                  _rt(tc, ses.info(ed.cur_clip), 1.0, 1.5, _e, None, None, ["transpose=clock"],
                      Path(s.temp_dir or _TMP) / "smoke_quality")))
    win.go("motion"); pump(app)
    check("player back on Interpolation", lambda: expect(ed.work.currentWidget() is ed.player, "yes", "workspace"))

    # ---- batch 4b: Output workspace ----
    win.go("output"); pump(app)
    check("Output replaces the player", lambda: expect(ed.work.currentWidget() is ed.output_view, "yes", "player"))
    check("folder view shows the new file", lambda: expect(ed.output_view.new_item is not None,
                                                           ed.output_view.new_item.text(0) if ed.output_view.new_item
                                                           else "", "missing"))

    # ---- 0.14 batch 5a: conflicts, greyed options, tabs, summary, transformed Trim ----
    win.go("crop"); pump(app)
    check("Transform has tabs", lambda: expect("crop" in ed.side_tabs, "yes", "no tabs"))
    check("Rotate & flip tab", lambda: (ed._side_tab("crop", "rotate"), pump(app),
                                        expect(not ed.panels["crop"].groups["rotate"].isHidden(), "shown", "hidden"),
                                        ed._side_tab("crop", "crop"))[2])
    ses.set("motion", on=True); ses.set("encode", video="copy"); pump(app, 10)
    check("Copy + Interpolation is a conflict", lambda: expect(
        any(i["step"] == "encode" and i["level"] == "error" for i in ed._issues), "blocked", str(ed._issues)))
    check("Add to queue is blocked", lambda: expect(not ed.summary.btn.isEnabled(), ed.summary.btn.text(), "enabled"))
    check("sidebar shows the problem", lambda: expect(ed.dots().get("encode") == "error", "red dot", str(ed.dots())))

    def _refuses():
        try:
            compile_job(tc, s, ses.edit)
        except ValueError as ex:
            return str(ex)[:80]
        raise AssertionError("compiled anyway")

    check("compile_job refuses it", _refuses)
    check("fix button: Re-encode", lambda: (ed._fix(("Re-encode", "encode", {"video": "encode"})), pump(app, 10),
                                            expect(not any(i["level"] == "error" and i["step"] == "encode"
                                                           for i in ed._issues), "fixed", str(ed._issues)))[2])
    win.go("encode"); pump(app)
    check("Copy greyed with Interpolation on", lambda: expect(
        not ed.panels["encode"].video.group.button(1).isEnabled(), "greyed", "enabled"))
    check("summary collapses", lambda: (ed.summary._toggle(), pump(app),
                                        expect(ed.summary.body.isHidden(), "collapsed", "still open"),
                                        ed.summary._toggle())[2])
    ses.set("crop", on=True, rotate=90); win.go("trim"); pump(app)
    check("Trim shows the rotated picture", lambda: expect(abs(ed.player.view.holder.transform().m11()) < 0.5,
                                                           "rotated", "not rotated"))
    check("Picture chip on Trim", lambda: expect(any(x[0] == "crop" for x in ed._chip_items),
                                                 [x[2] for x in ed._chip_items], "missing"))

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
    check("field commit after the editor is gone", lambda: expect(len(slot_errors) == before, "no exception", "raised"))


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
    import faulthandler
    from PySide6.QtCore import QTimer
    faulthandler.dump_traceback_later(int(os.environ.get("SMOKE_TIMEOUT", "300")), exit=True)

    def close_modals():
        w = QApplication.activeModalWidget()
        if w is not None:
            results.append(("FAIL", "a dialog opened and blocked the test", f"{type(w).__name__}: {w.windowTitle()}"))
            print(f"  !!! closed a dialog: {type(w).__name__} '{w.windowTitle()}'", flush=True)
            (w.reject if hasattr(w, "reject") else w.close)()

    guard = QTimer(); guard.setInterval(300); guard.timeout.connect(close_modals); guard.start()
    from ralenti.ui.editor import session as session_mod
    autosave = Path(session_mod.AUTOSAVE)
    disk_backup = autosave.with_name("editor.smoke-backup.json")
    if disk_backup.exists():                  # a previous run was killed: put the edit back first
        autosave.write_bytes(disk_backup.read_bytes())
    backup = autosave.read_bytes() if autosave.exists() else None
    if backup is not None:
        disk_backup.write_bytes(backup)
    try:
        run(app, sys.argv[1] if len(sys.argv) > 1 else None)
    except Exception:
        results.append(("FAIL", "smoke test itself", traceback.format_exc().strip().splitlines()[-1]))
    finally:
        if backup is not None:
            autosave.write_bytes(backup)
        elif autosave.exists():
            autosave.unlink()
        disk_backup.unlink(missing_ok=True)
        faulthandler.cancel_dump_traceback_later()
    report()


if __name__ == "__main__":
    main()