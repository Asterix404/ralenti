"""QueueManager: runs up to N jobs at once (fixed, or Auto: scaled from measured throughput and system
load), plus a separate preview lane. Pause suspends jobs instantly; stopped or interrupted jobs resume
from their last finished part. Events: added, removed, started, step, progress, finished, paused, idle,
preview_idle."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from . import pipeline
from .config import APP_DIR, LOGS_DIR, QUEUE_FILE, TEMP_DIR, Settings
from .jobs import Job, Status
from .progress import fmt_eta
from .runner import Cancelled, DiskFull, StepContext
from .tools import Toolchain

Listener = Callable[[str, Job | None], None]
RATES_FILE = APP_DIR / "rates.json"
FINISHED = (Status.DONE, Status.FAILED, Status.CANCELLED)
PREVIEW = "preview"
LIGHT = ("cut",)          # never take a concurrency slot
MAX_AUTO = 4


def rate_key(job: Job) -> str:
    s, p = job.spec, job.plan
    if job.kind == "interpolate":
        return f"interpolate:{s.get('model')}:{'vmaf' if p.get('vmaf') else 'direct'}"
    if job.kind == "encode":
        return f"encode:{s.get('encoder')}:{'vmaf' if p.get('vmaf') else 'fixed'}"
    if job.kind == "merge":
        return f"merge:{'reencode' if p.get('reencode') else 'copy'}"
    return job.kind


class AutoScaler:
    """Hill-climbs the number of concurrent jobs: tries one more while the GPU has headroom, keeps it
    only if total throughput rises clearly, and steps back when memory runs short."""
    WARMUP, WINDOW, MIN_GAIN, COOLDOWN = 40.0, 30.0, 1.08, 300.0

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.level, self.ceiling, self.trial, self.cool = 1, MAX_AUTO, None, 0.0
        self.since, self.samples = None, []

    def hold(self, now: float | None = None) -> None:
        """Restart the measurement (e.g. after a pause)."""
        self.since = time.monotonic() if now is None else now
        self.samples = []

    def _set(self, level: int, now: float) -> None:
        self.level = max(1, min(MAX_AUTO, level))
        self.since, self.samples = now, []

    def feed(self, tp, util, vram, ram, waiting, single_util=None, now=None) -> int:
        now = time.monotonic() if now is None else now
        if self.since is None:
            self.since = now
        if self.cool and now >= self.cool:
            self.ceiling, self.cool = MAX_AUTO, 0.0
        self.samples = [x for x in self.samples if now - x[0] <= self.WINDOW] + [(now, tp)]
        if now - self.since < self.WARMUP or len(self.samples) < 4:
            return self.level
        avg = sum(v for _, v in self.samples) / len(self.samples)
        if (vram or 0) > 0.92 or (ram or 0) > 0.92:
            if self.level > 1:
                self._set(self.level - 1, now)
                self.ceiling, self.cool = self.level, now + self.COOLDOWN
            self.trial = None
            return self.level
        if self.trial is not None:
            before, self.trial = self.trial, None
            if avg < before * self.MIN_GAIN:
                self._set(self.level - 1, now)
                self.ceiling, self.cool = self.level, now + self.COOLDOWN
            else:
                self.since, self.samples = now, []
            return self.level
        headroom = (util is None or util < 85) and (vram or 0) < 0.8 and (ram or 0) < 0.85
        saturated = single_util is not None and single_util >= 90 and self.level == 1
        if waiting and headroom and not saturated and self.level < self.ceiling and avg > 0:
            self.trial = avg
            self._set(self.level + 1, now)
        return self.level


class _Run:
    def __init__(self, job: Job):
        self.job = job
        self.cancel, self.pause = threading.Event(), threading.Event()
        self.ctx: StepContext | None = None
        self.thread: threading.Thread | None = None
        self.units, self.last, self.p0 = 0.0, 0.0, 0.0
        self.solo, self.hold = True, False

    @property
    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


class QueueManager:
    def __init__(self, tc: Toolchain, settings: Settings, path: Path = QUEUE_FILE):
        self.tc, self.settings, self.path = tc, settings, Path(path)
        self.jobs: list[Job] = []
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._listeners: list[Listener] = []
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._runs: dict[str, _Run] = {}
        self._prun: _Run | None = None
        self._p_alive = False
        self._stop_after = self._paused_all = False
        self._last_emit: dict[str, float] = {}
        self.run_started_at = 0.0
        self.user_stopped = False
        self.rates: dict[str, float] = self._load_rates()
        self.scaler = AutoScaler()
        self._done_units, self._m_last = 0.0, time.monotonic()
        self._util_acc: dict[str, list] = {}

    # ---------- events ----------
    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def _emit(self, event: str, job: Job | None = None) -> None:
        for fn in list(self._listeners):
            try:
                fn(event, job)
            except Exception:
                traceback.print_exc()

    def _notify(self, job: Job) -> None:
        now = time.monotonic()
        if now - self._last_emit.get(job.id, 0.0) >= 0.25:
            self._last_emit[job.id] = now
            self._emit("progress", job)

    # ---------- persistence ----------
    def previews_dir(self) -> Path:
        return Path(self.settings.temp_dir or TEMP_DIR) / "previews"

    def load(self) -> None:
        import shutil
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        with self._lock:
            for d in data:
                try:
                    job = Job.from_dict(d)
                except (TypeError, ValueError, KeyError):
                    continue
                if job.kind == PREVIEW:
                    continue
                if job.status in (Status.RUNNING, Status.PAUSED):
                    was_paused = job.status == Status.PAUSED
                    job.reset(full=False)          # finished steps and parts are kept
                    if was_paused:
                        job.status = Status.PAUSED
                self.jobs.append(job)
        shutil.rmtree(self.previews_dir(), ignore_errors=True)

    def save(self) -> None:
        with self._lock:
            data = [j.to_dict() for j in self.jobs if j.kind != PREVIEW]
        with self._save_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    @staticmethod
    def _load_rates() -> dict[str, float]:
        try:
            return {k: float(v) for k, v in json.loads(RATES_FILE.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save_rates(self) -> None:
        try:
            RATES_FILE.write_text(json.dumps(self.rates, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _learn(self, job: Job) -> None:
        units = pipeline.work_units(job)
        secs = job.finished - job.started
        if units < 0.5 or secs < 1:
            return
        k, r = rate_key(job), secs / units
        old = self.rates.get(k)
        self.rates[k] = r if old is None else old * 0.5 + r * 0.5

    # ---------- editing ----------
    def get(self, job_id: str | None) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def _run_of(self, job_id: str) -> _Run | None:
        r = self._runs.get(job_id)
        if r is None and self._prun is not None and self._prun.job.id == job_id:
            r = self._prun
        return r if r is not None and r.alive else None

    def live_ids(self) -> set[str]:
        ids = {k for k, r in self._runs.items() if r.alive}
        if self._prun is not None and self._prun.alive:
            ids.add(self._prun.job.id)
        return ids

    def add(self, job: Job) -> None:
        with self._lock:
            self.jobs.append(job)
        self.save()
        self._emit("added", job)
        if job.kind == PREVIEW:
            self.start_preview()

    def remove(self, job_id: str) -> bool:
        with self._lock:
            job = self.get(job_id)
            if not job or self._run_of(job_id):
                return False
            self.jobs.remove(job)
        pipeline.cleanup_temp(job, remove_part=True)
        if job.kind == PREVIEW:
            try:
                Path(job.output).unlink(missing_ok=True)
            except OSError:
                pass
        self.save()
        self._emit("removed", job)
        return True

    def move(self, job_id: str, delta: int) -> None:
        with self._lock:
            job = self.get(job_id)
            if not job:
                return
            i = self.jobs.index(job)
            j = max(0, min(len(self.jobs) - 1, i + delta))
            self.jobs.insert(j, self.jobs.pop(i))
        self.save()

    def retry(self, job_id: str, from_scratch: bool = False) -> None:
        job = self.get(job_id)
        if not job or self._run_of(job_id):
            return
        if from_scratch:
            pipeline.cleanup_temp(job, remove_part=True)
        job.reset(full=from_scratch)
        self.save()
        self._emit("progress", job)
        if job.kind == PREVIEW:
            self.start_preview()

    def cancel_job(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job:
            return
        r = self._run_of(job_id)
        if r is not None:
            r.cancel.set()
            r.pause.clear()
            if r.ctx:
                r.ctx.resume()
                r.ctx.kill()
        elif job.status in (Status.QUEUED, Status.PAUSED):
            job.status = Status.CANCELLED
            self.save()
            self._emit("progress", job)

    def clear_finished(self) -> None:
        for j in [j for j in self.jobs if j.status == Status.DONE]:
            self.remove(j.id)

    # ---------- pause / resume ----------
    def pause(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job:
            return
        r = self._run_of(job_id)
        if r is not None:
            r.pause.set()
            if r.ctx:
                r.ctx.suspend()
        elif job.status != Status.QUEUED:
            return
        job.status = Status.PAUSED
        self.scaler.hold()
        self.save()
        self._emit("paused", job)

    def resume(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job or job.status != Status.PAUSED:
            return
        r = self._run_of(job_id)
        if r is not None:
            r.pause.clear()
            if r.ctx:
                r.ctx.resume()
            job.status = Status.RUNNING
        else:
            job.status = Status.QUEUED
            if job.kind != PREVIEW and not self.is_running:
                self.start()
        self.scaler.hold()
        self.save()
        self._emit("paused", job)

    def _main_runs(self) -> list[_Run]:
        return [r for r in self._runs.values() if r.alive]

    @property
    def all_paused(self) -> bool:
        runs = self._main_runs()
        return bool(runs) and all(r.pause.is_set() for r in runs)

    def pause_all(self) -> None:
        self._paused_all = True
        for r in self._main_runs():
            self.pause(r.job.id)
        self._emit("paused")

    def resume_all(self) -> None:
        self._paused_all = False
        for r in self._main_runs():
            self.resume(r.job.id)
        if not self.is_running and self._next() is not None:
            self.start()
        self._emit("paused")

    # ---------- overview ----------
    def _main_jobs(self) -> list[Job]:
        with self._lock:
            return [j for j in self.jobs if j.kind != PREVIEW]

    @property
    def running_count(self) -> int:
        return len(self._main_runs())

    def target(self) -> int:
        c = str(getattr(self.settings, "concurrency", "auto") or "auto")
        if c == "auto":
            return self.scaler.level
        try:
            return max(1, min(8, int(c)))
        except ValueError:
            return 1

    def concurrency_label(self) -> str:
        c = str(getattr(self.settings, "concurrency", "auto") or "auto")
        return f"Auto · {self.scaler.level} at once" if c == "auto" else f"{c} at once"

    def run_jobs(self) -> list[Job]:
        t = self.run_started_at
        return [j for j in self._main_jobs() if j.status in (Status.QUEUED, Status.RUNNING, Status.PAUSED)
                or (t and j.status in FINISHED and j.finished >= t)]

    def run_progress(self) -> tuple[float, int, int]:
        jobs = [j for j in self.run_jobs() if j.status not in (Status.FAILED, Status.CANCELLED)]
        if not jobs:
            return 0.0, 0, 0
        weights = [max(1.0, pipeline.work_units(j)) for j in jobs]
        done_w = sum(w * (1.0 if j.status == Status.DONE else j.progress) for j, w in zip(jobs, weights))
        return done_w / sum(weights), sum(j.status == Status.DONE for j in jobs), len(jobs)

    def remaining_estimate(self) -> tuple[float | None, bool]:
        jobs = self._main_jobs()
        active = [(j, self._runs[j.id]) for j in jobs if j.id in self._runs and self._runs[j.id].alive]
        queued = [j for j in jobs if j.status == Status.QUEUED]
        if not active and not queued:
            return None, True
        total, complete, live = 0.0, True, {}
        for j, r in active:
            paused = r.ctx.paused_seconds if r.ctx else 0.0
            elapsed, dp = time.time() - j.started - paused, j.progress - r.p0
            if dp > 0.02 and elapsed > 3:
                per_full = elapsed / dp
                total += per_full * (1 - j.progress)
                if (u := pipeline.work_units(j)) > 0:
                    live[rate_key(j)] = per_full / u
            else:
                complete = False
        for j in queued:
            rate = live.get(rate_key(j)) or self.rates.get(rate_key(j))
            if rate:
                total += rate * pipeline.work_units(j) * (1 - j.progress)
            elif j.kind == "cut" or (j.kind == "merge" and not j.plan.get("reencode")):
                total += 2
            else:
                complete = False
        par = max(1, sum(1 for j, _ in active if j.kind not in LIGHT))
        return (total / par if total > 0 else None), complete

    # ---------- running ----------
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def preview_running(self) -> bool:
        return self._p_alive

    def start(self) -> None:
        self._stop_after = False
        self._paused_all = False
        if self.is_running:
            return
        self.run_started_at = time.time()
        self.user_stopped = False
        self._running.set()
        self.scaler.reset()
        self._m_last = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ralenti-queue")
        self._thread.start()
        self._emit("started")

    def stop(self, kill_current: bool = True) -> None:
        """kill_current=False: let running jobs finish, start no new ones.
        kill_current=True: stop now; interrupted jobs resume from their last finished part."""
        self.user_stopped = True
        if not kill_current:
            self._stop_after = True
            self._emit("progress")
            return
        self._running.clear()
        for r in self._main_runs():
            r.hold = r.job.status == Status.PAUSED
            r.cancel.set()
            r.pause.clear()
            if r.ctx:
                r.ctx.resume()
                r.ctx.kill()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _next(self) -> Job | None:
        return next((j for j in self.jobs if j.status == Status.QUEUED and j.kind != PREVIEW), None)

    def _launch(self, job: Job) -> _Run:
        r = _Run(job)
        r.units = pipeline.work_units(job)
        heavy = job.kind not in LIGHT
        if heavy:
            others = [x for x in self._runs.values() if x.alive and x.job.kind not in LIGHT]
            if others:
                r.solo = False
                for x in others:
                    x.solo = False
        job.status = Status.RUNNING
        r.thread = threading.Thread(target=self._run_job, args=(job, r, False), daemon=True,
                                    name=f"ralenti-job-{job.id}")
        self._runs[job.id] = r
        r.thread.start()
        return r

    def _loop(self) -> None:
        tick = 0
        try:
            while True:
                with self._lock:
                    self._runs = {k: r for k, r in self._runs.items() if r.alive}
                    active = list(self._runs.values())
                    if self._running.is_set() and not self._stop_after and not self._paused_all:
                        heavy, target = sum(1 for r in active if r.job.kind not in LIGHT), self.target()
                        while (nxt := self._next()) is not None:
                            if nxt.kind not in LIGHT and heavy >= target:
                                break
                            active.append(self._launch(nxt))
                            if nxt.kind not in LIGHT:
                                heavy += 1
                    waiting = self._next() is not None
                if not active and (not self._running.is_set() or self._stop_after or self._paused_all
                                   or not waiting):
                    break
                tick += 1
                if tick % 8 == 0:
                    self._measure()
                time.sleep(0.5)
        except Exception:
            traceback.print_exc()
        finally:
            self._running.clear()
            self._stop_after = False
            self._emit("idle")

    def _measure(self) -> None:
        """Throughput (work units per second) and system load, fed to the auto scaler."""
        with self._lock:
            heavy = [r for r in self._runs.values() if r.alive and r.job.kind not in LIGHT]
            done, self._done_units = self._done_units, 0.0
        now = time.monotonic()
        dt, self._m_last = max(0.5, now - self._m_last), now
        for r in heavy:
            if not r.units:
                r.units = pipeline.work_units(r.job)
            cur = r.units * r.job.progress
            done += max(0.0, cur - r.last)
            r.last = cur
        util = vram = ram = None
        try:
            from .gpu import stats
            st = stats()
            if st:
                try:
                    idx = max(0, int(str(self.settings.gpu_device).split(".")[0]))
                except ValueError:
                    idx = 0
                g = next((x for x in st if x.index == idx), st[0])
                util, vram = g.util, (g.mem_used / g.mem_total if g.mem_total else None)
        except Exception:
            pass
        try:
            import psutil
            ram = psutil.virtual_memory().percent / 100
        except Exception:
            pass
        if len(heavy) == 1 and util is not None and time.time() - heavy[0].job.started > 20:
            k = "util:" + rate_key(heavy[0].job)
            acc = self._util_acc.setdefault(k, [])
            acc.append(util)
            if len(acc) >= 5:
                self.rates[k] = sum(acc[-30:]) / len(acc[-30:])
        if str(getattr(self.settings, "concurrency", "auto")) == "auto" and heavy \
                and not any(r.pause.is_set() for r in heavy):
            nxt = self._next()
            from .estimate import bench_util
            single = (self.rates.get("util:" + rate_key(nxt)) or bench_util(nxt.spec.get("model"))) if nxt else None
            self.scaler.feed(done / dt, util, vram, ram, nxt is not None, single)

    # ---------- preview lane ----------
    def start_preview(self) -> None:
        with self._lock:
            if self._p_alive:
                return
            self._p_alive = True
        threading.Thread(target=self._preview_loop, daemon=True, name="ralenti-preview").start()

    def _preview_loop(self) -> None:
        try:
            while True:
                with self._lock:
                    job = next((j for j in self.jobs if j.status == Status.QUEUED and j.kind == PREVIEW), None)
                    if job is None:
                        self._p_alive = False
                        break
                    r = _Run(job)
                    r.thread = threading.current_thread()
                    job.status = Status.RUNNING
                    self._prun = r
                self._run_job(job, r, True)
        except Exception:
            traceback.print_exc()
            with self._lock:
                self._p_alive = False
        finally:
            self._prun = None
            self._emit("preview_idle")

    # ---------- one job ----------
    def _run_job(self, job: Job, r: _Run, preview: bool) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / f"{job.id}.log"
        log_path.touch()
        fresh = not any(s.status == Status.DONE for s in job.steps)
        r.p0 = job.overall()
        job.started, job.error = time.time(), ""
        self.save()
        self._emit("progress", job)
        step = None
        try:
            for i, step in enumerate(job.steps):
                if step.status == Status.DONE:
                    continue
                job.current = i
                step.status, step.progress = Status.RUNNING, 0.0
                job.fps = job.speed = job.eta = ""
                if not r.pause.is_set():
                    job.status = Status.RUNNING
                self._emit("step", job)
                r.ctx = StepContext(job, step, log_path, self._notify, r.cancel, r.pause, self.save)
                pipeline.execute(step, job, self.tc, r.ctx)
                step.status, step.progress = Status.DONE, 1.0
                job.progress = job.overall()
                self._emit("progress", job)
                self.save()
            job.status = Status.DONE
        except Cancelled:
            if step:
                step.status, step.progress = Status.QUEUED, 0.0
            job.status = Status.PAUSED if r.hold else Status.CANCELLED
        except Exception as e:
            if isinstance(e, DiskFull) and not preview:
                self._stop_after = True
                self.user_stopped = True
            if step:
                step.status, step.error = Status.FAILED, str(e)
            job.status, job.error = Status.FAILED, str(e)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n[ralenti] " + traceback.format_exc())
        finally:
            r.ctx = None
            job.finished = time.time()
            job.progress, job.eta = job.overall(), ""
            with self._lock:
                self._done_units += max(0.0, r.units * job.progress - r.last)
            if job.status == Status.DONE:
                job.detail = f"{job.detail} · {fmt_eta(job.finished - job.started)} total".strip(" ·")
                if fresh and not preview and r.solo:
                    self._learn(job)
            self._save_rates()
            self.save()
            self._emit("finished", job)
