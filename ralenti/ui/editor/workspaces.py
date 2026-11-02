"""Workspaces that take the player's place on Audio and Encode.
Audio: every audio track with its waveform (removed parts hatched); tick the ones to keep; listen to the original or
the processed sound at the playhead. Encode: size, bitrate and time estimates, a comparison of the encoders for this
video and a quality test: a few seconds encoded and measured, with a zoomable still of the original next to the
encode."""
import json
import os
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from shiboken6 import isValid

from ...core.audio_out import bytes_per_second
from ...core.config import TEMP_DIR
from ...core.encoders import ENCODERS
from ...core.estimate import estimate, load_bench
from ...core.progress import fmt_eta
from ...core.quality_test import Cancelled, picture_chain, run_test, size_factor
from ...core.segments import kept_ranges, output_to_source, source_to_output
from ...core.space import BPP
from ...core.tools import run
from ...core.tracks import audio_tracks
from ...core.waveform import listen_cmd, peaks
from .. import theme
from ..icons import icon
from ..util import fmt_ts, mono_font, notify
from ..widgets.fields import button, v_scroll
from ..widgets.segmented import Segmented
from ..widgets.volume import VolumeControl
from ..workers import run_async
from .summary import analyse, dur, preview_output
from .toolbars import ToolRow, tool


def _mb(n: float) -> str:
    n = float(n or 0)
    if n >= 1e9:
        return f"{n / 1e9:.2f} GB"
    return f"{n / 1e6:.0f} MB" if n >= 1e7 else f"{n / 1e6:.1f} MB"


def _label(text: str = "", name: str = "Hint") -> QLabel:
    lb = QLabel(text); lb.setObjectName(name); lb.setWordWrap(True)
    return lb


def _polish(w, prop: str, val) -> None:
    if w.property(prop) != val:
        w.setProperty(prop, val)
        w.style().unpolish(w); w.style().polish(w)


def _local_kept(s, clip):
    e = s.edit
    if not e.trim.get("on"):
        return None
    off, d = s.clip_offset(clip), s.info(clip).duration
    out = []
    for a, b in kept_ranges(e.trim, s.media().duration):
        la, lb = max(0.0, a - off), min(d, b - off)
        if lb > la + 1e-3:
            out.append((la, lb))
    return out


# ============================== Audio ==============================
class Wave(QWidget):
    clicked = Signal(float)

    def __init__(self):
        super().__init__()
        self.peaks, self.secs, self.duration = None, 0.0, 0.0
        self.kept, self.pos, self.on = None, None, True
        self.msg = "Reading the sound…"
        self.setMinimumHeight(70)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click or drag to move the playhead")

    def set_data(self, values, secs) -> None:
        self.peaks, self.secs = values, float(secs or 0)
        self.update()

    def set_pos(self, t) -> None:
        if t != self.pos:
            self.pos = t
            self.update()

    def _span(self) -> float:
        return max(self.duration, self.secs, 1e-6)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(Qt.NoPen); p.setBrush(QColor(theme.BG)); p.drawRoundedRect(r, 7, 7)
        if not self.peaks:
            p.setPen(QColor(theme.FAINT)); p.drawText(r, Qt.AlignCenter, self.msg)
            return
        d, n = self._span(), len(self.peaks)
        mid, half = r.center().y(), r.height() / 2 - 5
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setPen(QPen(QColor(theme.ACCENT if self.on else theme.FAINT), 1))
        x0, w = int(r.left()) + 1, int(r.width()) - 2
        for i in range(max(0, w)):
            t = (i + 0.5) / w * d
            if t >= self.secs:
                break
            k = min(n - 1, int(t / self.secs * n))
            a = max(0.5, self.peaks[k] * half)
            p.drawLine(QPointF(x0 + i, mid - a), QPointF(x0 + i, mid + a))
        if self.kept is not None:
            gaps, prev = [], 0.0
            for ka, kb in self.kept:
                if ka > prev + 1e-3:
                    gaps.append((prev, ka))
                prev = max(prev, kb)
            if prev < d - 1e-3:
                gaps.append((prev, d))
            p.setPen(Qt.NoPen)
            for ga, gb in gaps:
                xa, xb = r.left() + r.width() * ga / d, r.left() + r.width() * gb / d
                rect = QRectF(xa, r.top(), xb - xa, r.height())
                p.setBrush(QColor(0, 0, 0, 150)); p.drawRect(rect)
                p.setBrush(QBrush(QColor(theme.FAINT), Qt.BDiagPattern)); p.drawRect(rect)
        if self.pos is not None:
            x = r.left() + r.width() * self.pos / d
            p.setPen(QPen(QColor(theme.WARN), 1.5)); p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.width() > 2:
            self.clicked.emit(max(0.0, min(1.0, e.position().x() / self.width())) * self._span())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton and self.width() > 2:
            self.clicked.emit(max(0.0, min(1.0, e.position().x() / self.width())) * self._span())


class TrackRow(QFrame):
    toggled = Signal(int, bool)
    picked = Signal(object, int, float)

    def __init__(self, clip, n: int, title: str, checked, sub: str = ""):
        super().__init__()
        self.clip, self.n, self.key = clip, n, None
        self.setObjectName("SegRow")
        v = QVBoxLayout(self); v.setContentsMargins(10, 8, 10, 10); v.setSpacing(6)
        h = QHBoxLayout(); h.setSpacing(8)
        if checked is None:
            t = QLabel(title); t.setStyleSheet("font-weight: 600;")
            h.addWidget(t, 1)
            self.cb = None
        else:
            self.cb = QCheckBox(title); self.cb.setChecked(bool(checked))
            self.cb.setStyleSheet(f"font-weight: 600; color: {theme.TEXT};")
            self.cb.setToolTip("Keep this track in the output")
            self.cb.toggled.connect(lambda on: self.toggled.emit(self.n, on))
            h.addWidget(self.cb, 1)
        self.sub = QLabel(sub); self.sub.setObjectName("Hint")
        h.addWidget(self.sub)
        self.wave = Wave()
        self.wave.clicked.connect(lambda t: self.picked.emit(self.clip, self.n, t))
        v.addLayout(h); v.addWidget(self.wave)


