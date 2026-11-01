"""Build and run an edit from the command line (the new editor's job compiler, no UI).
Examples:
  python scripts/run_edit.py clip.mp4 --dry
  python scripts/run_edit.py clip.mp4 --no-motion --trim 2 6 --video copy
  python scripts/run_edit.py clip.mp4 --no-motion --rotate 90 --max-h 1080 --vmaf 95
  python scripts/run_edit.py a.mp4 b.mp4 --slowmo 2 --audio convert --acodec opus
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ralenti.core.config import APP_DIR, LOGS_DIR, Settings, ensure_dirs  # noqa: E402
from ralenti.core.edit import Edit, check, compile_job  # noqa: E402
from ralenti.core.queue import QueueManager  # noqa: E402
from ralenti.core.tools import Toolchain  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("clips", nargs="+")
ap.add_argument("--trim", nargs=2, type=float, metavar=("START", "END"))
ap.add_argument("--crop", help="auto | none | W:H:X:Y")
ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
ap.add_argument("--hflip", action="store_true")
ap.add_argument("--vflip", action="store_true")
ap.add_argument("--scale", type=float, default=1.0)
ap.add_argument("--max-h", type=int, default=0)
ap.add_argument("--no-motion", action="store_true")
ap.add_argument("--model", default="auto")
ap.add_argument("--slowmo", type=float, default=2.0)
ap.add_argument("--mult", type=float)
ap.add_argument("--fixed", type=float)
ap.add_argument("--scene", default="normal", choices=["off", "normal", "sensitive"])
ap.add_argument("--audio", default="auto", choices=["auto", "copy", "convert", "remove"])
ap.add_argument("--acodec", default="auto", choices=["auto", "aac", "opus", "flac", "pcm"])
ap.add_argument("--aquality", default="high", choices=["transparent", "high", "small"])
ap.add_argument("--slow", default="natural", choices=["natural", "keep_pitch"])
ap.add_argument("--video", default="encode", choices=["encode", "copy"])
ap.add_argument("--encoder", default="av1_nvenc")
ap.add_argument("--vmaf", type=float, default=95.0, help="0 = constant quality")
ap.add_argument("--q", type=int)
ap.add_argument("--out-dir", default="")
ap.add_argument("--name", default="")
ap.add_argument("--dry", action="store_true", help="show the planned job, don't run it")
a = ap.parse_args()

ensure_dirs()
settings = Settings.load()
tc = Toolchain.resolve(settings)
e = Edit(clips=a.clips)
if a.trim:
    e.trim.update(on=True, start=a.trim[0], end=a.trim[1])
if a.crop or a.rotate or a.hflip or a.vflip or a.scale < 1 or a.max_h:
    crop = a.crop or "none"
    if ":" in crop:
        crop = [int(x) for x in crop.split(":")]
    e.crop.update(on=True, crop=crop, rotate=a.rotate, hflip=a.hflip, vflip=a.vflip, scale=a.scale, max_h=a.max_h)
mode, val = ("fixed", a.fixed) if a.fixed else (("multiply", a.mult) if a.mult else ("keep", 0.0))
e.motion.update(on=not a.no_motion, model=a.model, slowmo=a.slowmo, fps_mode=mode, fps_value=val, scene=a.scene)
e.audio.update(mode=a.audio, codec=a.acodec, quality=a.aquality, slow=a.slow)
e.output.update(video=a.video, encoder=a.encoder, vmaf=a.vmaf or None, q=a.q, out_dir=a.out_dir, name=a.name)

for lvl, msg in check(e, tc):
    print(f"[{lvl}] {msg}")
job = compile_job(tc, settings, e)
print("Kind  :", job.kind)
print("Steps :", " → ".join(s.label for s in job.steps))
print("Audio :", job.plan.get("audio_out", {}).get("label", "-"))
print("Output:", job.output)
for n in job.state.get("notes") or []:
    print("Note  :", n)
if a.dry:
    sys.exit(0)

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
q.start()
try:
    time.sleep(0.5)
    while q.is_running:
        time.sleep(0.2)
except KeyboardInterrupt:
    q.stop()
    q.wait(10)
print("Log   :", LOGS_DIR / f"{job.id}.log")
