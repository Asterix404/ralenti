"""Locates FFmpeg (full build) and RIFE, detects FFmpeg capabilities, adapts encoder options."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_ENTRY = re.compile(r"^\s*[A-Z.|]{2,6}\s+([\w-]+)\s")


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[2]


def run(args, env=None, timeout=120) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout, creationflags=NO_WINDOW)


@dataclass(frozen=True)
class Caps:
    filters: frozenset = frozenset()
    encoders: frozenset = frozenset()


@lru_cache(maxsize=8)
def caps(ffmpeg: str) -> Caps:
    if not ffmpeg:
        return Caps()
    found = {}
    for flag in ("-filters", "-encoders"):
        names = set()
        try:
            for line in run([ffmpeg, "-hide_banner", flag], timeout=20).stdout.splitlines():
                m = _ENTRY.match(line)
                if m:
                    names.add(m.group(1))
        except (OSError, subprocess.TimeoutExpired):
            pass
        found[flag] = frozenset(names)
    return Caps(found["-filters"], found["-encoders"])


def _find_full() -> str:
    from .config import TOOLS_DIR
    cands = [TOOLS_DIR / "ffmpeg" / "ffmpeg.exe", app_root() / "tools" / "ffmpeg" / "ffmpeg.exe"]
    if w := shutil.which("ffmpeg"):
        cands.append(Path(w))
    existing = [str(c) for c in cands if c.is_file()]
    for c in existing:
        f = caps(c).filters
        if "libvmaf" in f and "cropdetect" in f:
            return c
    return existing[0] if existing else ""


def _sibling_probe(ffmpeg: str) -> str:
    if ffmpeg and (p := Path(ffmpeg).with_name("ffprobe.exe")).is_file():
        return str(p)
    return ""


def _find_rife() -> str:
    from .config import TOOLS_DIR
    for d in (TOOLS_DIR / "rife", app_root() / "tools" / "rife"):
        if d.is_dir():
            hit = next(d.rglob("rife-ncnn-vulkan.exe"), None)
            if hit:
                return str(hit)
    return ""


@dataclass
class Toolchain:
    ffmpeg: str = ""
    ffprobe: str = ""
    rife: str = ""

    @classmethod
    def resolve(cls, s) -> "Toolchain":
        full = s.full_ffmpeg or _find_full()
        return cls(ffmpeg=full, ffprobe=s.full_ffprobe or _sibling_probe(full),
                   rife=getattr(s, "rife_exe", "") or _find_rife())

    @property
    def any_ffmpeg(self) -> str:
        return self.ffmpeg

    @property
    def has_vmaf(self) -> bool:
        return "libvmaf" in caps(self.ffmpeg).filters

    @property
    def has_rubberband(self) -> bool:
        return "rubberband" in caps(self.ffmpeg).filters

    @property
    def has_cropdetect(self) -> bool:
        return "cropdetect" in caps(self.ffmpeg).filters

    def binary_for(self, codec: str) -> str:
        return self.ffmpeg if self.ffmpeg and codec in caps(self.ffmpeg).encoders else ""

    @property
    def has_engine(self) -> bool:
        from .engines import available_engines
        return bool(available_engines(self))

    def problems(self) -> list[str]:
        """Only real blockers: FFmpeg is required; an AI engine is needed to interpolate."""
        p = []
        if not self.ffmpeg:
            p.append("FFmpeg (full build) is missing. It's required: get it from Downloads.")
        elif not self.ffprobe:
            p.append("ffprobe is missing next to ffmpeg.exe.")
        if not self.has_engine:
            p.append("No AI engine yet: install RIFE (free) from Downloads to interpolate.")
        return p

    def report(self) -> str:
        from .engines import engines
        yes = lambda b: "yes" if b else "NO"
        lines = [f"FFmpeg       : {self.ffmpeg or '-'}", f"ffprobe      : {self.ffprobe or '-'}",
                 f"VMAF {yes(self.has_vmaf)} | Rubber Band {yes(self.has_rubberband)} | "
                 f"crop detection {yes(self.has_cropdetect)}",
                 f"SVT-AV1      : {yes(bool(self.binary_for('libsvtav1')))} | "
                 f"AV1 NVENC {yes(bool(self.binary_for('av1_nvenc')))}"]
        lines += [f"{e.name:<13}: {e.status(self)}" for e in engines()]
        lines += [f"! {x}" for x in self.problems()]
        return "\n".join(lines)


# ---------- encoder option compatibility (options get renamed between FFmpeg versions) ----------
_FIXABLE = {"spatial_aq", "spatial-aq", "temporal_aq", "temporal-aq", "aq-strength", "aq_strength",
            "rc-lookahead", "rc_lookahead", "multipass", "b_ref_mode", "b-ref-mode"}


@lru_cache(maxsize=32)
def encoder_options(ffmpeg: str, codec: str) -> frozenset:
    try:
        out = run([ffmpeg, "-hide_banner", "-h", f"encoder={codec}"], timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    return frozenset(re.findall(r"^\s+-([\w-]+)\s", out, re.M))


def adapt_cmd(cmd: list[str]) -> list[str]:
    """Renames (spatial_aq <-> spatial-aq) or drops encoder options this FFmpeg doesn't support."""
    if not cmd or "-c:v" not in cmd:
        return cmd
    i = len(cmd) - 1 - cmd[::-1].index("-c:v")
    if i + 1 >= len(cmd):
        return cmd
    opts = encoder_options(cmd[0], cmd[i + 1])
    if not opts:
        return cmd
    out, j = [], 0
    while j < len(cmd):
        a = cmd[j]
        name = a[1:] if a.startswith("-") else ""
        if name in _FIXABLE and name not in opts and j + 1 < len(cmd):
            alt = name.replace("_", "-") if "_" in name else name.replace("-", "_")
            if alt in opts:
                out += ["-" + alt, cmd[j + 1]]
            j += 2
            continue
        out.append(a)
        j += 1
    return out
