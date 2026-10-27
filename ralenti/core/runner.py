"""StepContext: what a step handler uses to run FFmpeg and engines, report progress, log, and honour
cancel and pause. Pausing suspends the processes: nothing is killed and no work is lost."""
from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .jobs import Job, Step
from .progress import ProgressParser, fmt_eta
from .tools import NO_WINDOW, adapt_cmd


class Cancelled(Exception):
    pass


class FFmpegError(RuntimeError):
    pass


class DiskFull(RuntimeError):
    pass


def _nt(pid: int, fn: str) -> None:
    """NtSuspendProcess / NtResumeProcess: freezes or thaws every thread of a process."""
    if sys.platform != "win32":
        return
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x0800, False, pid)          # PROCESS_SUSPEND_RESUME
    if h:
        try:
            getattr(ctypes.windll.ntdll, fn)(h)
        finally:
            k.CloseHandle(h)


class StepContext:
    LOW_SPACE = 1.5e9

    def __init__(self, job: Job, step: Step, log_path: Path, notify: Callable[[Job], None],
                 cancel: threading.Event, pause: threading.Event | None = None, save: Callable | None = None):
        self.job, self.step, self.log_path = job, step, log_path
        self._notify, self._cancel = notify, cancel
        self._pause = pause or threading.Event()
        self._save = save
        self._proc: subprocess.Popen | None = None
        self._tracked: list = []
        self._suspended = False
        self._t0 = time.time()
        self._paused_total, self._pause_t = 0.0, None
        self._disk_msg, self._last_disk = "", 0.0

    # ---------- pause ----------
    @property
    def paused_seconds(self) -> float:
        return self._paused_total + (time.time() - self._pause_t if self._pause_t else 0.0)

    def _procs(self) -> list:
        return [p for p in [self._proc, *self._tracked] if p is not None and p.poll() is None]

    def suspend(self) -> None:
        if self._suspended:
            return
        self._suspended, self._pause_t = True, time.time()
        for p in self._procs():
            _nt(p.pid, "NtSuspendProcess")

    def resume(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        if self._pause_t:
            self._paused_total += time.time() - self._pause_t
            self._pause_t = None
        for p in self._procs():
            _nt(p.pid, "NtResumeProcess")

    def _adopt(self, p) -> None:
        """A process started while the job is paused is frozen right away."""
        if self._pause.is_set() and not self._cancel.is_set():
            if not self._suspended:
                self._suspended, self._pause_t = True, time.time()
            _nt(p.pid, "NtSuspendProcess")

    def track(self, p) -> None:
        """Extra processes (e.g. an engine pipeline) that pause and cancel must also reach."""
        self._tracked = [x for x in self._tracked if x.poll() is None] + [p]
        self._adopt(p)

    def check(self) -> None:
        while self._pause.is_set() and not self._cancel.is_set():
            if not self._suspended:
                self.suspend()
            time.sleep(0.2)
        if self._suspended and not self._pause.is_set():
            self.resume()
        if self._cancel.is_set():
            raise Cancelled()

    def checkpoint(self) -> None:
        """Persist the job now (e.g. after a finished segment)."""
        if self._save:
            self._save()

    # ---------- progress / log ----------
    def progress(self, frac: float, detail: str | None = None) -> None:
        frac = max(0.0, min(1.0, frac))
        self.step.progress = frac
        if detail is not None:
            self.job.detail = detail
        elapsed = time.time() - self._t0 - self.paused_seconds
        self.job.eta = fmt_eta(elapsed * (1 - frac) / frac) if frac > 0.01 else ""
        self.job.progress = self.job.overall()
        self._notify(self.job)

    def log(self, text: str) -> None:
        with open(self.log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(text + "\n")

    # ---------- running ----------
    def run_ffmpeg(self, cmd: list[str], total: float, env: dict | None = None,
                   span: tuple[float, float] = (0.0, 1.0), frames: int = 0) -> str:
        """Blocking. Maps progress into [span] of this step. Returns this run's stderr."""
        cmd = adapt_cmd(cmd)
        self.check()
        self.log(f"\n=== {time.strftime('%H:%M:%S')} {self.step.label}\n{subprocess.list2cmdline(cmd)}\n")
        offset = self.log_path.stat().st_size
        parser, (lo, hi) = ProgressParser(), span
        with open(self.log_path, "ab") as errf:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL,
                                          env=env, creationflags=NO_WINDOW, text=True, encoding="utf-8",
                                          errors="replace", bufsize=1)
            self._adopt(self._proc)
            for line in self._proc.stdout:
                if self._space_low():
                    self.kill()
                    break
                snap = parser.feed(line)
                if not snap:
                    continue
                self.job.fps = f"{snap.fps:.1f}" if snap.fps else ""
                self.job.speed = f"{snap.speed:.2f}x" if snap.speed else ""
                if frames and snap.frame:
                    frac = snap.frame / frames
                elif total > 0 and snap.out_time > 0:
                    frac = snap.out_time / total
                else:
                    continue
                self.progress(lo + (hi - lo) * min(1.0, frac))
            code = self._proc.wait()
        self._proc = None
        with open(self.log_path, "rb") as f:
            f.seek(offset)
            err = f.read().decode("utf-8", "replace")
        if self._disk_msg:
            raise DiskFull(self._disk_msg)
        if self._cancel.is_set():
            raise Cancelled()
        if code != 0:
            tail = "\n".join(err.strip().splitlines()[-15:])
            raise FFmpegError(f"ffmpeg exited with code {code}\n{tail}")
        return err

    def _space_low(self) -> bool:
        now = time.monotonic()
        if now - self._last_disk < 4:
            return False
        self._last_disk = now
        dirs = {Path(self.job.output).parent}
        if tmp := self.job.plan.get("tmp"):
            dirs.add(Path(tmp))
        for d in dirs:
            probe = d if d.exists() else d.parent
            try:
                free = shutil.disk_usage(probe).free
            except OSError:
                continue
            if free < self.LOW_SPACE:
                self._disk_msg = (f"Disk almost full on {probe.anchor or probe} ({free / 1e9:.1f} GB left). "
                                  "Stopped to protect your system. Free up space, then resume the job.")
                return True
        return False

    def kill(self) -> None:
        for p in self._procs():
            try:
                p.kill()
            except OSError:
                pass
