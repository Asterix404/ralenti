"""StepContext: what a step handler uses to run ffmpeg, report progress and log."""
from __future__ import annotations

import subprocess
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


class StepContext:
    def __init__(self, job: Job, step: Step, log_path: Path,
                 notify: Callable[[Job], None], cancel: threading.Event):
        self.job, self.step, self.log_path = job, step, log_path
        self._notify, self._cancel = notify, cancel
        self._proc: subprocess.Popen | None = None
        self._t0 = time.time()
        self._disk_msg = ""
        self._last_disk = 0.0

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def progress(self, frac: float, detail: str | None = None) -> None:
        frac = max(0.0, min(1.0, frac))
        self.step.progress = frac
        if detail is not None:
            self.job.detail = detail
        elapsed = time.time() - self._t0
        self.job.eta = fmt_eta(elapsed * (1 - frac) / frac) if frac > 0.01 else ""
        self.job.progress = self.job.overall()
        self._notify(self.job)

    def log(self, text: str) -> None:
        with open(self.log_path, "a", encoding="utf-8", errors="replace") as f:
            f.write(text + "\n")

    def run_ffmpeg(self, cmd: list[str], total: float, env: dict | None = None,
                       span: tuple[float, float] = (0.0, 1.0), frames: int = 0) -> str:
        """Blocking. Maps out_time/total into [span] of this step's progress.
        Returns this run's stderr (read back from the job log)."""
        cmd = adapt_cmd(cmd)
        self.check()
        self.log(f"\n=== {time.strftime('%H:%M:%S')} {self.step.label}\n"
                 f"{subprocess.list2cmdline(cmd)}\n")
        offset = self.log_path.stat().st_size
        parser, (lo, hi) = ProgressParser(), span

        with open(self.log_path, "ab") as errf:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=errf, stdin=subprocess.DEVNULL,
                env=env, creationflags=NO_WINDOW, text=True, encoding="utf-8",
                errors="replace", bufsize=1)
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

    LOW_SPACE = 1.5e9

    def _space_low(self) -> bool:
        now = time.monotonic()
        if now - self._last_disk < 4:
            return False
        self._last_disk = now
        import shutil
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

    def track(self, p) -> None:
        """Extra processes (e.g. RIFE pipeline) that cancel must also kill."""
        self._tracked = [x for x in getattr(self, "_tracked", []) if x.poll() is None] + [p]

    def kill(self) -> None:
        for p in [self._proc, *getattr(self, "_tracked", [])]:
            if p and p.poll() is None:
                p.kill()