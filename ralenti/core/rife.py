"""RIFE engine (rife-ncnn-vulkan), built into Ralenti. FFmpeg decodes frames, RIFE interpolates
them in chunks (bounded temp space) and the results stream straight into the encoder."""
from __future__ import annotations

import math
import re
import shutil
import struct
import subprocess
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .engines import Engine, Model, Option
from .tools import NO_WINDOW, adapt_cmd

PREFIX = "rife-"


@dataclass(frozen=True)
class RifeModel:
    code: str
    label: str
    version: tuple
    path: str


def is_rife(model: str | None) -> bool:
    return bool(model) and str(model).startswith(PREFIX)


def scan_models(exe: str) -> list[RifeModel]:
    """v4 models only: they support any frame count, which Ralenti needs."""
    root = Path(exe).parent if exe else None
    if not root or not root.is_dir():
        return []
    out = []
    for d in root.iterdir():
        m = re.fullmatch(r"rife-v(4(?:\.\d+)*)(.*)", d.name)
        if d.is_dir() and m and any(d.glob("*.param")):
            suffix = m[2].strip("-_ ")
            out.append(RifeModel(d.name, f"RIFE {m[1]}" + (f" {suffix}" if suffix else ""),
                                 tuple(int(x) for x in m[1].split(".")), str(d)))
    return sorted(out, key=lambda r: r.version, reverse=True)


def plan(src_fps: Fraction, target: Fraction, slowmo: float) -> tuple[int, Fraction]:
    """(frames RIFE makes per source frame, encoder input frame rate)."""
    slow = Fraction(slowmo).limit_denominator(100)
    k = target * slow / src_fps
    if k.denominator == 1:
        m = int(k)
    elif k.denominator <= 4 and k.numerator <= 16:
        m = k.numerator          # e.g. 2.5x: make 5x, the encoder keeps every 2nd frame (exact)
    else:
        m = math.ceil(k)         # odd ratios: nearest frames
    m = max(1, m)
    return m, src_fps * m / slow


def chunk_frames(w: int, h: int, m: int) -> int:
    per_frame = w * h * 3 * (1 + 0.5 * m)
    return max(16, min(400, int(1.5e9 / per_frame)))


def write_bmp(path: Path, w: int, h: int, data: bytes) -> None:
    """24-bit bottom-up BMP from top-down BGR24 rows."""
    row = w * 3
    pad = b"\0" * ((-row) % 4)
    body = b"".join(data[(h - 1 - y) * row:(h - y) * row] + pad for y in range(h))
    path.write_bytes(b"BM" + struct.pack("<IHHI", 54 + len(body), 0, 0, 54)
                     + struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(body), 2835, 2835, 0, 0) + body)


