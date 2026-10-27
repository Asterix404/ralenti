"""Live view: watch a job while it renders, with sound.

While you watch, each finished part gets a small playback copy (H.264, a keyframe every quarter second).
FFmpeg stream-copies those copies plus the processed audio into ONE ordinary file, and the player plays
that file like any video (sound included, no separate audio, nothing to keep in sync). As new parts
finish, the next, longer file is built in the background and swapped in at the end of the current one.
While watched, the job keeps its part files (a marker in its temp folder)."""
import ctypes
import os
import shutil
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ..core.commands import write_concat_list
from ..core.config import TEMP_DIR
from ..core.jobs import Status
from ..core.pipeline import info_from_dict, segment_limits
from ..core.tools import NO_WINDOW
from ..core.tools import run as run_tool
from . import theme
from .icons import icon
from .util import fmt_ts, mono_font, notify
from .widgets.fields import button
from .widgets.timeline import Timeline, zoom_controls
from .widgets.volume import VolumeControl
from .workers import run_async

READY = (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia, QMediaPlayer.BufferingMedia)
PLAYING = QMediaPlayer.PlayingState
MARKER = ".live"
END = 10 ** 9


def _quiet_ffmpeg() -> None:
    """Silence the FFmpeg messages printed by Qt's player (only real failures remain visible)."""
    try:
        import PySide6
        for dll in Path(PySide6.__file__).parent.glob("avutil*.dll"):
            ctypes.CDLL(str(dll)).av_log_set_level(8)
    except Exception:
        pass


_quiet_ffmpeg()


def layout(job):
    """(mode, parts [(start, duration, path, ready)], total seconds, audio path or None)."""
    p, st = job.plan, job.state
    a_step = next((s for s in job.steps if s.kind == "audio"), None)
    a_path = p.get("audio_tmp") or ""
    audio = a_path if a_step and a_step.status == Status.DONE and os.path.exists(a_path) else None
    info = info_from_dict(p["info"])
    target = Fraction(st["fps"]) if st.get("fps") else (info.fps or Fraction(30))
    segs = st.get("segments") or []
    limits = segment_limits(segs, Fraction(st["ratio"])) if segs and st.get("ratio") else []
    durs = [float(l / target) for l in limits]
    total = sum(durs)
    if job.status == Status.DONE and os.path.exists(job.output):
        return "final", [(0.0, total or info.duration, job.output, True)], total or info.duration, None
    seg_dir = Path(p.get("tmp", "")) / "seg"
    done = set(st.get("seg_done") or [])
    parts, t = [], 0.0
    for i, d in enumerate(durs):
        f = seg_dir / f"{i:05d}.mkv"
        parts.append((t, d, str(f), i in done and (d <= 0 or f.exists())))
        t += d
    return "parts", parts, total, audio


def proxy_cmd(tc, src: str, dst: str) -> list[str]:
    if tc.binary_for("h264_nvenc"):
        enc = ["-c:v", "h264_nvenc", "-preset", "p3", "-rc", "constqp", "-qp", "21", "-g", "15", "-bf", "0",
               "-forced-idr", "1"]
    else:
        enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-g", "15", "-keyint_min", "15",
               "-sc_threshold", "0", "-bf", "0"]
    return [tc.ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error", "-i", src, "-map", "0:v:0", "-an",
            "-vf", "scale=-2:'min(ih,1080)',format=yuv420p", *enc, dst]


class _Proxies(QObject):
    """Background maker of playback copies, in the order given by set_todo()."""
    ready = Signal(int)
    failed = Signal(int)

    def __init__(self, tc, parent):
        super().__init__(parent)
        self.tc, self.todo, self.proc = tc, [], None
        self.lock, self.stop = threading.Lock(), threading.Event()
        threading.Thread(target=self._run, daemon=True, name="ralenti-live-proxy").start()

    def set_todo(self, items) -> None:
        with self.lock:
            self.todo = list(items)

    def _emit(self, sig, i) -> None:
        try:
            sig.emit(i)
        except RuntimeError:
            pass

    def _run(self) -> None:
        while not self.stop.is_set():
            with self.lock:
                item = self.todo.pop(0) if self.todo else None
            if item is None:
                self.stop.wait(0.3)
                continue
            i, src, dst = item
            dst = Path(dst)
            if dst.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.stem + ".tmp.mp4")
            ok = False
            try:
                self.proc = subprocess.Popen(proxy_cmd(self.tc, src, str(tmp)), stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                             creationflags=NO_WINDOW)
                ok = self.proc.wait() == 0 and tmp.exists() and tmp.stat().st_size > 0
                if ok:
                    os.replace(tmp, dst)
            except OSError:
                ok = False
            finally:
                self.proc = None
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            if self.stop.is_set():
                break
            self._emit(self.ready if ok else self.failed, i)

    def close(self) -> None:
        self.stop.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass


