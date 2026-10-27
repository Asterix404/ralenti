"""Builds step-based jobs from user options and executes each step. Engine-agnostic: the AI part
is delegated to the engine that owns the chosen model."""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

from . import vmaf
from .audio import AudioMode, encode_audio_args, resolve_mode
from .commands import (build_audio, build_cut, build_encode, build_merge, build_mux, build_preview_compose,
                       scaled_size, write_concat_list)
from .config import TEMP_DIR, Settings
from .cropdetect import Crop, detect_crop
from .encoders import ENCODERS, Encoder
from .engines import Render, engine_for, resolve_model
from .fps import target_fps
from .jobs import Job, Step
from .merge import MergeTarget, build_merge_reencode, compat_issues, pick_target
from .probe import MediaInfo, probe
from .tools import Toolchain
from .tools import run as run_tool

INTERP_DEFAULTS = dict(model="auto", fps_mode="keep", fps_value=0.0, slowmo=2.0, crop="auto", audio="natural",
                       encoder="av1_nvenc", q=None, vmaf=95.0, start=None, end=None, scale=1.0, device="0",
                       engine_opts=None, out_dir="", suffix="_ralenti")
ENCODE_DEFAULTS = dict(encoder="av1_nvenc", q=None, vmaf=95.0, audio="auto", out_dir="", suffix="_enc")
BYTES_PER_PIXEL = {"ffv1": 1.6, "ref_nvenc": 0.4}


# ---------------- helpers ----------------
def info_to_dict(i: MediaInfo) -> dict:
    d = asdict(i)
    d["fps"] = str(i.fps)
    return d


def info_from_dict(d: dict) -> MediaInfo:
    return MediaInfo(**{**d, "fps": Fraction(d.get("fps") or "0")})


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for n in range(1, 1000):
        c = p.with_name(f"{p.stem} ({n}){p.suffix}")
        if not c.exists():
            return c
    return p


def part_path(output: str) -> str:
    p = Path(output)
    return str(p.with_name(f"{p.stem}.ralenti-part{p.suffix}"))


def _out(src: str, out_dir: str, tail: str, ext: str) -> str:
    p = Path(src)
    return str(unique_path((Path(out_dir) if out_dir else p.parent) / f"{p.stem}{tail}{ext}"))


def _tmp(settings: Settings, job_id: str) -> str:
    return str(Path(settings.temp_dir or TEMP_DIR) / job_id)


def _trim(spec: dict) -> tuple[float | None, float | None]:
    start = spec.get("start") or None
    dur = (spec["end"] - (start or 0)) if spec.get("end") else None
    return start, dur


def cleanup_temp(job: Job, remove_part: bool = False) -> None:
    if tmp := job.plan.get("tmp"):
        shutil.rmtree(tmp, ignore_errors=True)
    if remove_part and (part := job.plan.get("part")):
        Path(part).unlink(missing_ok=True)


def _prepare(tc: Toolchain, s: dict):
    """Resolves the model ("auto" or missing -> best installed) and returns its engine."""
    if not tc.ffmpeg:
        raise ValueError("FFmpeg is missing. It's required: get it from Downloads.")
    code = resolve_model(tc, s.get("model"))
    if not code:
        raise ValueError("No AI engine is installed. Get RIFE (free) from Downloads.")
    s["model"] = code
    eng = engine_for(code)
    s["engine_opts"] = {**eng.defaults(), **(s.get("engine_opts") or {})}
    return eng


def _render_args(job: Job, tc: Toolchain):
    s, p, st = job.spec, job.plan, job.state
    eng = engine_for(s["model"])
    if eng is None or not eng.available(tc):
        raise RuntimeError(f"The engine for the model {s['model']} isn't installed.")
    info = info_from_dict(p["info"])
    start, dur = _trim(s)
    r = Render(info=info, model=s["model"], start=start, dur=dur or max(0.0, info.duration - (start or 0)),
               crop=Crop(*st["crop"]) if st.get("crop") else None,
               size=tuple(st["size"]) if st.get("size") else None,
               target=Fraction(st["fps"]) if st.get("fps") else (info.fps or Fraction(30)),
               slowmo=float(s["slowmo"]), device=str(s.get("device", "0")),
               opts=dict(s.get("engine_opts") or {}), tmp=Path(p["tmp"]))
    return eng, r


