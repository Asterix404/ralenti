"""QueueManager: a main worker (one job at a time) plus a preview lane that runs short
preview renders alongside it. Persistence, resume, events and ETA.
Events: added, removed, started, step, progress, finished, idle, preview_idle."""
from __future__ import annotations

import json
import os
import shutil
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


def rate_key(job: Job) -> str:
    s, p = job.spec, job.plan
    if job.kind == "interpolate":
        return f"interpolate:{s.get('model')}:{'vmaf' if p.get('vmaf') else 'direct'}"
    if job.kind == "encode":
        return f"encode:{s.get('encoder')}:{'vmaf' if p.get('vmaf') else 'fixed'}"
    if job.kind == "merge":
        return f"merge:{'reencode' if p.get('reencode') else 'copy'}"
    return job.kind


class QueueManager:
    def __init__(self, tc: Toolchain, settings: Settings, path: Path = QUEUE_FILE):
        self.tc, self.settings, self.path = tc, settings, Path(path)
        self.jobs: list[Job] = []
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self._cancel = threading.Event()
        self._pcancel = threading.Event()
        self._ctx: StepContext | None = None
        self._pctx: StepContext | None = None
        self._p_alive = False
        self._last_emit: dict[str, float] = {}
        self._cur_p0 = 0.0
        self.run_started_at = 0.0
        self.user_stopped = False
        self.rates: dict[str, float] = self._load_rates()

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
                if job.status == Status.RUNNING:
                    job.reset(full=False)
                self.jobs.append(job)
        shutil.rmtree(self.previews_dir(), ignore_errors=True)   # previews never survive a restart

    def save(self) -> None:
        with self._lock:
            data = [j.to_dict() for j in self.jobs if j.kind != PREVIEW]
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

    def _learn(self, job: Job) -> None:
        units = pipeline.work_units(job)
        secs = job.finished - job.started
        if units < 0.5 or secs < 1:
            return
        k, r = rate_key(job), secs / units
        old = self.rates.get(k)
        self.rates[k] = r if old is None else old * 0.5 + r * 0.5
        try:
            RATES_FILE.write_text(json.dumps(self.rates, indent=1), encoding="utf-8")
        except OSError:
            pass

    # ---------- editing ----------
    def get(self, job_id: str | None) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

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
            if not job or job.status == Status.RUNNING:
                return False
            self.jobs.remove(job)
        pipeline.cleanup_temp(job, remove_part=True)
        if job.kind == PREVIEW:
            try:
                Path(job.output).unlink(missing_ok=True)
            except OSError:
                pass   # still open in the player; the restart cleanup removes it
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
        if not job or job.status == Status.RUNNING:
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
        if job.status == Status.QUEUED:
            job.status = Status.CANCELLED
            self.save()
            self._emit("progress", job)
        elif job.status == Status.RUNNING:
            if job.kind == PREVIEW:
                self._pcancel.set()
                if ctx := self._pctx:
                    ctx.kill()
            else:
                self.stop()

    def clear_finished(self) -> None:
        for j in [j for j in self.jobs if j.status == Status.DONE]:
            self.remove(j.id)

    # ---------- overview (main queue only) ----------
    def _main_jobs(self) -> list[Job]:
        with self._lock:
            return [j for j in self.jobs if j.kind != PREVIEW]

    def run_jobs(self) -> list[Job]:
        t = self.run_started_at
        return [j for j in self._main_jobs() if j.status in (Status.QUEUED, Status.RUNNING)
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
        cur = next((j for j in jobs if j.status == Status.RUNNING), None)
        queued = [j for j in jobs if j.status == Status.QUEUED]
        if not cur and not queued:
            return None, True
        total, complete, live = 0.0, True, {}
        if cur:
            elapsed, dp = time.time() - cur.started, cur.progress - self._cur_p0
            if dp > 0.02 and elapsed > 3:
                per_full = elapsed / dp
                total += per_full * (1 - cur.progress)
                if (u := pipeline.work_units(cur)) > 0:
                    live[rate_key(cur)] = per_full / u
            else:
                complete = False
        for j in queued:
            r = live.get(rate_key(j)) or self.rates.get(rate_key(j))
            if r:
                total += r * pipeline.work_units(j) * (1 - j.progress)
            elif j.kind == "cut" or (j.kind == "merge" and not j.plan.get("reencode")):
                total += 2
            else:
                complete = False
        return (total if total > 0 else None), complete

    # ---------- running ----------
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def preview_running(self) -> bool:
        return self._p_alive

    def start(self) -> None:
        if self.is_running:
            return
        self.run_started_at = time.time()
        self.user_stopped = False
        self._running.set()
        self._cancel.clear()
        self._thread = threading.Thread(target=self._worker, args=(False,), daemon=True, name="ralenti-queue")
        self._thread.start()
        self._emit("started")

    def start_preview(self) -> None:
        with self._lock:
            if self._p_alive:
                return
            self._p_alive = True
        threading.Thread(target=self._worker, args=(True,), daemon=True, name="ralenti-preview").start()

    def stop(self, kill_current: bool = True) -> None:
        """Stops the main queue (previews keep running)."""
        self.user_stopped = True
        self._running.clear()
        if kill_current:
            self._cancel.set()
            if ctx := self._ctx:
                ctx.kill()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _next(self, preview: bool) -> Job | None:
        return next((j for j in self.jobs if j.status == Status.QUEUED and (j.kind == PREVIEW) == preview), None)

    def _worker(self, preview: bool) -> None:
        try:
            while preview or self._running.is_set():
                with self._lock:
                    job = self._next(preview)
                    if job is None:
                        if preview:
                            self._p_alive = False
                        break
                (self._pcancel if preview else self._cancel).clear()
                self._run_job(job, preview)
        except Exception:
            traceback.print_exc()
            if preview:
                with self._lock:
                    self._p_alive = False
        finally:
            if preview:
                self._pctx = None
                self._emit("preview_idle")
            else:
                self._running.clear()
                self._ctx = None
                self._emit("idle")

    def _run_job(self, job: Job, preview: bool) -> None:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOGS_DIR / f"{job.id}.log"
        log_path.touch()
        fresh = not any(s.status == Status.DONE for s in job.steps)
        if not preview:
            self._cur_p0 = job.overall()
        cancel = self._pcancel if preview else self._cancel
        job.status, job.started, job.error = Status.RUNNING, time.time(), ""
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
                self._emit("step", job)
                ctx = StepContext(job, step, log_path, self._notify, cancel)
                if preview:
                    self._pctx = ctx
                else:
                    self._ctx = ctx
                pipeline.execute(step, job, self.tc, ctx)
                step.status, step.progress = Status.DONE, 1.0
                job.progress = job.overall()
                self._emit("progress", job)
                self.save()
            job.status = Status.DONE
        except Cancelled:
            if step:
                step.status, step.progress = Status.QUEUED, 0.0
            job.status = Status.CANCELLED
        except Exception as e:
            if isinstance(e, DiskFull) and not preview:
                self._running.clear()
                self.user_stopped = True
            if step:
                step.status, step.error = Status.FAILED, str(e)
            job.status, job.error = Status.FAILED, str(e)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n[ralenti] " + traceback.format_exc())
        finally:
            if preview:
                self._pctx = None
            else:
                self._ctx = None
            job.finished = time.time()
            job.progress, job.eta = job.overall(), ""
            if job.status == Status.DONE:
                job.detail = f"{job.detail} · {fmt_eta(job.finished - job.started)} total".strip(" ·")
                if fresh and not preview:
                    self._learn(job)
            self.save()
            self._emit("finished", job)