PLAYING = QMediaPlayer.PlayingState
LOADED = (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia, QMediaPlayer.BufferingMedia, QMediaPlayer.EndOfMedia)


def _ts(x: float) -> str:
    m, s = divmod(max(0.0, float(x)), 60)
    return f"{int(m)}:{s:04.1f}"


class AudioView(QWidget):
    """Every audio track with its waveform, and a player for the sound of the edit: only the kept parts, back to
    back, original or processed (slowed like the output). Switch while it plays; the playhead moves on the waves."""

    def __init__(self, page):
        super().__init__()
        self.page, self.s = page, page.session
        self.rows, self._tracks, self._waves = [], {}, {}
        self.sel, self._gen, self._ready, self._p = None, 0, None, None
        self._kind, self._want, self._pending, self._resume = "orig", None, None, None
        self._last, self._played = None, False
        self.setFocusPolicy(Qt.StrongFocus)
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0)
        card = QFrame(); card.setObjectName("Card")
        cv = QVBoxLayout(card); cv.setContentsMargins(16, 14, 16, 14); cv.setSpacing(10)
        head = QHBoxLayout()
        lab = QLabel("AUDIO TRACKS"); lab.setObjectName("FieldLabel")
        self.info = _label()
        head.addWidget(lab); head.addSpacing(10); head.addWidget(self.info, 1)
        cv.addLayout(head)
        self.box = QWidget()
        self.list = QVBoxLayout(self.box); self.list.setContentsMargins(0, 0, 4, 0); self.list.setSpacing(8)
        self.list.addStretch(1)
        self.scroll = v_scroll(self.box); self.scroll.setFrameShape(QFrame.NoFrame)
        self.empty = _label(); self.empty.setAlignment(Qt.AlignCenter)
        cv.addWidget(self.scroll, 1); cv.addWidget(self.empty, 1)
        self.bar = ToolRow()
        self.b_play = tool("", "play-fill", "Play / pause the sound of the edit (Space)", lambda: self.toggle())
        self.b_play.setFixedSize(34, 28)
        self.clock = QLabel("0:00.0"); self.clock.setObjectName("Muted"); self.clock.setFont(mono_font(9))
        self.which = Segmented([("Original", "orig", "The sound as it is in the source (A)"),
                                ("Processed", "proc", "The sound as it comes out: slowed down with the pitch handled "
                                                      "as chosen (A)")])
        self.which.set_value("orig"); self.which.setFixedWidth(220)
        self.which.changed.connect(self._switch)
        self.b_loop = tool("", "arrow-clockwise", "Loop", checkable=True)
        self.bar.add(self.b_play); self.bar.h.addSpacing(6); self.bar.add(self.clock); self.bar.sep()
        self.bar.add(self.which); self.bar.h.addSpacing(4); self.bar.add(self.b_loop); self.bar.finish()
        self.plan = _label(); self.plan.setTextFormat(Qt.RichText)
        cv.addWidget(self.bar); cv.addWidget(self.plan)
        root.addWidget(card, 1)
        self.players = {}
        for k in ("orig", "proc"):
            mp, out = QMediaPlayer(self), QAudioOutput(self)
            mp.setAudioOutput(out)
            mp.mediaStatusChanged.connect(lambda st, k=k: self._status(k, st))
            self.players[k] = mp
        self.tick = QTimer(self); self.tick.setInterval(33); self.tick.timeout.connect(self._tick)
        for key, fn in (("Space", self.toggle),
                        ("A", lambda: self._switch("proc" if self._kind == "orig" else "orig")),
                        ("Left", lambda: self._nudge(-5)), ("Right", lambda: self._nudge(5))):
            sc = QShortcut(QKeySequence(key), self); sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        page.player.player.positionChanged.connect(self._pos)

    # ---------- tracks ----------
    def _tracks_of(self, clip):
        if clip not in self._tracks:
            self._tracks[clip] = audio_tracks(self.page.qm.tc.ffprobe, clip)
        return self._tracks[clip]

    def _empty(self, text: str) -> None:
        self.empty.setText(text); self.empty.show()
        for w in (self.scroll, self.bar, self.plan):
            w.hide()
        self.info.setText("")
        self.stop()

    def showEvent(self, e):
        super().showEvent(e)
        self.setFocus()

    def refresh(self) -> None:
        if not isValid(self.s):
            return
        s, e = self.s, self.s.edit
        for r in self.rows:
            self.list.removeWidget(r); r.deleteLater()
        self.rows = []
        if not e.clips:
            return self._empty("Open a video first.")
        multi = len(e.clips) > 1
        keep = sorted({int(x) for x in e.audio.get("tracks") or [0]})
        mode = e.audio.get("mode") or "auto"
        entries = []
        if multi:
            for i, c in enumerate(e.clips):
                info, tr = s.info(c), self._tracks_of(c)
                sub = tr[0]["label"].split(" · ", 1)[-1] if tr else ("" if info.has_audio else "no sound")
                entries.append((c, 0, f"Clip {i + 1} · {Path(c).name}", None, sub, info.has_audio))
        else:
            c = e.clips[0]
            tr = self._tracks_of(c)
            if not tr and s.info(c).has_audio:
                tr = [dict(n=0, label="Track 1")]
            for t in tr:
                title, _x, sub = t["label"].partition(" · ")
                entries.append((c, t["n"], title, t["n"] in keep, sub, True))
        if not any(x[5] for x in entries):
            return self._empty("This video has no sound.")
        self.empty.hide()
        for w in (self.scroll, self.bar, self.plan):
            w.show()
        if self.sel is not None and self.sel[0] not in e.clips:
            self.sel = None
        for c, n, title, checked, sub, has in entries:
            row = TrackRow(c, n, title, checked, sub)
            row.toggled.connect(self._toggle); row.picked.connect(self._pick)
            row.wave.duration = s.info(c).duration
            row.wave.kept = _local_kept(s, c)
            row.wave.on = mode != "remove" and (checked is None or checked) and has
            if not has:
                row.wave.msg = "No sound in this clip"
            self.list.insertWidget(self.list.count() - 1, row)
            self.rows.append(row)
            if has:
                self._wave(row)
        if self.sel is None:
            first = next((r for r in self.rows if r.wave.on), self.rows[0])
            self.sel = (first.clip, first.n)
        for r in self.rows:
            _polish(r, "sel", (r.clip, r.n) == self.sel)
        n = len(entries)
        if multi:
            self.info.setText(f"{n} clips · the first track of each is joined · click a wave to pick what you hear")
        else:
            k = sum(1 for x in entries if x[3])
            self.info.setText(f"{n} tracks · keeping {k} · click a wave to pick what you hear" if n > 1
                              else "One track")
        p = self._plan()
        key = self._key(p) if p else None
        if self._ready is not None and key != self._ready:
            self._halt(); self._ready = None; self._resume = None
        self._p = p
        drop = not p or p["drop"]
        self.which.group.button(1).setEnabled(not drop)
        if drop and self._kind == "proc":
            self._kind = "orig"; self.which.set_value("orig")
        an = analyse(s)
        if an:
            ap = an["aplan"]
            text = f"Result: <b>{ap['label']}</b>"
            if mode == "remove":
                text += " · the output is silent. Change Sound in the panel to keep it."
            self.plan.setText(text)
        if self.tick.isActive():
            self._tick()
        else:
            self._pos(self.page.player.player.position())
            if p:
                u = self._resume if self._resume is not None else self._from_video(p)
                self._clock(u, output_to_source(u, p["ranges"]))

    def _wave(self, row) -> None:
        try:
            key = (row.clip, row.n, os.path.getmtime(row.clip))
        except OSError:
            return
        row.key = key
        v = self._waves.get(key)
        if isinstance(v, tuple):
            row.wave.set_data(*v)
            return
        if v == "fail":
            row.wave.msg = "Couldn't read this track"; row.wave.update()
            return
        if v == "busy":
            return
        self._waves[key] = "busy"
        ff, path, n, d = self.page.qm.tc.ffmpeg, row.clip, row.n, self.s.info(row.clip).duration

        def done(res):
            self._waves[key] = res if res and res[0] else "fail"
            if isValid(self):
                self._fill_waves()

        run_async(lambda: peaks(ff, path, n, 1200, d), done, lambda _m: done(None))

    def _fill_waves(self) -> None:
        for r in self.rows:
            v = self._waves.get(r.key)
            if isinstance(v, tuple):
                r.wave.set_data(*v)
            elif v == "fail":
                r.wave.msg = "Couldn't read this track"; r.wave.update()

    def _toggle(self, n: int, on: bool) -> None:
        cur = sorted({int(x) for x in self.s.edit.audio.get("tracks") or [0]})
        new = sorted(set(cur) | {n}) if on else [x for x in cur if x != n]
        if not new:
            notify(self.page, "Keep at least one track. To drop the sound, set Sound to Remove in the panel.", "info")
            QTimer.singleShot(0, self.refresh)
            return
        self.s.set("audio", tracks=new)

    # ---------- what is played ----------
    def _plan(self):
        s, e = self.s, self.s.edit
        if not self.sel or not e.clips or self.sel[0] not in e.clips:
            return None
        clip, n = self.sel
        info = s.info(clip)
        if not info or not info.has_audio:
            return None
        kept = _local_kept(s, clip)
        ranges = [(round(a, 3), round(b, 3)) for a, b in (kept if kept is not None else [(0.0, info.duration)])
                  if b - a > 0.02]
        if not ranges:
            return None
        an = analyse(s)
        ap = an["aplan"] if an else {"mode": "drop"}
        flt = ap.get("filter") if ap.get("mode") == "encode" else None
        return dict(clip=clip, n=int(n), ranges=ranges, flt=flt, slow=float(an["slow"]) if an and flt else 1.0,
                    drop=ap.get("mode") == "drop")

    @staticmethod
    def _key(p) -> str:
        return json.dumps(p, sort_keys=True, default=str)

    @staticmethod
    def _total(p) -> float:
        return sum(b - a for a, b in p["ranges"])

    def _f(self, kind=None) -> float:
        p = self._p
        return p["slow"] if p and (kind or self._kind) == "proc" else 1.0

    def _playing(self) -> bool:
        return any(mp.playbackState() == PLAYING for mp in self.players.values())

    def _cur_u(self) -> float:
        return self.players[self._kind].position() / 1000 / self._f()

    def _from_video(self, p) -> float:
        page = self.page
        t = page.player.source_position() if p["clip"] == page.cur_clip else 0.0
        u = source_to_output(t, p["ranges"])
        return 0.0 if u >= self._total(p) - 0.05 else u

    def _icon(self, playing: bool) -> None:
        self.b_play.setIcon(icon("pause-fill" if playing else "play-fill", "text", checked=None))

    # ---------- transport ----------
    def toggle(self) -> None:
        if not isValid(self.s) or not self.isVisible():
            return
        if self._playing():
            self._pause()
            return
        p = self._plan()
        if p is None:
            notify(self.page, "Nothing to play: this clip has no kept sound.", "info")
            return
        key = self._key(p)
        u = self._resume if (self._resume is not None and self._ready == key) else self._from_video(p)
        if self._ready != key:
            self._want = u
            self._render(p, key)
            return
        self._play_at(u)

    def _play_at(self, u: float) -> None:
        if not self._p:
            return
        u = max(0.0, min(u, self._total(self._p)))
        for k, mp in self.players.items():
            if k != self._kind:
                mp.pause()
        mp = self.players[self._kind]
        self._played, self._resume = True, None
        if mp.mediaStatus() not in LOADED:
            self._pending = u
            return
        mp.setPosition(int(u * self._f() * 1000))
        mp.audioOutput().setVolume(VolumeControl.level); mp.audioOutput().setMuted(False)
        mp.play()
        self.tick.start()
        self._icon(True)

    def _pause(self) -> None:
        u = self._cur_u()
        self._halt()
        self._resume = u
        self._show(u)
        self._sync_video(u)

    def _halt(self) -> None:
        for mp in self.players.values():
            mp.pause()
        self.tick.stop()
        self._want = self._pending = None
        self._icon(False)

    def _sync_video(self, u: float) -> None:
        p = self._p
        if not p or p["clip"] not in self.s.edit.clips:
            return
        t = output_to_source(u, p["ranges"])
        self._last = t
        self.page._goto(self.s.clip_offset(p["clip"]) + t, play=False)

    def _switch(self, kind) -> None:
        if kind == self._kind:
            self.which.set_value(kind)
            return
        p = self._p
        if kind == "proc" and (not p or p["drop"]):
            self.which.set_value(self._kind)
            return
        playing = self._playing()
        u = self._cur_u() if self._ready else None
        self._kind = kind
        self.which.set_value(kind)
        for k, mp in self.players.items():
            if k != kind:
                mp.pause()
        if self._ready and u is not None:
            if playing:
                self._play_at(u)
            else:
                self._resume = u
                self._show(u)

    def _nudge(self, d: float) -> None:
        if not self.isVisible() or not self._ready or not self._p:
            return
        u = max(0.0, min(self._cur_u() + d / self._f(), self._total(self._p)))
        if self._playing():
            self._play_at(u)
        else:
            self._resume = u
            self._show(u)
            self._sync_video(u)

    def _status(self, k, st) -> None:
        if k != self._kind:
            return
        if st in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self._pending is not None:
            u, self._pending = self._pending, None
            self._play_at(u)
        elif st == QMediaPlayer.EndOfMedia and self._played and self._p:
            if self.b_loop.isChecked():
                self._play_at(0.0)
            else:
                self.tick.stop(); self._icon(False)
                self._resume = None
                end = max(0.0, self._total(self._p) - 0.01)
                self._show(end)
                self._sync_video(end)

    def _clock(self, u: float, t: float) -> None:
        p = self._p
        if not p:
            self.clock.setText("0:00.0")
            return
        f = self._f()
        self.clock.setText(f"{_ts(u * f)} / {_ts(self._total(p) * f)} · source {_ts(t)}")

    def _show(self, u: float) -> None:
        p = self._p
        if not p:
            return
        t = output_to_source(u, p["ranges"])
        for r in self.rows:
            r.wave.set_pos(t if r.clip == p["clip"] else None)
        self._clock(u, t)

    def _tick(self) -> None:
        if not self._p:
            return
        u = self._cur_u()
        self._last = output_to_source(u, self._p["ranges"])
        self._show(u)

    # ---------- render the sound to listen to ----------
    def _render(self, p, key) -> None:
        page = self.page
        ff = page.qm.tc.ffmpeg
        if not ff:
            notify(page, "FFmpeg is missing: get it from Downloads.", "warn")
            return
        for mp in self.players.values():
            mp.stop(); mp.setSource(QUrl())
        self._gen += 1
        gen = self._gen
        self._ready, self._p = None, p
        d = Path(page.settings.temp_dir or TEMP_DIR) / "listen"
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True, exist_ok=True)
        files = {"orig": d / f"orig_{gen}.wav"}
        cmds = [listen_cmd(ff, p["clip"], p["n"], p["ranges"], None, files["orig"])]
        if not p["drop"]:
            files["proc"] = d / f"proc_{gen}.wav"
            cmds.append(listen_cmd(ff, p["clip"], p["n"], p["ranges"], p["flt"], files["proc"]))
        self.bar.note.setText("Preparing the sound…")
        self.b_play.setEnabled(False)

        def work():
            for c in cmds:
                r = run(c, timeout=900)
                if r.returncode != 0:
                    tail = [x for x in (r.stderr or "").strip().splitlines() if x.strip()][-1:] or ["FFmpeg failed"]
                    raise RuntimeError(tail[0][:200])
            return True

        def done(_r):
            if not isValid(self) or gen != self._gen:
                return
            self.b_play.setEnabled(True)
            self.bar.note.setText("")
            self._ready = key
            for k, f in files.items():
                if f.exists():
                    self.players[k].setSource(QUrl.fromLocalFile(str(f)))
            if self._want is not None:
                u, self._want = self._want, None
                self._play_at(u)

        def fail(msg):
            if not isValid(self) or gen != self._gen:
                return
            self.b_play.setEnabled(True)
            self._want = None
            self.bar.note.setText(f"Couldn't prepare the sound: {msg}")

        run_async(work, done, fail)

    # ---------- waves, playhead ----------
    def _pick(self, clip, n: int, t: float) -> None:
        self.setFocus()
        same = self.sel == (clip, n)
        self.sel = (clip, n)
        for r in self.rows:
            _polish(r, "sel", (r.clip, r.n) == self.sel)
        if not same:
            self._halt(); self._ready = None; self._resume = None
            self._p = self._plan()
        p = self._p
        if same and p and self._ready == self._key(p):
            u = source_to_output(t, p["ranges"])
            if self._playing():
                self._play_at(u)
            else:
                self._resume = u
                self._show(u)
        self.page._goto(self.s.clip_offset(clip) + t, play=False)

    def _pos(self, ms) -> None:
        if not self.isVisible() or self.tick.isActive():
            return
        t = ms / 1000
        for r in self.rows:
            r.wave.set_pos(t if r.clip == self.page.cur_clip else None)

    def stop(self, keep_note: bool = False) -> None:
        self._gen += 1
        was = self._played
        self._halt()
        self.b_play.setEnabled(True)
        if not keep_note:
            self.bar.note.setText("")
        if was and self._last is not None and self._p and self._p["clip"] in self.s.edit.clips:
            self.page._goto(self.s.clip_offset(self._p["clip"]) + self._last, play=False)
        self._played, self._resume, self._ready = False, None, None