def _tail(s: dict, extra: str = "") -> str:
    mode_tag = {"multiply": f"{s['fps_value']:g}x", "fixed": f"{s['fps_value']:g}fps"}.get(s["fps_mode"], "")
    sc = float(s.get("scale") or 1)
    return (s["suffix"] + f"_{s['model']}" + (f"_{mode_tag}" if mode_tag else "")
            + (f"_slow{s['slowmo']:g}x" if s["slowmo"] != 1 else "") + extra
            + (f"_{round(sc * 100)}pct" if sc < 0.999 else ""))


# ---------------- job builders ----------------
def make_interpolate_job(tc: Toolchain, settings: Settings, src: str, **opts) -> Job:
    s = {**INTERP_DEFAULTS, **opts, "src": str(src)}
    eng = _prepare(tc, s)
    info = probe(tc.ffprobe, s["src"])
    if not info.width:
        raise ValueError(f"Not a readable video: {src}")
    enc = ENCODERS[s["encoder"]]
    if not tc.binary_for(enc.codec):
        raise ValueError(f"{enc.label} isn't available with your FFmpeg build.")
    amode = resolve_mode(AudioMode(s["audio"]), s["slowmo"], info.has_audio)
    use_vmaf = bool(s["vmaf"]) and enc.has_quality and tc.has_vmaf
    two_pass = use_vmaf or not eng.direct_encode(tc, enc.codec)
    audio_step = amode != AudioMode.DROP
    output = _out(s["src"], s["out_dir"], _tail(s, f"_vmaf{s['vmaf']:g}" if use_vmaf else ""), enc.ext)

    job = Job("interpolate", Path(src).name, output, s)
    tmp = Path(_tmp(settings, job.id))
    inter = settings.intermediate if settings.intermediate in BYTES_PER_PIXEL else "ref_nvenc"
    if not eng.direct_encode(tc, ENCODERS[inter].codec):
        inter = "ffv1" if inter == "ref_nvenc" else "ref_nvenc"
    job.plan = dict(info=info_to_dict(info), audio_mode=amode.value, vmaf=use_vmaf, two_pass=two_pass,
                    audio_step=audio_step, engine=eng.key, engine_temp=eng.temp_bytes(), tmp=str(tmp),
                    part=part_path(output), intermediate=str(tmp / "intermediate.mkv"), intermediate_enc=inter,
                    video_tmp=str(tmp / f"video{enc.ext}"), audio_tmp=str(tmp / "audio.mka"))
    steps = [Step("analyze", "Analyzing", 1), Step("interpolate", f"Interpolating ({eng.name})", 60 if two_pass else 90)]
    if audio_step:
        steps.append(Step("audio", "Processing audio", 2))
    if use_vmaf:
        steps.append(Step("vmaf_search", f"VMAF search (target {s['vmaf']:g})", 15))
    if two_pass:
        steps.append(Step("encode", f"Encoding ({enc.label})", 20))
    if audio_step:
        steps.append(Step("mux", "Muxing", 1))
    steps.append(Step("finalize", "Finishing", 0.5))
    job.steps = steps
    return job


def make_preview_job(tc: Toolchain, settings: Settings, src: str, start: float, length: float, **opts) -> Job:
    """A short before/after render that runs on the preview lane, next to the main queue."""
    s = {**INTERP_DEFAULTS, **opts, "src": str(src)}
    eng = _prepare(tc, s)
    info = probe(tc.ffprobe, s["src"])
    if not info.width:
        raise ValueError(f"Not a readable video: {src}")
    start = max(0.0, min(float(start), max(0.0, info.duration - 0.2)))
    dur = max(0.2, min(float(length), info.duration - start))
    s["start"], s["end"] = (start or None), start + dur
    amode = resolve_mode(AudioMode(s["audio"]), s["slowmo"], info.has_audio)
    job = Job("preview", f"Preview · {Path(src).name}", "", s)
    out = Path(settings.temp_dir or TEMP_DIR) / "previews" / f"{job.id}.mp4"
    job.output = str(out)
    job.plan = dict(info=info_to_dict(info), audio_mode=amode.value, vmaf=False, two_pass=False,
                    audio_step=False, engine=eng.key, engine_temp=eng.temp_bytes(), tmp=_tmp(settings, job.id),
                    part=part_path(str(out)))
    job.steps = [Step("analyze", "Analyzing", 1), Step("preview", f"Rendering preview ({eng.name})", 20),
                 Step("finalize", "Finishing", 0.2)]
    return job


