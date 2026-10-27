"""GPU discovery (names, VRAM) and live stats via nvidia-smi, with a Windows fallback."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import lru_cache

from .tools import run


@dataclass(frozen=True)
class GPU:
    index: int
    name: str
    vram_mb: int = 0
    nvidia: bool = False

    @property
    def vram_gb(self) -> int:
        return round(self.vram_mb / 1024)

    @property
    def short(self) -> str:
        return self.name.replace("NVIDIA ", "").replace("GeForce ", "")


@dataclass
class GPUStats:
    index: int
    util: int
    mem_used: int
    mem_total: int
    temp: int
    power: float = 0.0


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _nvsmi(fields: str) -> list[list[str]]:
    try:
        r = run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"], timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    return [[c.strip() for c in line.split(",")] for line in r.stdout.splitlines() if line.strip()]


@lru_cache(maxsize=1)
def nvidia_gpus() -> tuple[GPU, ...]:
    return tuple(GPU(_int(r[0]), r[1], _int(r[2]), True)
                 for r in _nvsmi("index,name,memory.total") if len(r) >= 3)


@lru_cache(maxsize=1)
def windows_adapters() -> tuple[GPU, ...]:
    try:
        r = run(["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_VideoController).Name"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return ()
    names = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    return tuple(GPU(i, n, 0, "NVIDIA" in n.upper()) for i, n in enumerate(names))


def stats() -> list[GPUStats]:
    out = []
    for r in _nvsmi("index,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"):
        if len(r) >= 5:
            try:
                power = float(r[5]) if len(r) > 5 else 0.0
            except ValueError:
                power = 0.0
            out.append(GPUStats(_int(r[0]), _int(r[1]), _int(r[2]), _int(r[3]), _int(r[4]), power))
    return out