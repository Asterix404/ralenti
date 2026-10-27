"""Job = spec (user options) + plan (derived paths/flags) + state (results) + steps."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from enum import Enum


class Status(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


@dataclass
class Step:
    kind: str
    label: str
    weight: float = 1.0
    status: Status = Status.QUEUED
    progress: float = 0.0
    error: str = ""


@dataclass
class Job:
    kind: str                     # interpolate | encode | cut | merge
    label: str
    output: str
    spec: dict
    plan: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: Status = Status.QUEUED
    current: int = -1
    progress: float = 0.0
    detail: str = ""
    fps: str = ""
    speed: str = ""
    eta: str = ""
    error: str = ""
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0

    @property
    def step(self) -> Step | None:
        return self.steps[self.current] if 0 <= self.current < len(self.steps) else None

    def overall(self) -> float:
        total = sum(s.weight for s in self.steps) or 1.0
        done = sum(s.weight * (1.0 if s.status == Status.DONE else s.progress)
                   for s in self.steps)
        return min(1.0, done / total)

    def reset(self, full: bool = False) -> None:
        """full=False resumes from the first unfinished step."""
        for s in self.steps:
            if full or s.status != Status.DONE:
                s.status, s.progress, s.error = Status.QUEUED, 0.0, ""
        if full:
            self.state = {}
        self.status, self.current, self.error = Status.QUEUED, -1, ""
        self.detail = self.fps = self.speed = self.eta = ""
        self.progress = self.overall()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        d = dict(d)
        steps = [Step(**{**s, "status": Status(s["status"])}) for s in d.pop("steps", [])]
        known = {f.name for f in fields(cls)}
        job = cls(**{k: v for k, v in d.items() if k in known})
        job.status = Status(job.status)
        job.steps = steps
        return job