class _Built(QObject):
    done = Signal(int, str, str)        # generation, path, error


class _Canvas(QWidget):
    def __init__(self):
        super().__init__()
        self.img, self.msg = None, ""
        self.setMinimumSize(480, 270)

    def set_frame(self, f) -> None:
        if f.isValid():
            img = f.toImage()
            if not img.isNull():
                self.img = img
                self.update()

    def set_msg(self, m: str) -> None:
        if m != self.msg:
            self.msg = m
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.img is not None:
            a = QRectF(self.rect()); w, h = self.img.width(), self.img.height()
            s = min(a.width() / w, a.height() / h)
            p.drawImage(QRectF(a.center().x() - w * s / 2, a.center().y() - h * s / 2, w * s, h * s), self.img)
        if self.msg:
            f = QFont(self.font()); f.setPointSizeF(10.5); f.setWeight(QFont.DemiBold); p.setFont(f)
            fm = p.fontMetrics()
            tw, th = fm.horizontalAdvance(self.msg) + 36, fm.height() + 18
            r = QRectF((self.width() - tw) / 2, (self.height() - th) / 2, tw, th)
            p.setRenderHint(QPainter.Antialiasing); p.setPen(Qt.NoPen); p.setBrush(QColor(0, 0, 0, 190))
            p.drawRoundedRect(r, th / 2, th / 2)
            p.setPen(QColor(theme.TEXT)); p.drawText(r, Qt.AlignCenter, self.msg)