def make_encode_job(tc: Toolchain, settings: Settings, src: str, **opts) -> Job:
    s = {**ENCODE_DEFAULTS, **opts, "src": str(src)}
    info = probe(tc.ffprobe, s["src"])
    if not info.width:
        raise ValueError(f"Not a readable video: {src}")
    enc = ENCODERS[s["encoder"]]
    use_vmaf = bool(s["vmaf"]) and enc.has_quality and tc.has_vmaf
    tail = s["suffix"] + f"_{enc.key}" + (f"_vmaf{s['vmaf']:g}" if use_vmaf else (f"_q{s['q']}" if s["q"] is not None else ""))
    output = _out(s["src"], s["out_dir"], tail, enc.ext)
    job = Job("encode", Path(src).name, output, s)
    job.plan = dict(info=info_to_dict(info), vmaf=use_vmaf, tmp=_tmp(settings, job.id), part=part_path(output))
    job.steps = (([Step("vmaf_search", f"VMAF search (target {s['vmaf']:g})", 30)] if use_vmaf else [])
                 + [Step("encode", f"Encoding ({enc.label})", 70), Step("finalize", "Finishing", 0.5)])
    return job


def make_cut_job(tc: Toolchain, settings: Settings, src: str, start: float, end: float,
                 output: str | None = None) -> Job:
    output = output or _out(src, "", "_cut", Path(src).suffix)
    job = Job("cut", Path(src).name, output, dict(src=str(src), start=start, end=end))
    job.plan = dict(tmp=_tmp(settings, job.id), part=part_path(output))
    job.steps = [Step("cut", "Cutting (lossless)", 1), Step("finalize", "Finishing", 0.1)]
    return job


def make_merge_job(tc: Toolchain, settings: Settings, files: list[str], output: str, mode: str = "auto",
                   encoder: str = "hevc_nvenc", q: int | None = None, res: str = "first") -> Job:
    """mode: auto (lossless if compatible, else re-encode) | copy | reencode."""
    infos = [probe(tc.ffprobe, f) for f in files]
    if bad := [Path(i.path).name for i in infos if not i.width]:
        raise ValueError("Not readable as video: " + ", ".join(bad))
    issues = compat_issues(infos)
    reencode = mode == "reencode" or (mode == "auto" and bool(issues))
    if reencode:
        output = str(Path(output).with_suffix(ENCODERS[encoder].ext))
    output = str(unique_path(Path(output)))
    job = Job("merge", f"{len(files)} clips", output, dict(files=list(files), mode=mode, encoder=encoder, q=q, res=res))
    job.plan = dict(tmp=_tmp(settings, job.id), part=part_path(output), total=sum(i.duration for i in infos),
                    reencode=reencode, issues=issues, infos=[info_to_dict(i) for i in infos])
    if reencode:
        job.plan["target"] = asdict(pick_target(infos, res))
    label = f"Merging (re-encode · {ENCODERS[encoder].label})" if reencode else "Merging (lossless)"
    job.steps = [Step("merge", label, 1), Step("finalize", "Finishing", 0.1)]
    return job


# ---------------- step handlers ----------------
def _analyze(job: Job, tc: Toolchain, ctl) -> None:
    s, p, st = job.spec, job.plan, job.state
    info = info_from_dict(p["info"])
    Path(p["tmp"]).mkdir(parents=True, exist_ok=True)
    fps = target_fps(info.fps, s["fps_mode"], s["fps_value"])
    st["fps"] = str(fps) if fps else ""
    crop = None
    if s["crop"] == "auto" and tc.has_cropdetect:
        ctl.progress(0.2, "Detecting black bars")
        c = detect_crop(tc.ffmpeg, info)
        crop = [c.w, c.h, c.x, c.y] if c else None
    elif isinstance(s["crop"], (list, tuple)):
        crop = list(s["crop"])
    st["crop"] = crop
    bw, bh = (crop[0], crop[1]) if crop else (info.width, info.height)
    sc = float(s.get("scale") or 1)
    st["size"] = list(scaled_size(bw, bh, sc)) if sc < 0.999 else None
    need = float(p.get("engine_temp") or 0)
    if p.get("two_pass"):
        start, dur = _trim(s)
        seconds = (dur or info.duration - (start or 0)) * float(s["slowmo"])
        w, h = st["size"] or (bw, bh)
        need += w * h * float(fps or info.fps or 30) * seconds * BYTES_PER_PIXEL[p["intermediate_enc"]]
    if need:
        free = shutil.disk_usage(p["tmp"]).free
        st["temp_est"] = int(need)
        if free < need * 1.15:
            raise RuntimeError(f"Not enough temp space: need about {need / 1e9:.1f} GB, "
                               f"{free / 1e9:.1f} GB free ({p['tmp']})")
    crop_txt = f"crop {crop[0]}x{crop[1]}+{crop[2]}+{crop[3]}" if crop else "no crop"
    ctl.progress(1.0, f"{crop_txt} · {float(fps or 0):.3f} fps")