def run(ctl, exe: str, model: str, extract_cmd: list[str], encode_cmd: list[str], size: tuple[int, int],
        m: int, est_src: int, work: Path, uhd: bool = False, span=(0.0, 1.0), tta: bool = False,
        cpu: bool = False, limit: int | None = None) -> int:
    """Decode -> RIFE (chunked, one-frame overlap) -> encoder. Returns frames written."""
    from .runner import DiskFull
    encode_cmd = adapt_cmd(encode_cmd)
    w, h = size
    fbytes, n = w * h * 3, chunk_frames(w, h, m)
    din, dout = work / "in", work / "out"
    ctl.log(f"[rife] {Path(model).name} · {m}x frames · {w}x{h} · chunks of {n}"
            f"{' · TTA' if tta else ''}\ndecode: {subprocess.list2cmdline(extract_cmd)}\n"
            f"encode: {subprocess.list2cmdline(encode_cmd)}")
    logf = open(ctl.log_path, "ab")
    rd = en = None
    lo, hi = span
    total, written, t0 = max(1, limit or est_src * m), 0, time.time()
    try:
        rd = subprocess.Popen(extract_cmd, stdout=subprocess.PIPE, stderr=logf, stdin=subprocess.DEVNULL,
                              creationflags=NO_WINDOW)
        en = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=logf,
                              creationflags=NO_WINDOW)
        ctl.track(rd); ctl.track(en)

        def read():
            b = rd.stdout.read(fbytes)
            return b if len(b) == fbytes else None

        prev, pending = None, read()
        if pending is None:
            raise RuntimeError("No frames could be decoded from the source. See the log.")
        while True:
            ctl.check()
            if getattr(ctl, "_space_low", None) and ctl._space_low():
                raise DiskFull(ctl._disk_msg)
            chunk = [prev] if prev is not None else []
            while len(chunk) < n and pending is not None:
                chunk.append(pending)
                pending = read()
            final = pending is None
            single = len(chunk) == 1
            if single:
                chunk = chunk * 2
            for d in (din, dout):
                shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True)
            for i, fr in enumerate(chunk, 1):
                write_bmp(din / f"{i:08d}.bmp", w, h, fr)
            k = len(chunk)
            outs = k * m
            cmd = [exe, "-i", str(din), "-o", str(dout), "-m", model, "-n", str(outs), "-f", "%08d.png"]
            if uhd:
                cmd.append("-u")
            if tta:
                cmd.append("-x")
            if cpu:
                cmd += ["-g", "-1"]
            p = subprocess.Popen(cmd, stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                                 cwd=str(Path(exe).parent), creationflags=NO_WINDOW)
            ctl.track(p)
            while p.poll() is None:
                ctl.check()
                time.sleep(0.05)
            files = sorted(dout.glob("*.png"))
            if p.returncode != 0 or len(files) < outs:
                raise RuntimeError(f"RIFE failed (code {p.returncode}, {len(files)} of {outs} frames). Your GPU "
                                   "may be out of memory: try a lower processing resolution. See the log.")
            keep = m if single else (outs if final else (k - 1) * m)
            if limit is not None:
                keep = max(0, min(keep, limit - written))
                final = final or written + keep >= limit
            for f in files[:keep]:
                try:
                    en.stdin.write(f.read_bytes())
                except OSError:
                    ctl.check()
                    raise RuntimeError("The encoder stopped early. See the log for details.") from None
                written += 1
            el = time.time() - t0
            ctl.job.fps = f"{written / el:.1f}" if el > 0 else ""
            ctl.progress(lo + (hi - lo) * min(1.0, written / total))
            if final:
                break
            prev = chunk[-1]
        en.stdin.close()
        if en.wait() != 0:
            raise RuntimeError("The encoder failed. See the log for details.")
        return written
    finally:
        for proc in (rd, en):
            if proc and proc.poll() is None:
                proc.kill()
        logf.close()
        shutil.rmtree(work, ignore_errors=True)


class RifeEngine(Engine):
    key = "rife"
    name = "RIFE"
    group = "RIFE · free & open source"
    supports_segments = True
    options = (Option("tta", "Quality boost", (("Off", False, "Normal speed"),
                                               ("On", True, "Extra passes per frame: slightly cleaner, much slower")),
                      False, "Runs each frame through extra passes (test-time augmentation) for slightly cleaner "
                             "motion. Several times slower."),)

    def available(self, tc) -> bool:
        return bool(tc.rife)

    def status(self, tc) -> str:
        if not tc.rife:
            return "not installed"
        n = len(scan_models(tc.rife))
        return f"{tc.rife} · {n} model{'s' if n != 1 else ''}"

    def models(self, tc) -> list[Model]:
        found = scan_models(tc.rife)
        base = "Free and open source. Fast on any Vulkan GPU (NVIDIA, AMD, Intel)."
        return [Model(r.code, r.label, base + (" Newest installed version." if i == 0 else " Older version."),
                      "Recommended" if i == 0 else "Free") for i, r in enumerate(found)]

    def owns(self, code: str) -> bool:
        return is_rife(code)

    def temp_bytes(self) -> float:
        return 1.6e9

    def render(self, r, tc, ctl, enc, out, q=None, span=(0.0, 1.0)) -> None:
        from .commands import build_frames_encode, rife_extract_cmd
        if not tc.rife:
            raise RuntimeError("RIFE isn't installed. Get it for free from Downloads.")
        if not tc.ffmpeg:
            raise RuntimeError("RIFE needs the full FFmpeg build. Get it from Downloads.")
        path = next((x.path for x in scan_models(tc.rife) if x.code == r.model), None)
        if not path:
            raise RuntimeError(f"The RIFE model {r.model} isn't installed. Reinstall RIFE from Downloads.")
        src = r.info.fps or Fraction(30)
        m, rate = plan(src, r.target or src, r.slowmo)
        ext, size = rife_extract_cmd(tc, r.info, r.start, r.dur, r.crop, r.size)
        ctl.job.detail = f"RIFE · {m}x frames → {enc.label}"
        run(ctl, tc.rife, path, ext, build_frames_encode(tc, r.info, enc, out, rate, r.target, q), size, m,
            round(r.dur * float(src)), r.tmp / "rife", uhd=size[1] > 1440, span=span,
            tta=bool(r.opts.get("tta")), cpu=r.device == "-1",
            limit=round(r.limit * float(rate / (r.target or src))) if r.limit else None)