# ============================== Encode ==============================
class ZoomCompare(QWidget):
    """Two stills of the same frame: split (drag the divider) or side by side. Scroll to zoom around the cursor,
    drag to move, double-click to fit."""

    def __init__(self):
        super().__init__()
        self.a = self.b = None
        self.la = self.lb = ""
        self.mode, self.split, self.zoom, self.center = "split", 0.5, 1.0, None
        self._drag = None
        self.msg = "Run a quality test to compare a still of the original with the encode, down to the pixel."
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setToolTip("Scroll to zoom · drag to move · double-click to fit")

    def set_images(self, a, b, la: str, lb: str) -> None:
        self.a = a if a is not None and not a.isNull() else None
        self.b = b if b is not None and not b.isNull() else None
        self.la, self.lb = la, lb
        self.zoom, self.center = 1.0, None
        self.update()

    def clear(self, msg: str) -> None:
        self.a = self.b = None
        self.msg = msg
        self.update()

    def set_mode(self, m) -> None:
        self.mode = m
        self.update()

    def _areas(self):
        r = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        if self.mode == "side":
            hw = (r.width() - 8) / 2
            return [QRectF(r.left(), r.top(), hw, r.height()), QRectF(r.left() + hw + 8, r.top(), hw, r.height())]
        return [r]

    def _scale(self, area) -> float:
        return min(area.width() / self.a.width(), area.height() / self.a.height()) * self.zoom

    def _c(self) -> QPointF:
        return self.center or QPointF(self.a.width() / 2, self.a.height() / 2)

    def _rect(self, area) -> QRectF:
        s, c = self._scale(area), self._c()
        return QRectF(area.center().x() - c.x() * s, area.center().y() - c.y() * s,
                      self.a.width() * s, self.a.height() * s)

    def _area_at(self, pos):
        areas = self._areas()
        return next((a for a in areas if a.contains(pos)), areas[0])

    def _split_x(self) -> float:
        a = self._areas()[0]
        return a.left() + a.width() * self.split

    def _clamp(self, c) -> QPointF:
        return QPointF(min(max(c.x(), 0.0), self.a.width()), min(max(c.y(), 0.0), self.a.height()))

    def _pill(self, p, region, text: str, right: bool) -> None:
        if not text or region.width() < 30:
            return
        f = QFont(self.font()); f.setPointSizeF(9); f.setWeight(QFont.DemiBold)
        fm = QFontMetrics(f)
        w, h = fm.horizontalAdvance(text) + 24, fm.height() + 10
        x = region.right() - w - 10 if right else region.left() + 10
        rect = QRectF(x, region.top() + 10, w, h)
        p.save(); p.setClipRect(region)
        p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 185)); p.drawRoundedRect(rect, h / 2, h / 2)
        p.setFont(f); p.setPen(QColor(theme.ACCENT if right else theme.TEXT)); p.drawText(rect, Qt.AlignCenter, text)
        p.restore()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        if self.a is None:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect().adjusted(20, 0, -20, 0), Qt.AlignCenter | Qt.TextWordWrap, self.msg)
            return
        areas = self._areas()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform, self._scale(areas[0]) < 2.0)
        b = self.b or self.a
        if self.mode == "side":
            for area, img, lab, right in ((areas[0], self.a, self.la, False), (areas[1], b, self.lb, True)):
                p.save(); p.setClipRect(area); p.drawImage(self._rect(area), img); p.restore()
                self._pill(p, area, lab, right)
        else:
            area = areas[0]
            ir, x = self._rect(area), self._split_x()
            p.save(); p.setClipRect(area); p.drawImage(ir, self.a)
            p.setClipRect(QRectF(x, area.top(), area.right() - x, area.height())); p.drawImage(ir, b)
            p.restore()
            self._pill(p, QRectF(area.left(), area.top(), x - area.left(), area.height()), self.la, False)
            self._pill(p, QRectF(x, area.top(), area.right() - x, area.height()), self.lb, True)
            p.setPen(QPen(QColor(255, 255, 255, 230), 2)); p.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            p.setPen(Qt.NoPen); p.setBrush(QColor(theme.ACCENT)); p.drawEllipse(QPointF(x, area.center().y()), 11, 11)
        txt = f"{self._scale(areas[0]) * 100:.0f}%"
        f = QFont(self.font()); f.setPointSizeF(8.5); f.setWeight(QFont.DemiBold); p.setFont(f)
        fm = QFontMetrics(f)
        bw = fm.horizontalAdvance(txt) + 16
        br = QRectF(self.width() - bw - 12, self.height() - fm.height() - 18, bw, fm.height() + 8)
        p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 170)); p.drawRoundedRect(br, br.height() / 2, br.height() / 2)
        p.setPen(QColor(theme.TEXT)); p.drawText(br, Qt.AlignCenter, txt)

    def wheelEvent(self, e):
        if self.a is None:
            return
        pos = e.position()
        area = self._area_at(pos)
        s0, ir = self._scale(area), self._rect(area)
        pt = QPointF((pos.x() - ir.left()) / s0, (pos.y() - ir.top()) / s0)
        self.zoom = max(1.0, min(64.0, self.zoom * 1.25 ** (e.angleDelta().y() / 120)))
        if self.zoom <= 1.0001:
            self.center = None
        else:
            s1 = self._scale(area)
            self.center = self._clamp(QPointF(pt.x() - (pos.x() - area.center().x()) / s1,
                                              pt.y() - (pos.y() - area.center().y()) / s1))
        self.update()
        e.accept()

    def _move_split(self, x: float) -> None:
        a = self._areas()[0]
        self.split = max(0.0, min(1.0, (x - a.left()) / max(1.0, a.width())))
        self.update()

    def mousePressEvent(self, e):
        if self.a is None:
            return
        pos = e.position()
        if e.button() == Qt.LeftButton and self.mode == "split" and abs(pos.x() - self._split_x()) <= 12:
            self._drag = ("split",)
        elif self.zoom > 1.0001:
            self._drag = ("pan", pos, self._c(), self._scale(self._area_at(pos)))
            self.setCursor(Qt.ClosedHandCursor)
        elif e.button() == Qt.LeftButton and self.mode == "split":
            self._drag = ("split",)
            self._move_split(pos.x())

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._drag and self._drag[0] == "split":
            self._move_split(pos.x())
            return
        if self._drag and self._drag[0] == "pan":
            _k, start, c0, s = self._drag
            self.center = self._clamp(QPointF(c0.x() - (pos.x() - start.x()) / s, c0.y() - (pos.y() - start.y()) / s))
            self.update()
            return
        if self.a is not None:
            near = self.mode == "split" and abs(pos.x() - self._split_x()) <= 12
            self.setCursor(Qt.SplitHCursor if near else (Qt.OpenHandCursor if self.zoom > 1.0001 else Qt.ArrowCursor))

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor if self.zoom > 1.0001 else Qt.ArrowCursor)

    def mouseDoubleClickEvent(self, e):
        self.zoom, self.center = 1.0, None
        self.update()