def _interpolate(job: Job, tc: Toolchain, ctl) -> None:
    s, p = job.spec, job.plan
    eng, r = _render_args(job, tc)
    if p["two_pass"]:
        enc, out, q = ENCODERS[p["intermediate_enc"]], p["intermediate"], None
    elif p["audio_step"]:
        enc, out, q = ENCODERS[s["encoder"]], p["video_tmp"], s.get("q")
    else:
        enc, out, q = ENCODERS[s["encoder"]], p["part"], s.get("q")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    eng.render(r, tc, ctl, enc, out, q)


def _audio(job: Job, tc: Toolchain, ctl) -> None:
    s, p = job.spec, job.plan
    info = info_from_dict(p["info"])
    start, dur = _trim(s)
    mode = AudioMode(p["audio_mode"])
    ctl.job.detail = {"keep_pitch": "Slow down, keep pitch", "natural": "Slow down, natural pitch",
                      "copy": "Copy"}.get(mode.value, mode.value)
    cmd = build_audio(tc, info, mode, s["slowmo"], p["audio_tmp"], Path(job.output).suffix, start, dur)
    ctl.run_ffmpeg(cmd, (dur or info.duration - (start or 0)) * s["slowmo"])


def _vmaf_search(job: Job, tc: Toolchain, ctl) -> None:
    s, p, st = job.spec, job.plan, job.state
    enc = ENCODERS[s["encoder"]]
    ref = p["intermediate"] if job.kind == "interpolate" else s["src"]
    src_info = info_from_dict(p["info"])
    ref_info = probe(tc.ffprobe, ref)
    windows = vmaf.sample_windows(ref_info.duration)
    model = vmaf.model_for(ref_info.width, ref_info.height)
    lo, hi = enc.q_range
    max_iter = (hi - lo + 1).bit_length()
    sample_dir = Path(p["tmp"]) / "samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    sample_secs = sum(d for _, d in windows)
    history: dict[int, dict] = {}
    done = [0]

    def score_at(q: int) -> float:
        scores, size, n = [], 0, len(windows)
        for i, (start, dur) in enumerate(windows):
            seg = 1 / (max_iter * n)
            base = (done[0] * n + i) * seg
            out = sample_dir / f"q{q}_{i}.mkv"
            ctl.job.detail = f"q={q} · sample {i + 1}/{n}"
            ctl.run_ffmpeg(build_encode(tc, ref, src_info, enc, str(out), q=q, start=start, duration=dur),
                           dur, span=(base, base + seg * 0.6))
            err = ctl.run_ffmpeg(vmaf.build_score(tc.ffmpeg, str(out), ref, start, dur, model, fps=ref_info.fps),
                                 dur, span=(base + seg * 0.6, base + seg))
            scores.append(vmaf.parse_score(err))
            size += out.stat().st_size
            out.unlink(missing_ok=True)
        done[0] += 1
        mean = sum(scores) / len(scores)
        est = size / sample_secs * ref_info.duration if sample_secs else 0
        history[q] = {"vmaf": round(mean, 3), "est_bytes": int(est)}
        ctl.log(f"[vmaf] q={q} vmaf={mean:.3f} est={est / 1e6:.1f} MB")
        ctl.progress(min(1.0, done[0] / max_iter), f"q={q} → VMAF {mean:.2f} · ~{est / 1e6:.0f} MB")
        return mean

    best, _ = vmaf.quality_search(score_at, lo, hi, float(s["vmaf"]), max_iter)
    vals = [h["vmaf"] for h in history.values()]
    if len(history) >= 3 and max(history) - min(history) >= 8 and max(vals) - min(vals) < 1.5:
        raise RuntimeError(f"VMAF barely changes with quality ({min(vals):.1f}-{max(vals):.1f}). "
                           "Samples and reference are probably misaligned; see the log.")
    if best is None:
        best = lo
        st["vmaf_warning"] = f"Target {s['vmaf']:g} not reachable; used best quality q={lo}"
        ctl.log(f"[vmaf] {st['vmaf_warning']}")
    st["q"] = best
    st["vmaf_history"] = {str(k): v for k, v in history.items()}
    h = history.get(best, {})
    ctl.progress(1.0, f"Chosen q={best} · VMAF {h.get('vmaf', '?')} · ~{h.get('est_bytes', 0) / 1e6:.0f} MB")


