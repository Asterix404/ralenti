"""Measures how fast this PC runs each installed AI model, each encoder and VMAF scoring, on a generated
1080p test clip. Results feed the time estimates and the automatic concurrency."""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from .config import LOGS_DIR, TEMP_DIR
from .encoders import ENCODERS
from .engines import Render, available_engines
from .estimate import save_bench
from .jobs import Job, Step
from .probe import probe
from .runner import Cancelled, StepContext

W, H, SECS = 1920, 1080, 3


def _sampler(stop, out, idx):
    from .gpu import stats
    while not stop.wait(0.5):
        try:
            st = stats()
            if st:
                out.append(next((x for x in st if x.index == idx), st[0]).util)
        except Exception:
            pass


def run(tc, settings, progress, cancel) -> dict:
    from . import vmaf
    from .commands import build_encode
    from .gpu import nvidia_gpus
    from .pipeline import _preview_encoder
    if not tc.ffmpeg:
        raise RuntimeError("FFmpeg is required. Get it from Downloads.")
    work = Path(settings.temp_dir or TEMP_DIR) / "benchmark"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log = LOGS_DIR / "benchmark.log"
    log.write_text("", encoding="utf-8")
    job = Job("benchmark", "Benchmark", str(work / "out.mkv"), {})
    job.plan = {"tmp": str(work)}
    ctl = StepContext(job, Step("bench", "Benchmark"), log, lambda _j: None, cancel)
    try:
        idx = max(0, int(str(settings.gpu_device).split(".")[0]))
    except ValueError:
        idx = 0
    try:
        progress(0.0, "Creating a test clip…")
        clip = work / "clip.mp4"
        ctl.run_ffmpeg([tc.ffmpeg, "-hide_banner", "-nostdin", "-y", "-nostats", "-progress", "pipe:1",
                        "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30000/1001:duration={SECS}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", str(clip)], SECS)
        info = probe(tc.ffprobe, str(clip))
        frames = round(SECS * float(info.fps))
        models = []
        for eng in available_engines(tc):
            ms = [m for m in eng.models(tc) if m.installed]
            models += [(eng, m) for m in (ms if eng.key == "rife" else ms[:3])]
        encs = [e for e in ENCODERS.values() if not e.hidden and e.has_quality and tc.binary_for(e.codec)]
        total = len(models) + len(encs) + (1 if tc.has_vmaf else 0)
        g = next((x for x in nvidia_gpus() if x.index == idx), None)
        res = {"date": time.strftime("%Y-%m-%d %H:%M"), "gpu": g.short if g else "", "models": {},
               "encoders": {}, "vmaf": None}
        pv, n = _preview_encoder(tc), 0
        for eng, m in models:
            progress(n / total, f"Testing {m.label}…"); n += 1
            r = Render(info=info, model=m.code, start=None, dur=SECS, crop=None, size=None, target=info.fps * 2,
                       slowmo=1.0, device=str(settings.gpu_device or "0"), opts=eng.defaults(), tmp=work / "w")
            stop, utils = threading.Event(), []
            threading.Thread(target=_sampler, args=(stop, utils, idx), daemon=True).start()
            t0 = time.time()
            try:
                eng.render(r, tc, ctl, pv, str(work / f"m{n}.mkv"))
                secs = max(0.01, time.time() - t0)
                u = utils[2:] or utils
                res["models"][m.code] = {"label": m.label, "engine": eng.name, "mpfs": W * H * frames * 2 / 1e6 / secs,
                                         "util": round(sum(u) / len(u)) if u else None}
            except Cancelled:
                raise
            except Exception as e:
                res["models"][m.code] = {"label": m.label, "engine": eng.name,
                                         "error": (str(e).strip().splitlines() or ["failed"])[0][:200]}
            finally:
                stop.set()
        for e in encs:
            progress(n / total, f"Testing {e.label}…"); n += 1
            t0 = time.time()
            try:
                ctl.run_ffmpeg(build_encode(tc, str(clip), info, e, str(work / f"e_{e.key}{e.ext}")), SECS)
                res["encoders"][e.key] = W * H * frames / 1e6 / max(0.01, time.time() - t0)
            except Cancelled:
                raise
            except Exception:
                pass
        if tc.has_vmaf:
            progress(n / total, "Testing VMAF scoring…")
            t0 = time.time()
            ctl.run_ffmpeg(vmaf.build_score(tc.ffmpeg, str(clip), str(clip), 0, SECS, vmaf.model_for(W, H),
                                            fps=info.fps), SECS)
            res["vmaf"] = W * H * frames / 1e6 / max(0.01, time.time() - t0)
        progress(1.0, "Done")
        save_bench(res)
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)