class _Stat(QWidget):
    def __init__(self, caption: str):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
        self.value = QLabel("–"); self.value.setObjectName("StatValue")
        cap = QLabel(caption.upper()); cap.setObjectName("FieldLabel")
        v.addWidget(self.value); v.addWidget(cap)


class _EncRow(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, title: str, tag: str):
        super().__init__()
        self.key = key
        self.setObjectName("EncRow"); self.setAttribute(Qt.WA_Hover); self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Use this one")
        h = QHBoxLayout(self); h.setContentsMargins(10, 6, 10, 6); h.setSpacing(10)
        t = QLabel(title); t.setStyleSheet("font-weight: 600;")
        g = QLabel(tag); g.setObjectName("Hint")
        self.size, self.time = QLabel(), QLabel()
        for w, width in ((self.size, 86), (self.time, 70)):
            w.setObjectName("Muted"); w.setFixedWidth(width); w.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(t); h.addWidget(g); h.addStretch(1); h.addWidget(self.size); h.addWidget(self.time)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)


class _TestSig(QObject):
    progress = Signal(str)
    finished = Signal(object, str)


class EncodeView(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page, self.s = page, page.session
        self.enc_rows, self.res, self.res_text = {}, None, ""
        self._cancel = self._sig_obj = self._ctx = None
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(12)
        top = QHBoxLayout(); top.setSpacing(12)
        est = QFrame(); est.setObjectName("Card")
        ev = QVBoxLayout(est); ev.setContentsMargins(16, 14, 16, 14); ev.setSpacing(10)
        lab = QLabel("ESTIMATE"); lab.setObjectName("FieldLabel")
        ev.addWidget(lab)
        stats = QHBoxLayout(); stats.setSpacing(22)
        self.st_size, self.st_rate, self.st_time, self.st_len = (_Stat("File size"), _Stat("Bitrate"), _Stat("Time"),
                                                                 _Stat("Length"))
        for st in (self.st_size, self.st_rate, self.st_time, self.st_len):
            stats.addWidget(st)
        stats.addStretch(1)
        ev.addLayout(stats)
        self.est_note = _label(); self.est_note.setTextFormat(Qt.RichText)
        ev.addWidget(self.est_note); ev.addStretch(1)
        cmp = QFrame(); cmp.setObjectName("Card")
        cv = QVBoxLayout(cmp); cv.setContentsMargins(10, 12, 10, 10); cv.setSpacing(2)
        hl = QLabel("COMPARE ENCODERS"); hl.setObjectName("FieldLabel"); hl.setContentsMargins(6, 0, 0, 4)
        cv.addWidget(hl)
        self.enc_list = QVBoxLayout(); self.enc_list.setSpacing(2)
        cv.addLayout(self.enc_list)
        self.cmp_note = _label(); self.cmp_note.setContentsMargins(6, 4, 6, 0)
        cv.addWidget(self.cmp_note); cv.addStretch(1)
        top.addWidget(est, 1); top.addWidget(cmp, 1)
        root.addLayout(top)
        test = QFrame(); test.setObjectName("Card")
        tv = QVBoxLayout(test); tv.setContentsMargins(16, 14, 16, 14); tv.setSpacing(10)
        row = QHBoxLayout(); row.setSpacing(8)
        tl = QLabel("QUALITY TEST"); tl.setObjectName("FieldLabel")
        self.test_btn = button("Test 3 s at the playhead", self._start_test, "accent", icon="gauge")
        self.cancel_btn = button("Cancel", self._cancel_test, "ghost", icon="x"); self.cancel_btn.hide()
        self.mode = Segmented([("Split", "split", "Drag the divider"), ("Side by side", "side", "Both at once")])
        self.mode.set_value("split"); self.mode.setFixedWidth(220)
        row.addWidget(tl); row.addSpacing(6); row.addWidget(self.test_btn); row.addWidget(self.cancel_btn)
        row.addStretch(1); row.addWidget(self.mode)
        self.test_status = _label(); self.test_status.setTextFormat(Qt.RichText)
        self.cmp = ZoomCompare()
        self.mode.changed.connect(self.cmp.set_mode)
        tv.addLayout(row); tv.addWidget(self.test_status); tv.addWidget(self.cmp, 1)
        root.addWidget(test, 1)

    # ---------- numbers ----------
    def _vm(self, enc):
        e, tc = self.s.edit, self.page.qm.tc
        return e.encode.get("vmaf") if enc and enc.has_quality and tc.has_vmaf else None

    def _chain(self, an, clip) -> list:
        s, e = self.s, self.s.edit
        c = e.crop
        on = bool(c.get("on"))
        info, first = s.info(clip), s.info(e.clips[0])
        crop = an["crop"] if isinstance(an["crop"], list) and (info.width, info.height) == (first.width, first.height) \
            else None
        return picture_chain(crop, an["rot"], an["hf"], an["vf"], float(c.get("scale") or 1.0) if on else 1.0,
                             int(c.get("max_h") or 0) if on else 0, info.width, info.height)

    def _sig(self, an) -> str:
        clip, enc = self.page.cur_clip, an["enc"]
        return json.dumps([clip, enc.key if enc else None, self._vm(enc), self.s.edit.encode.get("q"),
                           self._chain(an, clip) if clip else []], default=str)

    def _size(self, an, key=None):
        """(bytes, measured)"""
        s, e, tc = self.s, self.s.edit, self.page.qm.tc
        info, od = an["info"], an["out_dur"]
        bps = bytes_per_second(an["aplan"], info)
        audio = (24000.0 if bps is None else bps) * od
        cur = "copy" if an["copy"] else (an["enc"].key if an["enc"] else None)
        key = key or cur
        if key == "copy":
            src = sum(os.path.getsize(c) for c in e.clips if os.path.exists(c))
            return src * an["dur"] / max(info.duration, 1e-6), False
        enc = ENCODERS.get(key or "")
        if not enc:
            return 0.0, False
        fps = float(an["fps"] or info.fps or 30)
        r = self.res
        if r and key == cur and self.page.cur_clip and r["sig"] == self._sig(an):
            return r["bps"] * od * fps / float(info.fps or 30) + audio, True
        w, h = an["size"]
        vm = self._vm(enc)
        q = None if vm else e.encode.get("q")
        return w * h * fps * od * BPP.get(key, 0.03) * size_factor(enc, vm, q) + audio, False

    def _time(self, an, key):
        enc = ENCODERS.get(key or "")
        if not enc:
            return None
        s, e = self.s, self.s.edit
        info, (w, h) = an["info"], an["size"]
        vm = bool(self._vm(enc))
        if an["motion"]:
            m = e.motion
            est = estimate(info, dict(model=an["model"], fps_mode=m.get("fps_mode") or "keep",
                                      fps_value=m.get("fps_value") or 0, slowmo=an["slow"], encoder=key, vmaf=vm),
                           (w, h), None, an["dur"], s.qm.rates)
            return est[0] if est else None
        b = load_bench()
        em = (b.get("encoders") or {}).get(key)
        if not em:
            return None
        fps, od = float(an["fps"] or info.fps or 30), an["out_dur"]
        t = w * h * fps * od / 1e6 / em
        if vm and b.get("vmaf"):
            sample = od if od <= 30 else 20.0
            smpf = w * h * sample * fps / 1e6
            lo, hi = enc.q_range
            t += (hi - lo + 1).bit_length() * (smpf / em + smpf / b["vmaf"])
        return t + 2

    # ---------- refresh ----------
    def _fill_encoders(self, an) -> None:
        for r in self.enc_rows.values():
            self.enc_list.removeWidget(r); r.deleteLater()
        self.enc_rows = {}
        if not an:
            self.cmp_note.setText("")
            return
        e, tc = self.s.edit, self.page.qm.tc
        items = []
        if not an["motion"] and not (an["filters"] and e.crop.get("on")):
            items.append(("copy", "Copy", "No re-encode · lossless"))
        items += [(k, x.label, x.tag) for k, x in ENCODERS.items() if not x.hidden and tc.binary_for(x.codec)]
        cur = "copy" if an["copy"] else (an["enc"].key if an["enc"] else None)
        timed = False
        for key, title, tag in items:
            row = _EncRow(key, title, tag)
            row.clicked.connect(self._choose)
            row.size.setText("≈ " + _mb(self._size(an, key)[0]))
            t = None if key == "copy" else self._time(an, key)
            timed |= bool(t)
            row.time.setText("seconds" if key == "copy" else (fmt_eta(t) if t else "–"))
            _polish(row, "cur", key == cur)
            self.enc_list.addWidget(row)
            self.enc_rows[key] = row
        note = "Click one to use it. Sizes are rough and use the quality set in the panel."
        if not timed:
            note += " Run the benchmark (Settings › Performance) to see times."
        self.cmp_note.setText(note)

    def _choose(self, key: str) -> None:
        if key == "copy":
            self.s.set("encode", video="copy")
        else:
            self.s.set("encode", video="encode", encoder=key)

    def refresh(self) -> None:
        if not isValid(self.s):
            return
        s, e, tc = self.s, self.s.edit, self.page.qm.tc
        an = analyse(s)
        self._fill_encoders(an)
        if not an:
            for st in (self.st_size, self.st_rate, self.st_time, self.st_len):
                st.value.setText("–")
            self.est_note.setText("Open a video first.")
            self.test_btn.setEnabled(False)
            self.test_status.setText("")
            return
        info, (w, h) = an["info"], an["size"]
        fps, od = float(an["fps"] or info.fps or 30), max(an["out_dur"], 1e-3)
        size, measured = self._size(an)
        t = None if an["copy"] else self._time(an, an["enc"].key if an["enc"] else None)
        self.st_size.value.setText("≈ " + _mb(size))
        self.st_rate.value.setText(f"{size * 8 / od / 1e6:.1f} Mb/s")
        self.st_time.value.setText("seconds" if an["copy"] else (fmt_eta(t) if t else "–"))
        self.st_len.value.setText(dur(od))
        bits = [f"{w}×{h} · " + f"{fps:.3f}".rstrip("0").rstrip(".") + " fps"]
        if an["copy"]:
            bits.append("copied as is: the size follows the source")
        elif measured:
            bits.append("size measured by the quality test")
        else:
            bits.append("rough size: the quality test below measures it")
        if not an["copy"] and not t:
            bits.append("run the benchmark (Settings › Performance) for a time")
        space = ""
        try:
            folder = preview_output(s, an).parent
            while not folder.exists() and folder != folder.parent:
                folder = folder.parent
            free = shutil.disk_usage(str(folder)).free
            low = size * 1.15 > free
            space = (f"<br><span style='color:{theme.DANGER if low else theme.MUTED}'>{_mb(free)} free on "
                     f"{folder.anchor or folder}" + (" · probably not enough" if low else "") + "</span>")
        except (OSError, ValueError):
            pass
        self.est_note.setText(" · ".join(bits) + space)
        busy = self._sig_obj is not None
        self.test_btn.setEnabled(not an["copy"] and bool(an["enc"]) and bool(tc.ffmpeg) and not busy)
        if busy:
            return
        if an["copy"]:
            self.test_status.setText("Copy keeps the original picture: there's nothing to test.")
        elif self.res and self.res["sig"] == self._sig(an):
            self.test_status.setText(self.res_text)
        elif self.res:
            self.test_status.setText(f"<span style='color:{theme.WARN}'>Settings changed since this test: run it "
                                     f"again.</span><br>{self.res_text}")
        else:
            vm = self._vm(an["enc"])
            self.test_status.setText(
                "Encodes 3 seconds at the playhead with the encoder and quality chosen in the panel"
                + (f", searching the level that reaches VMAF {vm:g}," if vm else "")
                + " and shows the same frame from both. Interpolation isn't applied: this tests the encoder.")

    # ---------- quality test ----------
    def _start_test(self) -> None:
        page, s, e, tc = self.page, self.s, self.s.edit, self.page.qm.tc
        an = analyse(s)
        clip = page.cur_clip
        if not an or an["copy"] or not an["enc"] or not clip or self._sig_obj is not None:
            return
        hit = page.previews._range_at(clip, page.player.source_position())
        if hit is None:
            notify(page, "This clip is removed in Trim: move the playhead to a kept part.", "info")
            return
        (ra, rb), t0 = hit
        if rb - t0 < 1.0:
            t0 = max(ra, rb - 3.0)
        length = min(3.0, rb - t0)
        if length < 0.5:
            notify(page, "This kept part is too short to test.", "warn")
            return
        enc = an["enc"]
        vm = self._vm(enc)
        q = None if vm else e.encode.get("q")
        info, chain = s.info(clip), self._chain(an, clip)
        work = Path(page.settings.temp_dir or TEMP_DIR) / "quality_test"
        self._ctx = (self._sig(an), enc, vm, clip, t0)
        self._cancel = ev = threading.Event()
        self._sig_obj = so = _TestSig()
        so.progress.connect(self._progress)
        so.finished.connect(self._done)
        self.test_btn.setEnabled(False); self.cancel_btn.show()
        self.cmp.clear("Testing…")
        self.test_status.setText("Starting…")

        def work_fn():
            try:
                so.finished.emit(run_test(tc, info, t0, length, enc, q, vm, chain, work, so.progress.emit, ev), "")
            except Cancelled:
                so.finished.emit(None, "Cancelled")
            except Exception as ex:
                so.finished.emit(None, str(ex) or type(ex).__name__)

        threading.Thread(target=work_fn, daemon=True, name="ralenti-quality-test").start()

    def _progress(self, text: str) -> None:
        self.test_status.setText(text + "…")

    def _cancel_test(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.test_status.setText("Stopping after this step…")

    def _done(self, res, err: str) -> None:
        self._sig_obj = None
        self.cancel_btn.hide()
        ctx, self._ctx = self._ctx, None
        if res is None or ctx is None:
            self.cmp.clear(self.cmp.msg if err == "Cancelled" else "The test didn't finish.")
            self.res_text = ""
            self.refresh()
            self.test_status.setText("Cancelled" if err == "Cancelled"
                                     else f"<span style='color:{theme.DANGER}'>Test failed: {err}</span>")
            return
        sig, enc, vm, clip, t0 = ctx
        qtxt = f"{enc.q_flag.upper()} {res['q']}" if res.get("q") is not None else "fixed profile"
        self.cmp.set_images(QImage(res["ref_png"]), QImage(res["enc_png"]), "Original", f"{enc.label} · {qtxt}")
        parts = [f"VMAF <b>{res['vmaf']:.1f}</b>" if res.get("vmaf") is not None
                 else "VMAF needs the full FFmpeg build (see Downloads)", qtxt, f"{res['bps'] * 8 / 1e6:.1f} Mb/s"]
        text = f"Tested at {fmt_ts(self.s.clip_offset(clip) + t0, False)} · " + " · ".join(parts)
        if res.get("searched"):
            n = len(res["scores"])
            text += f" · tried {n} level{'s' if n != 1 else ''} for VMAF {vm:g}"
            if not res["reached"]:
                text += f" · <span style='color:{theme.WARN}'>target not reached: best quality used</span>"
        self.res, self.res_text = dict(res, sig=sig), text
        self.refresh()