def _encode(job: Job, tc: Toolchain, ctl) -> None:
    s, p, st = job.spec, job.plan, job.state
    enc = ENCODERS[s["encoder"]]
    info = info_from_dict(p["info"])
    if job.kind == "interpolate":
        src = p["intermediate"]
        out = p["video_tmp"] if p["audio_step"] else p["part"]
        audio, total = None, probe(tc.ffprobe, src).duration
    else:
        src, out = s["src"], p["part"]
        audio, total = encode_audio_args(s.get("audio", "auto"), info, enc.ext), info.duration
    q = st.get("q", s.get("q"))
    ctl.job.detail = f"{enc.label} · q={q if q is not None else enc.q_default}"
    ctl.run_ffmpeg(build_encode(tc, src, info, enc, out, q=q, audio=audio), total)


def _mux(job: Job, tc: Toolchain, ctl) -> None:
    p = job.plan
    enc = ENCODERS[job.spec["encoder"]]
    audio = p["audio_tmp"] if Path(p["audio_tmp"]).exists() else None
    ctl.run_ffmpeg(build_mux(tc, p["video_tmp"], audio, p["part"], vtag=enc.mp4_tag or None), 0)
    ctl.progress(1.0)


def _preview_encoder(tc: Toolchain) -> Encoder:
    if tc.binary_for("h264_nvenc"):
        return Encoder("pv", "Preview", "h264_nvenc", ".mp4",
                       ("-c:v", "h264_nvenc", "-preset", "p4", "-rc", "constqp", "-qp", "16"), "yuv420p")
    return Encoder("pv", "Preview", "libx264", ".mp4", ("-c:v", "libx264", "-preset", "veryfast", "-crf", "16"),
                   "yuv420p")


def _preview(job: Job, tc: Toolchain, ctl) -> None:
    p, st = job.plan, job.state
    eng, r = _render_args(job, tc)
    r.tmp.mkdir(parents=True, exist_ok=True)
    ai, pv = r.tmp / "ai.mp4", _preview_encoder(tc)
    eng.render(r, tc, ctl, pv, str(ai), span=(0.0, 0.85))
    ctl.job.detail = f"{eng.name} · {r.total:.1f} s preview"
    part, out = Path(p["part"]), Path(job.output)
    part.parent.mkdir(parents=True, exist_ok=True)
    side = [str(out.with_suffix(".orig.m4a")), str(out.with_suffix(".proc.m4a"))] if r.info.has_audio else None
    cmd = build_preview_compose(tc, r.info, str(ai), r.slowmo, str(part), side, pv, crop=r.crop, fps=r.target,
                                start=r.start, duration=r.dur, audio_mode=AudioMode(p["audio_mode"]))
    ctl.run_ffmpeg(cmd, r.total, span=(0.85, 1.0))
    st["sidecars"] = side
    st["orig_stretched"] = True
    thumb = out.with_suffix(".jpg")
    try:
        run_tool([tc.ffmpeg, "-hide_banner", "-nostdin", "-y", "-ss", f"{r.total / 2:.2f}", "-i", str(part),
                  "-vf", "crop=iw:ih/2:0:ih/2,scale=320:-2", "-frames:v", "1", "-q:v", "4", str(thumb)], timeout=30)
    except Exception:
        pass
    if thumb.exists():
        st["thumb"] = str(thumb)


