"""Run one job through the real queue engine, no UI.
  python scripts/run_headless.py --models
  python scripts/run_headless.py clip.mp4
  python scripts/run_headless.py clip.mp4 --slowmo 4 --vmaf 0
  python scripts/run_headless.py clip.mp4 --preview --start 2 --length 3
  python scripts/run_headless.py clip.mp4 --encode-only
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ralenti.core import engines, pipeline  # noqa: E402
from ralenti.core.config import APP_DIR, LOGS_DIR, Settings, ensure_dirs  # noqa: E402
from ralenti.core.queue import QueueManager  # noqa: E402
from ralenti.core.tools import Toolchain  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("src", nargs="?")
ap.add_argument("--models", action="store_true", help="list installed models and exit")
ap.add_argument("--model", default="auto")
ap.add_argument("--mult", type=float, help="fps multiplier, e.g. 2")
ap.add_argument("--fixed", type=float, help="fixed output fps, e.g. 60")
ap.add_argument("--slowmo", type=float, default=2.0)
ap.add_argument("--audio", default="natural", choices=["copy", "drop", "keep_pitch", "natural"])
ap.add_argument("--encoder", default="av1_nvenc")
ap.add_argument("--vmaf", type=float, default=95.0, help="0 = constant quality")
ap.add_argument("--crop", default="auto", choices=["auto", "none"])
ap.add_argument("--scale", type=float, default=1.0)
ap.add_argument("--start", type=float)
ap.add_argument("--end", type=float)
ap.add_argument("--length", type=float, default=3.0)
ap.add_argument("--preview", action="store_true")
ap.add_argument("--encode-only", action="store_true")
a = ap.parse_args()

ensure_dirs()
settings = Settings.load()
tc = Toolchain.resolve(settings)
print(tc.report(), "\n")
if a.models or not a.src:
    for eng, m in engines.all_models(tc):
        print(f"  {eng.name:<12} {m.code:<14} {m.label}")
    sys.exit(0)

mode, val = ("fixed", a.fixed) if a.fixed else (("multiply", a.mult) if a.mult else ("keep", 0.0))
opts = dict(model=a.model, fps_mode=mode, fps_value=val, slowmo=a.slowmo, audio=a.audio, encoder=a.encoder,
            vmaf=a.vmaf or None, crop=a.crop, scale=a.scale, start=a.start, end=a.end,
            device=settings.gpu_device or "0")
if a.encode_only:
    job = pipeline.make_encode_job(tc, settings, a.src, encoder=a.encoder, vmaf=a.vmaf or None)
elif a.preview:
    job = pipeline.make_preview_job(tc, settings, a.src, a.start or 0.0, a.length,
                                    **{k: v for k, v in opts.items() if k not in ('start', 'end')})
else:
    job = pipeline.make_interpolate_job(tc, settings, a.src, **opts)
print("Steps :", " → ".join(s.label for s in job.steps))
print("Model :", job.spec.get("model", "-"))
print("Output:", job.output)

q = QueueManager(tc, settings, path=APP_DIR / "queue_headless.json")


def on_event(ev, j):
    if j is None or j.id != job.id:
        return
    if ev == "step":
        print(f"\n→ {j.step.label}")
    elif ev == "progress" and j.step:
        print(f"\r  [{j.progress * 100:5.1f}%] {j.detail[:55]:<55} {j.fps:>6} fps", end="", flush=True)
    elif ev == "finished":
        print(f"\n\n{j.status.value.upper()}  {j.output}")
        if j.error:
            print(j.error)


q.subscribe(on_event)
q.add(job)
if job.kind != "preview":
    q.start()
try:
    time.sleep(0.5)
    while q.is_running or q.preview_running:
        time.sleep(0.2)
except KeyboardInterrupt:
    q.cancel_job(job.id)
    q.wait(10)
print("Log   :", LOGS_DIR / f"{job.id}.log")
