"""Previews on the Interpolation step: render a few seconds at the playhead with the current settings, keep each
render as a take and compare takes with the original (or with each other) in the player. Previews follow the edit:
they start inside a kept part and use the crop, rotation, flip and output size. Takes made with other settings are
marked as changed."""
import json
import os
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from ...core import pipeline
from ...core.engines import engine_for, resolve_model
from ...core.jobs import Status
from ...core.probe import probe
from ...core.segments import kept_ranges
from ..bridge import QueueBridge
from ..util import notify
from ..widgets.takes import TakePanel
from .summary import dur, model_label

AUDIO_NAMES = {"copy": "Original audio", "natural": "Natural pitch", "keep_pitch": "Pitch kept", "drop": "Silent"}


def _clock(t: float) -> str:
    m, s = divmod(int(max(0.0, t)), 60)
    return f"{m}:{s:02d}"


def _covers(job, t: float) -> bool:
    s = job.spec.get("start") or 0.0
    e = job.spec.get("end") or s
    return s - 1e-3 <= t < e - 0.02


class Previews(QObject):
    def __init__(self, page):
        super().__init__(page)
        self.page, self.s, self.qm, self.player = page, page.session, page.qm, page.player
        self.ids: list[str] = []
        self._tinfo, self._discard = {}, set()
        self._want, self._left, self._right = None, None, "off"
        self._refreshing, self.active = False, False
        st = page.settings
        self.panel = TakePanel(float(getattr(st, "preview_length", 3.0) or 3.0))
        self.panel.render_requested.connect(self.render)
        self.panel.length_changed.connect(self._set_length)
        self.panel.take_clicked.connect(self._take_clicked)
        self.panel.take_deleted.connect(lambda jid: self._drop(jid))
        self.panel.take_rerender.connect(self._rerender)
        self.panel.take_versus.connect(self._take_versus)
        p = self.player
        p.describe_take, p.render_info = self._take_sub, self._render_desc
        p.cta_primary.connect(lambda: self.render())
        p.render_here.connect(lambda: self.render())
        p.compare_requested.connect(self._compare_requested)
        p.shown_changed.connect(self.refresh)
        self.bridge = QueueBridge(self.qm)
        self.bridge.setParent(self)
        self.bridge.event.connect(self._event)

    # ---------- on / off (only on Interpolation) ----------
    def set_active(self, on: bool) -> None:
        self.active = bool(on)
        p = self.player
        p.resolve = self._resolve if on else None
        p.on_uncovered = self._uncovered if on else None
        p.cmp.setVisible(on); p.layout_seg.setVisible(on)
        if hasattr(self.page, "cmp_row"):
            self.page.cmp_row.setVisible(on)
        if not on:
            p.hide_cta()
            if p.take:
                p.show_source()
        self.refresh()

    # ---------- what a preview uses ----------
    def _opts(self, clip) -> dict:
        s, e = self.s, self.s.edit
        m, c, a = e.motion, e.crop, e.audio
        on = bool(c.get("on"))
        crop = (c.get("crop") or "none") if on else "none"
        if isinstance(crop, list):
            info, first = s.info(clip), s.info(e.clips[0])
            if (info.width, info.height) != (first.width, first.height):
                crop = "none"
        mode = a.get("mode") or "auto"
        audio = "drop" if mode == "remove" else ("copy" if mode == "copy" else (a.get("slow") or "natural"))
        return dict(model=m.get("model") or "auto", fps_mode=m.get("fps_mode") or "keep",
                    fps_value=float(m.get("fps_value") or 0), slowmo=float(m.get("slowmo") or 1),
                    scene=m.get("scene") or "normal", engine_opts=dict(m.get("engine_opts") or {}), audio=audio,
                    crop=list(crop) if isinstance(crop, list) else crop,
                    scale=float(c.get("scale") or 1.0) if on else 1.0, max_h=int(c.get("max_h") or 0) if on else 0,
                    transform={"rotate": int(c.get("rotate") or 0) % 360 if on else 0,
                               "hflip": bool(c.get("hflip")) and on, "vflip": bool(c.get("vflip")) and on},
                    device=str(self.page.settings.gpu_device or "0"))

    def _sig(self, clip) -> str:
        o = self._opts(clip)
        o["model"] = resolve_model(self.qm.tc, o["model"])
        for k in ("device", "scene"):
            o.pop(k, None)
        return json.dumps(o, sort_keys=True, default=str)

    def build_job(self, clip, t0: float, length: float):
        job = pipeline.make_preview_job(self.qm.tc, self.page.settings, clip, t0, length, **self._opts(clip))
        job.spec["sig"] = self._sig(clip)
        return job

    def _range_at(self, clip, t: float):
        """((start, end) of the kept part to preview, start time) in the clip's own time."""
        s = self.s
        info, off = s.info(clip), s.clip_offset(clip)
        local = []
        for a, b in kept_ranges(s.edit.trim, s.media().duration):
            la, lb = max(0.0, a - off), min(info.duration, b - off)
            if lb - la > 0.05:
                local.append((la, lb))
        if not local:
            return None
        for a, b in local:
            if a - 1e-3 <= t < b:
                return (a, b), max(a, t)
        nxt = next(((a, b) for a, b in local if a > t), local[-1])
        return nxt, nxt[0]

    def _engine_ok(self) -> bool:
        tc = self.qm.tc
        eng = engine_for(resolve_model(tc, self.s.edit.motion.get("model")))
        if eng is None:
            notify(self.page, "Install an AI engine first: RIFE is free, in Downloads.", "warn")
            return False
        res = eng.confirm(self.page, self.page.settings, tc)
        if isinstance(res, str) and res:
            self.s.set("motion", model=res)
            return True
        return bool(res)

    # ---------- render ----------
    def render(self, length=None) -> None:
        page, e = self.page, self.s.edit
        clip = page.cur_clip
        if not clip or not e.clips:
            return
        m = e.motion
        if not m.get("on"):
            notify(page, "Turn Interpolation on (top right) to preview it.", "info")
            return
        if float(m.get("slowmo") or 1) == 1 and (m.get("fps_mode") or "keep") == "keep":
            notify(page, "Nothing to preview: turn on slow motion or pick a higher frame rate.", "warn")
            return
        hit = self._range_at(clip, self.player.source_position())
        if hit is None:
            notify(page, "This clip is removed in Trim, so there's nothing to preview here.", "warn")
            return
        (ra, rb), t = hit
        length = self.panel.length() if length is None else float(length)
        if length <= 0:
            t0, length = ra, rb - ra
        else:
            t0 = t if rb - t >= 0.5 else max(ra, rb - length)
            length = min(length, rb - t0)
        if length < 0.2:
            notify(page, "This kept part is too short to preview.", "warn")
            return
        if not self._engine_ok():
            return
        try:
            job = self.build_job(clip, t0, length)
        except Exception as ex:
            QMessageBox.warning(page, "Ralenti", str(ex))
            return
        self.ids.append(job.id)
        self._want = job.id
        self.qm.add(job)
        page.show_previews_tab()
        self.refresh()
        if self.qm.is_running:
            notify(page, "Preview started next to the running job. They share the GPU, so it may take a bit longer.",
                   "info")

    def _rerender(self, jid: str) -> None:
        job = self.qm.get(jid)
        if not job or job.spec.get("src") != self.page.cur_clip:
            return
        start = job.spec.get("start") or 0.0
        length = max(0.2, (job.spec.get("end") or start) - start)
        self._drop(jid)
        if not self._engine_ok():
            return
        try:
            new = self.build_job(self.page.cur_clip, start, length)
        except Exception as ex:
            QMessageBox.warning(self.page, "Ralenti", str(ex))
            return
        self.ids.append(new.id)
        self._want = new.id
        self.qm.add(new)
        self.refresh()

    def _set_length(self, v: float) -> None:
        st = self.page.settings
        st.preview_length = float(v)
        st.save()

    # ---------- takes ----------
    def _jobs(self, clip) -> list:
        return [j for j in (self.qm.get(i) for i in self.ids) if j and j.spec.get("src") == clip]

    @staticmethod
    def _ready(job) -> bool:
        return job.status == Status.DONE and os.path.exists(job.output)

    def _stale(self, job) -> bool:
        src = job.spec.get("src")
        return src in self.s.edit.clips and job.spec.get("sig") != self._sig(src)

    def _texts(self, job) -> tuple[str, str]:
        s, st = job.spec, job.state
        start, end = s.get("start") or 0.0, s.get("end") or 0.0
        src = s.get("src")
        off = self.s.clip_offset(src) if src in self.s.edit.clips else 0.0
        size = st.get("size")
        res = f"{size[0]}×{size[1]}" if size else "full res"
        title = f"{_clock(off + start)} · {s['slowmo']:g}× · {model_label(self.qm.tc, s['model'])}"
        sub = f"{dur(end - start)} · {res} · {AUDIO_NAMES.get(job.plan.get('audio_mode'), '')}"
        return title, sub

    def _take_sub(self, jid) -> str:
        job = self.qm.get(jid)
        return self._texts(job)[1] if job else ""

    def _details(self, job) -> str:
        s, st = job.spec, job.state
        bits = [model_label(self.qm.tc, s["model"])]
        if st.get("fps"):
            bits.append(f"{float(Fraction(st['fps'])):.3f} fps")
        if s["slowmo"] != 1:
            bits.append(f"{s['slowmo']:g}× slow-mo")
        size = st.get("size")
        bits.append(f"{size[0]}×{size[1]}" if size else "full resolution")
        if st.get("crop"):
            bits.append(f"crop {st['crop'][0]}×{st['crop'][1]}")
        tr = s.get("transform") or {}
        if tr.get("rotate"):
            bits.append(f"rotated {tr['rotate']}°")
        if tr.get("hflip"):
            bits.append("flipped ↔")
        if tr.get("vflip"):
            bits.append("flipped ↕")
        bits.append(AUDIO_NAMES.get(job.plan.get("audio_mode"), ""))
        return " · ".join(b for b in bits if b)

    def _take_dict(self, job):
        info = self._tinfo.get(job.id)
        if info is None:
            info = probe(self.qm.tc.ffprobe, job.output)
            if not info.width:
                return None
            self._tinfo[job.id] = info
        s = job.spec
        start = s.get("start") or 0.0
        end = s.get("end") or start
        side = job.state.get("sidecars")
        audio = tuple(side) if side and all(os.path.exists(a) for a in side) else None
        note = None
        if audio and not job.state.get("orig_stretched"):
            audio, note = (None, audio[1]), "Re-render this take to hear the original audio in sync"
        stale = self._stale(job)
        return dict(id=job.id, path=job.output, info=info, start=start, end=end, slowmo=float(s["slowmo"]),
                    before="Original", after=f"AI · {model_label(self.qm.tc, s['model'])}",
                    details=self._details(job) + (" · settings changed since this render" if stale else ""),
                    caption=f"Take {_clock(start)}–{_clock(end)}", audio=audio, audio_note=note,
                    audio_labels=("Original", AUDIO_NAMES.get(job.plan.get("audio_mode"), "Processed")))

    def _render_desc(self) -> str:
        tc, m = self.qm.tc, self.s.edit.motion
        n, slow = self.panel.length(), float(m.get("slowmo") or 1)
        bits = [model_label(tc, resolve_model(tc, m.get("model"))),
                f"{slow:g}× slow-mo" if slow != 1 else "same speed",
                "whole kept part" if n <= 0 else f"{n:g} s"]
        return " · ".join(b for b in bits if b)

    def refresh(self, *_) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        reroute = False
        p = self.player
        try:
            clips = self.s.edit.clips
            for jid in list(self.ids):
                job = self.qm.get(jid)
                if job is None or job.spec.get("src") not in clips:
                    self._drop(jid, refresh=False)
            clip = self.page.cur_clip
            self.panel.set_has_file(clip is not None)
            jobs = self._jobs(clip) if clip else []
            shown = p.take["id"] if p.take else None
            b_id = p.take_b["id"] if p.take_b else None
            right = b_id or shown
            self.panel.set_takes([(j, *self._texts(j), j.status == Status.DONE and self._stale(j), j.id == right,
                                   bool(b_id) and j.id == shown) for j in jobs])
            self.page.set_preview_count(len(jobs))
            ready = [j for j in jobs if self._ready(j)]
            if self.active and clip:
                p.set_takes([(j.spec.get("start") or 0.0, j.spec.get("end") or 0.0, j.id in (shown, b_id))
                             for j in ready])
                p.set_compare_choices([(j.id, self._texts(j)[0]) for j in ready], self._left, self._right)
            else:
                p.set_takes([])
            reroute = bool(shown) and shown not in {j.id for j in ready}
        finally:
            self._refreshing = False
        if reroute:
            p.route_to(p.source_position())

    def _drop(self, jid: str, refresh: bool = True) -> None:
        if self._left == jid:
            self._left = None
        if self._right == jid:
            self._right = "off"
        if jid in self.ids:
            self.ids.remove(jid)
        job = self.qm.get(jid)
        if job:
            if self.player.take and self.player.take["id"] == jid:
                self.player.show_source()
            if job.status == Status.RUNNING:
                self._discard.add(jid)
                self.qm.cancel_job(jid)
            else:
                self._purge(job)
        if refresh:
            self.refresh()

    def _purge(self, job) -> None:
        self.qm.remove(job.id)
        for f in (job.state.get("sidecars") or []) + [job.state.get("thumb")]:
            if f:
                try:
                    Path(f).unlink(missing_ok=True)
                except OSError:
                    pass
        self._tinfo.pop(job.id, None)

    def _event(self, ev, job) -> None:
        if job is None or job.kind != "preview":
            return
        mine = job.id in self.ids or job.id in self._discard
        if ev == "removed":
            if job.id in self.ids:
                self.ids.remove(job.id)
            if self.player.take and self.player.take["id"] == job.id:
                self.player.show_source()
        elif ev == "finished":
            if job.id in self._discard:
                self._discard.discard(job.id)
                self._purge(job)
            elif job.status == Status.DONE and job.id == self._want:
                self._want = None
                if job.spec.get("src") == self.page.cur_clip and self.active:
                    self._show(job)
            elif job.status == Status.FAILED and job.id in self.ids:
                first = (job.error or "").strip().splitlines() or ["see the queue log"]
                notify(self.page, "Preview failed: " + first[0], "error")
        if mine:
            self.refresh()

    # ---------- compare model: follows the playhead ----------
    def _show(self, job, at=None) -> None:
        if self._left == job.id:
            self._left = None
        self._right = job.id
        self.refresh()
        self.player.route_to((job.spec.get("start") or 0.0) if at is None else at, play=True)

    def _resolve(self, t: float):
        clip = self.page.cur_clip
        if not clip or not self.active:
            return None
        ready = {j.id: j for j in self._jobs(clip) if self._ready(j)}
        if self._left:
            a = ready.get(self._left)
            if not a or not _covers(a, t):
                return None
            b = ready.get(self._right) if self._right != "off" else None
            A = self._take_dict(a)
            if b and b.id != a.id and _covers(b, t):
                B = self._take_dict(b)
                return (A, B, "ab") if A and B else None
            return (A, None, "result") if A else None
        if self._right == "off":
            return None
        j = ready.get(self._right)
        if not j or not _covers(j, t):
            return None
        A = self._take_dict(j)
        return (A, None, "orig") if A else None

    def _uncovered(self, t: float) -> None:
        if self._right == "off" or not self.page.cur_clip:
            return
        n = self.panel.length()
        self.player.show_cta(f"No preview at {_clock(t)}",
                             "Render a few seconds here to compare the AI result with the original.",
                             "Render the whole kept part" if n <= 0 else f"Render {n:g} s here")

    def _compare_requested(self, left, right) -> None:
        if left and left == right:
            right = "off"
        self._left, self._right = left, right
        t = self.player.source_position()
        target = left or (right if right != "off" else None)
        job = self.qm.get(target) if target else None
        if job and self._ready(job) and not _covers(job, t):
            t = job.spec.get("start") or 0.0
        self.refresh()
        self.player.route_to(t)

    def _take_clicked(self, jid: str) -> None:
        job = self.qm.get(jid)
        if not job:
            return
        if self._ready(job):
            self._show(job)
        elif job.status in (Status.QUEUED, Status.RUNNING):
            notify(self.page, "This take is still rendering", "info")

    def _take_versus(self, jid: str) -> None:
        p = self.player
        cur = self._right if self._right != "off" else (p.take["id"] if p.take else None)
        if not cur or cur == jid:
            self._take_clicked(jid)
            notify(self.page, "Showing this take. Press ⇄ on another take to compare the two.", "info")
            return
        a, b = self.qm.get(cur), self.qm.get(jid)
        if not (a and b and self._ready(a) and self._ready(b)):
            return
        s = max(a.spec.get("start") or 0.0, b.spec.get("start") or 0.0)
        e = min(a.spec.get("end") or 0.0, b.spec.get("end") or 0.0)
        if e - s < 0.05:
            notify(self.page, "These two takes don't cover the same moment, so they can't be compared.", "warn")
            return
        self._left, self._right = cur, jid
        self.refresh()
        p.route_to(s, play=True)