def _cut(job: Job, tc: Toolchain, ctl) -> None:
    s, p = job.spec, job.plan
    ctl.run_ffmpeg(build_cut(tc, s["src"], s["start"], s["end"], p["part"]), s["end"] - s["start"])


def _merge(job: Job, tc: Toolchain, ctl) -> None:
    s, p = job.spec, job.plan
    if p.get("reencode"):
        infos = [info_from_dict(d) for d in p["infos"]]
        target = MergeTarget(**p["target"])
        enc = ENCODERS[s["encoder"]]
        ctl.job.detail = f"{target.width}×{target.height} · {float(target.rate):.3f} fps → {enc.label}"
        ctl.run_ffmpeg(build_merge_reencode(tc, infos, target, enc, p["part"], q=s.get("q")), p["total"])
        return
    lst = write_concat_list(s["files"], Path(p["tmp"]) / "concat.txt")
    ctl.run_ffmpeg(build_merge(tc, lst, p["part"]), p.get("total", 0))


def _finalize(job: Job, tc: Toolchain, ctl) -> None:
    part = Path(job.plan["part"])
    if not part.exists():
        raise RuntimeError(f"Expected output missing: {part}")
    final = Path(job.output)
    if final.exists():
        final = unique_path(final)
        job.output = str(final)
    os.replace(part, final)
    cleanup_temp(job)
    ctl.progress(1.0, f"{final.stat().st_size / 1e6:.1f} MB")


HANDLERS = {"analyze": _analyze, "interpolate": _interpolate, "audio": _audio, "vmaf_search": _vmaf_search,
            "encode": _encode, "mux": _mux, "preview": _preview, "cut": _cut, "merge": _merge,
            "finalize": _finalize}


def execute(step: Step, job: Job, tc: Toolchain, ctl) -> None:
    handler = HANDLERS.get(step.kind)
    if handler is None:
        raise RuntimeError(f"Unknown step type: {step.kind}")
    handler(job, tc, ctl)


def work_units(job: Job) -> float:
    """Rough job size (thousands of megapixel-frames), used for queue ETA learning."""
    p, s = job.plan, job.spec
    try:
        if job.kind in ("interpolate", "preview", "encode"):
            info = info_from_dict(p["info"])
            mp = info.width * info.height / 1e6
            if job.kind == "encode":
                secs, fps = info.duration, info.fps
            else:
                mp *= float(s.get("scale", 1) or 1) ** 2
                start, dur = _trim(s)
                secs = (dur or max(0.0, info.duration - (start or 0))) * float(s.get("slowmo", 1))
                fps = (Fraction(job.state["fps"]) if job.state.get("fps")
                       else target_fps(info.fps, s["fps_mode"], s["fps_value"]))
            return secs * float(fps or 30) * mp / 1000
        if job.kind == "merge":
            if p.get("reencode"):
                t = p.get("target", {})
                mp = t.get("width", 1920) * t.get("height", 1080) / 1e6
                return p.get("total", 0) * float(Fraction(t.get("fps", "30"))) * mp / 1000
            return p.get("total", 0) / 60
        if job.kind == "cut":
            return (s["end"] - s["start"]) / 60
    except (KeyError, ValueError, TypeError, ZeroDivisionError):
        pass
    return 0.0



# ===================== 0.10.0: segmented rendering (resume, pause, live output) =====================
import math  # noqa: E402
from dataclasses import replace as _replace  # noqa: E402

from .commands import _head  # noqa: E402

SEG_SECONDS = 10


def plan_segments(total: int, seg: int) -> list[list[int]]:
    """Source-frame ranges [a, b) relative to the clip start."""
    return [[a, min(a + seg, total)] for a in range(0, max(1, total), seg)]


def segment_limits(segs, ratio: Fraction) -> list[int]:
    """Output frames per segment; cumulative rounding so the total is exact."""
    return [int(math.floor(b * ratio) - math.floor(a * ratio)) for a, b in segs]


_make_interpolate_job_v09 = make_interpolate_job


def make_interpolate_job(tc, settings, src, **opts):
    job = _make_interpolate_job_v09(tc, settings, src, **opts)
    eng = engine_for(job.spec["model"])
    job.plan["segmented"] = bool(getattr(eng, "supports_segments", False))
    audio = next((s for s in job.steps if s.kind == "audio"), None)
    if audio:                                   # audio first: the video can be muxed as soon as it's done
        job.steps.remove(audio)
        job.steps.insert(1, audio)
    return job