class LiveView(QWidget):
    changed = Signal()

    def __init__(self, qm):
        super().__init__()
        self.qm, self.jid = qm, None
        self.mode, self.parts, self.total, self.audio_path = None, [], 0.0, None
        self.src, self.offset, self.loaded_end, self.pending = None, 0.0, 0.0, None
        self.next = None                    # prebuilt (path, offset, end)
        self.building = None                # (gen, a, b, target, now, out, has_audio)
        self.wait, self.target, self.gen = None, 0.0, 0
        self.want_play, self.ended, self.hidden = True, False, True
        self.proxies, self.pfails, self.bfails = None, {}, 0
        self._icon, self._last_pos, self._stall, self._recovers, self._recover_at = None, -1.0, 0, 0, -10.0
        self.snapshots: list[Path] = []
        self._sig = _Built(self)
        self._sig.done.connect(self._built)
        self.setFocusPolicy(Qt.StrongFocus)

        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        self.canvas = _Canvas()
        frame = QFrame(); frame.setObjectName("Card")
        fl = QVBoxLayout(frame); fl.setContentsMargins(1, 1, 1, 1); fl.addWidget(self.canvas)
        self.timeline = Timeline(); self.timeline.seek.connect(self.seek)
        self.time = QLabel(); self.time.setObjectName("Muted"); self.time.setFont(mono_font(9))
        self.play_b = self._tb("pause-fill", "Play / pause (Space)", self.toggle, 22)
        mute = self._tb("speaker-high", "Mute", lambda: self.aout.setMuted(not self.aout.isMuted()))
        self.volume = VolumeControl(mute)
        self.ext_btn = button("Open in external player", self._external, "ghost", icon="arrow-square-out")
        self.ext_btn.setToolTip("Joins what's rendered so far (with sound) into one file and opens it")
        row = QHBoxLayout(); row.setSpacing(4)
        row.addWidget(self._tb("skip-back", "Back 5 s (←)", lambda: self.seek(self.position() - 5, quiet=True)))
        row.addWidget(self.play_b)
        row.addWidget(self._tb("skip-forward", "Forward 5 s (→)", lambda: self.seek(self.position() + 5)))
        row.addSpacing(10); row.addWidget(self.time); row.addSpacing(8); row.addWidget(zoom_controls(self.timeline))
        row.addStretch(1); row.addWidget(self.ext_btn); row.addWidget(self.volume)
        self.status = QLabel(); self.status.setObjectName("Hint"); self.status.setWordWrap(True)
        v.addWidget(frame, 1); v.addWidget(self.timeline); v.addLayout(row); v.addWidget(self.status)

        self.vp, self.sink, self.aout = QMediaPlayer(self), QVideoSink(self), QAudioOutput(self)
        self.vp.setVideoOutput(self.sink)
        self.vp.setAudioOutput(self.aout)
        self.volume.bind(self.aout)
        self.sink.videoFrameChanged.connect(self.canvas.set_frame)
        self.vp.mediaStatusChanged.connect(self._vstatus)
        self.vp.errorOccurred.connect(lambda *_: self._verror())
        for k, fn in (("Space", self.toggle), ("Left", lambda: self.seek(self.position() - 5, quiet=True)),
                      ("Right", lambda: self.seek(self.position() + 5))):
            sc = QShortcut(QKeySequence(k), self); sc.setContext(Qt.WidgetWithChildrenShortcut); sc.activated.connect(fn)
        self.poll = QTimer(self); self.poll.setInterval(1000); self.poll.timeout.connect(self.refresh)
        self.tick_t = QTimer(self); self.tick_t.setInterval(200); self.tick_t.timeout.connect(self._tick)

    def _tb(self, name, tip, fn, size=20):
        b = QToolButton(); b.setIcon(icon(name, "muted", active="text", checked=None)); b.setIconSize(QSize(size, size))
        b.setToolTip(tip); b.setCursor(Qt.PointingHandCursor); b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(lambda: fn())
        return b

    # ---------- watching ----------
    def _job(self):
        return self.qm.get(self.jid) if self.jid else None

    def set_active(self, on: bool) -> None:
        self.hidden = not on
        if on:
            self.poll.start(); self.tick_t.start(); self.refresh(); self.setFocus()
        else:
            self.poll.stop(); self.tick_t.stop()
        self._apply()

    def watch(self, jid) -> None:
        if jid == self.jid:
            self.refresh()
            return
        self._release()
        self.jid = jid
        self.mode, self.parts, self.total, self.audio_path = None, [], 0.0, None
        self.want_play, self.pfails, self.bfails, self.target = True, {}, 0, 0.0
        self.proxies = _Proxies(self.qm.tc, self)
        self.proxies.ready.connect(lambda _i: self._after_proxy())
        self.proxies.failed.connect(self._proxy_failed)
        self.refresh()

    def _mark(self, on: bool) -> None:
        job = self._job()
        tmp = job.plan.get("tmp") if job else None
        if not tmp:
            return
        f = Path(tmp) / MARKER
        try:
            if on:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.touch()
            else:
                f.unlink(missing_ok=True)
        except OSError:
            pass

    def _release(self) -> None:
        if self.proxies:
            self.proxies.close()
            self.proxies = None
        self._stop_all()
        job = self._job()
        self._mark(False)
        if job and job.status == Status.DONE and job.plan.get("tmp"):
            shutil.rmtree(job.plan["tmp"], ignore_errors=True)
        for f in self.snapshots:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
        self.snapshots, self.jid = [], None

    def _stop_all(self) -> None:
        self.vp.stop(); self.vp.setSource(QUrl())
        self.src, self.offset, self.loaded_end, self.pending = None, 0.0, 0.0, None
        self.next, self.building, self.wait, self.ended = None, None, None, False
        self._stall, self._recovers = 0, 0
        self.canvas.img = None
        self.canvas.set_msg("")
        self.canvas.update()

    # ---------- parts and playback copies ----------
    def _live_dir(self) -> Path:
        return Path(self.parts[0][2]).parent / "live"

    def _proxy(self, i: int) -> Path:
        return self._live_dir() / f"{i:05d}.mp4"

    def _play(self, i: int):
        """Playback copy of part i ('' for an empty part), or None if it isn't ready yet."""
        _s, d, _p, ok = self.parts[i]
        if not ok:
            return None
        if d <= 0:
            return ""
        q = self._proxy(i)
        return str(q) if q.exists() else None

    def _idx(self, t: float) -> int:
        for i, (s, d, _p, _ok) in enumerate(self.parts):
            if s <= t < s + d:
                return i
        return max(0, len(self.parts) - 1)

    def _run(self, i: int, back: int = 40) -> tuple[int, int]:
        a = b = i
        while a > 0 and i - a < back and self._play(a - 1) is not None:
            a -= 1
        while b + 1 < len(self.parts) and self._play(b + 1) is not None:
            b += 1
        return a, b

    def avail_end(self) -> float:
        e = 0.0
        for s, d, _p, ok in self.parts:
            if not ok:
                break
            e = s + d
        return e

    def _playable_spans(self) -> list:
        spans, start, end = [], None, 0.0
        for i, (s, d, _p, _ok) in enumerate(self.parts):
            if self._play(i) is not None:
                start = s if start is None else start
                end = s + d
            elif start is not None:
                spans.append((start, end)); start = None
        if start is not None:
            spans.append((start, end))
        return spans

    def _update_todo(self) -> None:
        if not self.proxies or self.mode != "parts" or not self.parts:
            return
        c = self._idx(self.position())
        order = list(range(c, len(self.parts))) + list(range(c - 1, -1, -1))
        self.proxies.set_todo([(k, self.parts[k][2], str(self._proxy(k))) for k in order
                               if self.parts[k][3] and self.parts[k][1] > 0 and not self._proxy(k).exists()
                               and self.pfails.get(k, 0) < 3])

    def _proxy_failed(self, i: int) -> None:
        self.pfails[i] = self.pfails.get(i, 0) + 1
        if self.pfails[i] >= 3:
            self.canvas.set_msg(f"Part {i + 1} couldn't be prepared for playback")
        self._update_todo()

    def _after_proxy(self) -> None:
        if self.wait and self.wait[0] != END and self._play(self.wait[0]) is not None:
            t = self.wait[1]
            self.wait = None
            self._request(t, now=True)
        self._update_timeline()

    # ---------- building the playable file ----------
    def _request(self, t: float, now: bool) -> None:
        i = self._idx(t)
        if self._play(i) is None:
            if now:
                self._wait_at(i, t)
            return
        a, b = self._run(i)
        if self.building and self.building[1:3] == (a, b):
            if now:
                g, a0, b0, _t, _n, out, ha = self.building
                self.building = (g, a0, b0, t, True, out, ha)
                self._preparing(t)
            return
        files = [(self._play(k), self.parts[k][1]) for k in range(a, b + 1) if self._play(k)]
        if not files:
            return
        self.gen += 1
        off = self.parts[a][0]
        dur = self.parts[b][0] + self.parts[b][1] - off
        out = self._live_dir() / f"snap-{self.gen:04d}.mkv"
        self.building = (self.gen, a, b, t, now, str(out), bool(self.audio_path))
        if now:
            self._preparing(t)
        threading.Thread(target=self._build, args=(self.gen, files, self.audio_path, off, dur, out),
                         daemon=True, name="ralenti-live-build").start()

    def _preparing(self, t: float) -> None:
        self.target, self.wait = t, None
        if self.vp.playbackState() == PLAYING:
            self.vp.pause()
        self.canvas.set_msg("Preparing playback…")

    def _build(self, gen, files, audio, off, dur, out: Path) -> None:
        lst = out.with_suffix(".ffconcat")
        lines = ["ffconcat version 1.0"]
        for path, d in files:
            lines += [f"file '{Path(path).name}'", f"duration {d:.6f}"]
        err = ""
        try:
            lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
            cmd = [self.qm.tc.ffmpeg, "-hide_banner", "-nostdin", "-y", "-v", "error",
                   "-f", "concat", "-safe", "0", "-i", str(lst)]
            if audio:
                cmd += ["-ss", f"{off:.3f}", "-t", f"{dur:.3f}", "-i", audio]
            cmd += ["-map", "0:v:0"] + (["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"] if audio else [])
            cmd += ["-c:v", "copy", "-t", f"{dur:.3f}", str(out)]
            r = run_tool(cmd, timeout=600)
            if r.returncode != 0 or not out.exists():
                err = (r.stderr.strip().splitlines() or ["FFmpeg failed"])[-1]
        except Exception as e:
            err = str(e) or type(e).__name__
        finally:
            try:
                lst.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            self._sig.done.emit(gen, str(out), err)
        except RuntimeError:
            pass

    def _built(self, gen: int, path: str, err: str) -> None:
        b = self.building
        if not b or b[0] != gen:
            self._drop(path)
            return
        _g, a, bb, t, now, _out, _ha = b
        self.building = None
        if err:
            self.bfails += 1
            self._drop(path)
            self.canvas.set_msg("Couldn't prepare playback · retrying…" if self.bfails < 4
                                else f"Couldn't prepare playback: {err}")
            if self.bfails < 4:
                QTimer.singleShot(1000, lambda: self._request(t, now=now))
            return
        self.bfails = 0
        snap = (path, self.parts[a][0], self.parts[bb][0] + self.parts[bb][1])
        if now or self.src is None:
            self._load(snap, t)
        else:
            if self.next:
                self._drop(self.next[0])
            self.next = snap
        self._cleanup()

    def _drop(self, path) -> None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    def _cleanup(self) -> None:
        if not self.parts:
            return
        keep = {self.src, self.next[0] if self.next else None, self.building[5] if self.building else None}
        for f in self._live_dir().glob("snap-*.mkv"):
            if str(f) not in keep:
                self._drop(f)

    def _load(self, snap, t: float) -> None:
        path, off, end = snap
        if self.next and self.next[0] == path:
            self.next = None
        self.src, self.offset, self.loaded_end = path, off, end
        self.pending, self.wait, self.ended = max(off, min(t, end - 0.02)), None, False
        self._stall = 0
        self.canvas.set_msg("")
        self.vp.setSource(QUrl.fromLocalFile(path))

    def _wait_at(self, i: int, t: float) -> None:
        self.wait, self.target = (i, t), t
        if self.vp.playbackState() == PLAYING:
            self.vp.pause()
        if i == END:
            msg = "Finishing the job…"
        elif self.parts[i][3]:
            msg = f"Preparing part {i + 1} for playback…"
        else:
            msg = f"Waiting for part {i + 1} of {len(self.parts)}… it's rendering now"
        self.canvas.set_msg(msg)
        self._update_todo()
        self._apply()

    # ---------- position ----------
    def position(self) -> float:
        if self.wait is not None:
            return self.wait[1]
        if self.building and self.building[4]:
            return self.building[3]
        if self.pending is not None:
            return self.pending
        if self.src is None:
            return self.target
        return self.offset + self.vp.position() / 1000

    @property
    def cur(self) -> int:
        if not self.parts:
            return -1
        return self._idx(self.position()) if self.mode == "parts" else 0

    # ---------- refresh from the job ----------
    def refresh(self) -> None:
        job = self._job()
        if job is None:
            if self.jid:
                self._stop_all(); self.jid = None
                self.canvas.set_msg("This job was removed from the queue.")
                self.changed.emit()
            return
        if job.status != Status.DONE:
            self._mark(True)
        mode, parts, total, audio = layout(job)
        t = self.position()
        prev = self.mode
        self.mode, self.parts, self.total = mode, parts, total
        if mode == "final" and prev != "final":
            self._stop_all()
            self.audio_path = None
            self._load((parts[0][2], 0.0, total), min(t, max(0.0, total - 0.05)))
            if prev is not None and job.plan.get("tmp"):
                tmp = job.plan["tmp"]
                self._mark(False)
                QTimer.singleShot(3000, lambda: shutil.rmtree(tmp, ignore_errors=True))
        elif mode == "parts":
            if audio != self.audio_path:
                self.audio_path = audio
                if self.src:                       # rebuild once so the sound is included
                    self.next = None
                    self._request(t, now=True)
            if self.wait and self.wait[0] == END and self.avail_end() > self.loaded_end + 0.01:
                self.wait = None
                self._request(self.loaded_end, now=True)
            elif self.src is None and not self.building and not self.wait and self.avail_end() > 0:
                self._request(min(self.target, self.avail_end() - 0.05), now=True)
            elif self.wait and self.wait[0] != END and self._play(self.wait[0]) is not None:
                w = self.wait[1]
                self.wait = None
                self._request(w, now=True)
            if self.avail_end() <= 0:
                self.canvas.set_msg("Waiting for the first part…")
        self._update_todo()
        self._update_timeline()
        self._status_text(job)
        self.changed.emit()

    def _update_timeline(self) -> None:
        end = self.avail_end()
        self.timeline.set_duration(self.total, keep=True)
        if self.mode == "parts":
            self.timeline.set_keyframes([s for s, *_ in self.parts[1:]])
            spans = [(0.0, end, False)] if end > 0 else []
            self.timeline.set_takes(spans + [(a, b, True) for a, b in self._playable_spans()])
        else:
            self.timeline.set_keyframes([]); self.timeline.set_takes([])

    def _status_text(self, job) -> None:
        a_step = next((s for s in job.steps if s.kind == "audio"), None)
        end = self.avail_end()
        if self.mode == "final":
            text = "Finished · this is the final video."
        else:
            ready = sum(1 for x in self.parts if x[3])
            prepared = sum(1 for i in range(len(self.parts)) if self._play(i))
            text = (f"Rendered {fmt_ts(end, False)} of {fmt_ts(self.total, False)} · {ready} of {len(self.parts)} parts"
                    + (f" · {prepared} ready to play" if prepared < ready else ""))
            text += (" · with sound" if self.audio_path else
                     " · this job has no audio" if a_step is None else " · sound appears once the audio is processed")
        self.status.setText(text + ". Click the timeline to jump to any rendered moment.")

    # ---------- player events ----------
    def _vstatus(self, s) -> None:
        if s in READY and self.pending is not None:
            self.vp.setPosition(int(max(0.0, self.pending - self.offset) * 1000))
            self.pending = None
            self._apply()
        elif s == QMediaPlayer.EndOfMedia:
            self._at_end()
        elif s == QMediaPlayer.InvalidMedia:
            self._verror()

    def _verror(self) -> None:
        if self.src is None:
            return
        t = self.pending if self.pending is not None else self.position()
        snap = (self.src, self.offset, self.loaded_end)
        self.src, self.pending, self.target = None, None, t
        self.vp.stop()
        self.canvas.set_msg("Couldn't read the video · retrying…")
        if self.mode == "final":
            QTimer.singleShot(800, lambda: self.src is None and self._load(snap, self.target))
        else:
            QTimer.singleShot(800, lambda: self.src is None and self._request(self.target, now=True))

    def _at_end(self) -> None:
        if self.mode != "parts":
            self.ended = True
            self._apply()
            return
        le = self.loaded_end
        if self.next and self.next[1] <= le + 0.01 < self.next[2]:
            self._load(self.next, le)
            return
        n = next((k for k, (s, d, _p, _ok) in enumerate(self.parts) if s >= le - 0.01 and d > 0), None)
        if n is None:
            self._wait_at(END, le)
        elif self._play(n) is not None:
            self._request(le, now=True)
        else:
            self._wait_at(n, le)

    # ---------- playback ----------
    def _go(self) -> bool:
        return (self.want_play and not self.hidden and not self.ended and self.wait is None
                and not (self.building and self.building[4]) and self.src is not None and self.pending is None)

    def _apply(self) -> None:
        go = self._go()
        if self.src is not None and self.pending is None:
            st = self.vp.playbackState()
            if go and st != PLAYING:
                self.vp.play()
            elif not go and st == PLAYING:
                self.vp.pause()
        name = "pause-fill" if self.want_play and not self.ended else "play-fill"
        if name != self._icon:
            self._icon = name
            self.play_b.setIcon(icon(name, "text", checked=None))

    def seek(self, t: float, quiet: bool = False) -> None:
        end = self.total if self.mode == "final" else self.avail_end()
        if end <= 0:
            return
        if t > end - 0.05:
            if not quiet and t > end + 0.3:
                notify(self, "That part isn't rendered yet: jumped to the newest rendered moment.", "info")
            t = end - 0.05
        t = max(0.0, t)
        self.ended, self._recovers = False, 0
        if self.src and self.pending is None and self.offset <= t < self.loaded_end - 0.02:
            self.wait = None
            self.canvas.set_msg("")
            self.vp.setPosition(int((t - self.offset) * 1000))
            self._stall = 0
            self._apply()
        elif self.next and self.next[1] <= t < self.next[2] - 0.02:
            self._load(self.next, t)
        elif self.mode == "parts":
            self._request(t, now=True)

    def toggle(self) -> None:
        if self.ended:
            self.want_play = True
            self.seek(0.0, quiet=True)
            return
        self.want_play = not self.want_play
        self._apply()

    def _tick(self) -> None:
        pos = self.position()
        self.timeline.set_position(pos)
        self.time.setText(f"{fmt_ts(pos)} / {fmt_ts(self.total)}")
        playing = self._go() and self.vp.playbackState() == PLAYING
        if playing and self.mode == "parts" and not self.building and not self.next \
                and self.loaded_end - pos < 6:
            n = next((k for k, (s, d, _p, _ok) in enumerate(self.parts) if s >= self.loaded_end - 0.01 and d > 0),
                     None)
            if n is not None and self._play(n) is not None:
                self._request(self.loaded_end, now=False)      # build the next, longer file ahead of time
        if not playing:
            self._last_pos, self._stall = pos, 0
            return
        if abs(pos - self._last_pos) < 1e-3:
            self._stall += 1
            if self._stall >= 6:                               # frozen for a second: reload at this spot
                self._stall = 0
                same = abs(pos - self._recover_at) < 0.5
                self._recovers = self._recovers + 1 if same else 1
                self._recover_at = pos
                self._load((self.src, self.offset, self.loaded_end), pos + (0.5 if self._recovers >= 3 else 0.0))
        else:
            self._stall = 0
        self._last_pos = pos

    # ---------- external player ----------
    def _external(self) -> None:
        job = self._job()
        if job and job.status == Status.DONE and os.path.exists(job.output):
            os.startfile(job.output)
            return
        end = self.avail_end()
        files = [p for s, d, p, ok in self.parts if ok and d > 0 and s < end]
        if not files or end <= 0:
            notify(self, "Nothing is rendered yet.", "info")
            return
        out_dir = Path(self.qm.settings.temp_dir or TEMP_DIR) / "live"
        out_dir.mkdir(parents=True, exist_ok=True)
        out, lst = out_dir / f"{self.jid}-{int(time.time())}.mkv", out_dir / f"{self.jid}.txt"
        write_concat_list(files, lst)
        audio = self.audio_path
        cmd = [self.qm.tc.ffmpeg, "-hide_banner", "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
        if audio:
            cmd += ["-i", audio]
        cmd += ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio else []) + ["-c", "copy", "-t", f"{end:.3f}", str(out)]
        self.ext_btn.setEnabled(False); self.ext_btn.setText("Preparing…")

        def work():
            r = run_tool(cmd, timeout=900)
            if r.returncode != 0 or not out.exists():
                raise RuntimeError((r.stderr.strip().splitlines() or ["FFmpeg failed"])[-1])
            return str(out)

        def done(path):
            self.ext_btn.setEnabled(True); self.ext_btn.setText("Open in external player")
            self.snapshots.append(Path(path))
            os.startfile(path)
            notify(self, f"Opened the first {fmt_ts(end, False)}" + (" with sound" if audio else "")
                   + ". Open again later to include more.", "ok")

        def fail(msg):
            self.ext_btn.setEnabled(True); self.ext_btn.setText("Open in external player")
            notify(self, f"Couldn't prepare the file: {msg}", "error")

        run_async(work, done, fail)


def open_live(parent, qm, job) -> None:
    win = parent.window()
    if hasattr(win, "show_live"):
        win.show_live(job.id)