_analyze_v09 = _analyze


def _analyze_seg(job, tc, ctl):
    _analyze_v09(job, tc, ctl)
    p, s, st = job.plan, job.spec, job.state
    if job.kind != "interpolate" or not p.get("segmented") or st.get("segments"):
        return
    info = info_from_dict(p["info"])
    src = info.fps or Fraction(30)
    start, dur = _trim(s)
    dur = dur or max(0.0, info.duration - (start or 0))
    total = max(1, int(round(dur * float(src))))
    st["segments"] = plan_segments(total, max(60, int(round(SEG_SECONDS * float(src)))))
    target = Fraction(st["fps"]) if st.get("fps") else src
    st["ratio"] = str(target * Fraction(s["slowmo"]).limit_denominator(100) / src)
    st["seg_done"] = []


_interpolate_whole = _interpolate


def _interpolate_seg(job, tc, ctl):
    p, s, st = job.plan, job.spec, job.state
    if not p.get("segmented") or not st.get("segments"):
        return _interpolate_whole(job, tc, ctl)
    eng, r0 = _render_args(job, tc)
    if p["two_pass"]:
        enc, out, q = ENCODERS[p["intermediate_enc"]], p["intermediate"], None
    elif p["audio_step"]:
        enc, out, q = ENCODERS[s["encoder"]], p["video_tmp"], s.get("q")
    else:
        enc, out, q = ENCODERS[s["encoder"]], p["part"], s.get("q")
    seg_dir = Path(p["tmp"]) / "seg"
    seg_dir.mkdir(parents=True, exist_ok=True)
    segs, ratio = st["segments"], Fraction(st["ratio"])
    limits = segment_limits(segs, ratio)
    src = float(r0.info.fps or 30)
    clip0 = r0.start or 0.0
    done = set(st.get("seg_done") or [])
    n = len(segs)
    for i, ((a, b), lim) in enumerate(zip(segs, limits)):
        f = seg_dir / f"{i:05d}.mkv"
        if lim <= 0 or (i in done and f.exists() and f.stat().st_size > 0):
            done.add(i)
            continue
        ctl.check()
        count = (b - a) + (0 if i == n - 1 else 1)        # one extra frame: in-betweens into the next part
        r = _replace(r0, start=(clip0 + max(0.0, a - 0.25) / src) or None, dur=count / src, limit=lim,
                     tmp=Path(p["tmp"]) / "work")
        tmp_out = seg_dir / f"{i:05d}.part.mkv"
        ctl.job.detail = f"{eng.name} · part {i + 1} of {n}"
        eng.render(r, tc, ctl, enc, str(tmp_out), q, span=(i / n, (i + 1) / n))
        os.replace(tmp_out, f)
        done.add(i)
        st["seg_done"] = sorted(done)
        ctl.checkpoint()
    files = [str(seg_dir / f"{i:05d}.mkv") for i in range(n) if (seg_dir / f"{i:05d}.mkv").exists()]
    lst = write_concat_list(files, seg_dir / "list.txt")
    cmd = _head(tc.ffmpeg) + ["-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0:v:0", "-c", "copy"]
    if Path(out).suffix.lower() in (".mp4", ".mov"):
        if enc.mp4_tag:
            cmd += ["-tag:v", enc.mp4_tag]
        cmd += ["-movflags", "+faststart"]
    ctl.job.detail = f"Joining {len(files)} parts"
    ctl.run_ffmpeg(cmd + [out], 0)
    st["joined"] = out


HANDLERS["analyze"] = _analyze_seg
HANDLERS["interpolate"] = _interpolate_seg



# ===================== 0.11.1: never delete files a live viewer is playing =====================
import time as _time  # noqa: E402

_cleanup_v0110 = cleanup_temp


def cleanup_temp(job, remove_part: bool = False) -> None:
    tmp = job.plan.get("tmp")
    if tmp:
        m = Path(tmp) / ".live"
        try:
            live = m.exists() and _time.time() - m.stat().st_mtime < 10
        except OSError:
            live = False
        if live:                                   # the viewer cleans up when it lets go
            if remove_part and (part := job.plan.get("part")):
                Path(part).unlink(missing_ok=True)
            return
    _cleanup_v0110(job, remove_part)
