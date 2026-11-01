"""The edit model: one video (one or more clips joined in order) and its steps, in pipeline order:
Trim -> Transform -> Interpolation -> Audio -> Encode -> Output. compile_job() turns an edit into ONE queue job
running every step in a single pass, on the cheapest route: lossless copy, plain edit (no AI) or interpolation.
The internal keys 'crop' (Transform) and 'motion' (Interpolation) are kept so saved edits keep loading."""
from __future__ import annotations

import copy as _copy
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from .audio import passthrough_ok
from .audio_out import plan_audio
from .encoders import ENCODERS
from .engines import resolve_model
from .fps import target_fps
from .jobs import Job, Step
from .keyframes import keyframes, prev_key
from .merge import compat_issues
from .naming import render, resolve
from .pipeline import BYTES_PER_PIXEL, _prepare, _tmp, info_to_dict, part_path
from .probe import probe
from .segments import boundaries, kept_ranges, source_to_output
from .tracks import audio_tracks
from .transform import output_size

STEPS = ("trim", "crop", "motion", "audio", "encode", "output")
LABELS = {"trim": "Trim", "crop": "Transform", "motion": "Interpolation", "audio": "Audio", "encode": "Encode",
          "output": "Output"}
DEFAULTS = {
    "trim": {"on": False, "cuts": [], "drop": [], "start": None, "end": None},
    "crop": {"on": False, "crop": "auto", "rotate": 0, "hflip": False, "vflip": False, "scale": 1.0, "max_h": 0},
    "motion": {"on": True, "model": "auto", "fps_mode": "keep", "fps_value": 0.0, "slowmo": 2.0, "scene": "normal",
               "engine_opts": {}, "cuts_add": [], "cuts_remove": []},
    "audio": {"mode": "auto", "codec": "auto", "quality": "high", "slow": "natural", "tracks": [0]},
    "encode": {"video": "encode", "encoder": "av1_nvenc", "vmaf": 95.0, "q": None},
    "output": {"out_dir": "", "name": "", "container": "auto", "exists": "number"},
}
PER_VIDEO = {"motion": ("cuts_add", "cuts_remove"), "audio": ("tracks",), "output": ("out_dir", "name")}
VIDEO_CONTAINERS = {"av1_nvenc": {"mp4", "mkv"}, "svtav1": {"mp4", "mkv"}, "hevc_nvenc": {"mp4", "mkv", "mov"},
                    "x265": {"mp4", "mkv", "mov"}, "h264_nvenc": {"mp4", "mkv", "mov"}, "prores": {"mov", "mkv"},
                    "ffv1": {"mkv"}}


def _merge(base: dict, over) -> dict:
    out = _copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k in out:
            out[k] = _copy.deepcopy(v)
    return out


def _d(name):
    return lambda: _copy.deepcopy(DEFAULTS[name])


def step_recipe(step: str, d: dict) -> dict:
    """A step's settings without what belongs to one video."""
    if step == "trim":
        return {}
    r = {k: _copy.deepcopy(v) for k, v in (d or {}).items()
         if k in DEFAULTS[step] and k not in PER_VIDEO.get(step, ())}
    if step == "crop" and isinstance(r.get("crop"), list):
        r.pop("crop")
    return r


@dataclass
class Edit:
    clips: list = field(default_factory=list)
    trim: dict = field(default_factory=_d("trim"))
    crop: dict = field(default_factory=_d("crop"))
    motion: dict = field(default_factory=_d("motion"))
    audio: dict = field(default_factory=_d("audio"))
    encode: dict = field(default_factory=_d("encode"))
    output: dict = field(default_factory=_d("output"))

    def to_dict(self) -> dict:
        return {"version": 2, "clips": list(self.clips), **{k: _copy.deepcopy(getattr(self, k)) for k in STEPS}}

    @classmethod
    def from_dict(cls, d) -> "Edit":
        d = _copy.deepcopy(d or {})
        out = d.get("output") or {}
        if "encode" not in d:          # version 1 kept the encoder in Output
            d["encode"] = {k: out.pop(k) for k in ("video", "encoder", "vmaf", "q") if k in out}
        return cls(clips=list(d.get("clips") or []), **{k: _merge(DEFAULTS[k], d.get(k)) for k in STEPS})

    def copy(self) -> "Edit":
        return Edit.from_dict(self.to_dict())

    def active(self) -> list[str]:
        return [k for k in STEPS if k in ("audio", "encode", "output") or getattr(self, k).get("on")]

    def recipe(self) -> dict:
        return {k: step_recipe(k, getattr(self, k)) for k in STEPS if k != "trim"}

    def apply_recipe(self, r: dict, steps=None) -> None:
        r = r or {}
        for k in steps or [x for x in STEPS if x in r]:
            if k != "trim" and k in r:
                getattr(self, k).update(step_recipe(k, r[k]))


def _transform(c: dict):
    if not c.get("on"):
        return "none", 0, False, False, 1.0, 0, False
    crop = list(c["crop"]) if isinstance(c.get("crop"), (list, tuple)) else (c.get("crop") or "none")
    rot = int(c.get("rotate") or 0) % 360
    hf, vf = bool(c.get("hflip")), bool(c.get("vflip"))
    scale, max_h = float(c.get("scale") or 1.0), int(c.get("max_h") or 0)
    filters = crop != "none" or bool(rot) or hf or vf or scale < 0.999 or max_h > 0
    return crop, rot, hf, vf, scale, max_h, filters


def pick_ext(e: Edit, enc, copy_video: bool, src_suffix: str = ".mp4", needs_mkv: bool = False):
    """(extension, warning)"""
    c = (e.output.get("container") or "auto").lower().lstrip(".")
    if copy_video:
        if c != "auto":
            return "." + c, ""
        return (".mkv" if needs_mkv else (src_suffix or ".mkv")), ""
    base = enc.ext if enc else ".mp4"
    if c == "auto" or enc is None:
        return base, ""
    if c in VIDEO_CONTAINERS.get(enc.key, {"mkv"}):
        return "." + c, ""
    return base, f"{enc.label} can't be stored in .{c}, so it's saved as {base}."


def check(e: Edit, tc) -> list[tuple[str, str]]:
    """[(level, message)], level = 'error' (can't queue) or 'warning'."""
    out = []
    if not tc.ffmpeg:
        out.append(("error", "FFmpeg is missing. It's required: get it from Downloads."))
    if not e.clips:
        out.append(("error", "Open a video first."))
    t = e.trim
    if t.get("on") and not (t.get("cuts") or t.get("drop")) and t.get("start") is not None \
            and t.get("end") is not None and float(t["end"]) <= float(t["start"]):
        out.append(("error", "The trim ends before it starts."))
    m = e.motion
    if m.get("on"):
        if not resolve_model(tc, m.get("model")):
            out.append(("error", "No AI engine yet: install RIFE (free) from Downloads, or turn Interpolation off."))
        elif float(m.get("slowmo") or 1) == 1 and (m.get("fps_mode") or "keep") == "keep":
            out.append(("error", "Nothing to interpolate: turn on slow motion or pick a higher frame rate."))
    filters = _transform(e.crop)[6]
    v = e.encode
    copy_ok = v.get("video") == "copy" and not m.get("on") and not filters
    if v.get("video") == "copy" and not copy_ok:
        out.append(("warning", "The video can't be copied with Interpolation or Transform on: it's re-encoded "
                               "with the chosen encoder."))
    if not copy_ok:
        enc = ENCODERS.get(v.get("encoder") or "")
        if enc is None or enc.hidden:
            out.append(("error", "Pick an encoder in Encode."))
        elif not tc.binary_for(enc.codec):
            out.append(("error", f"{enc.label} isn't available with your FFmpeg build."))
    return out


def _legacy(aplan: dict) -> str:
    if aplan["mode"] == "drop":
        return "drop"
    return aplan.get("slow") or "copy"


def _intermediate(settings, ok) -> str:
    inter = settings.intermediate if settings.intermediate in BYTES_PER_PIXEL else "ref_nvenc"
    if not ok(ENCODERS[inter].codec):
        inter = "ffv1" if inter == "ref_nvenc" else "ref_nvenc"
    return inter


def _encoder(v: dict, tc):
    enc = ENCODERS.get(v.get("encoder") or "")
    if enc is None or enc.hidden or not tc.binary_for(enc.codec):
        raise ValueError("The chosen encoder isn't available with your FFmpeg build.")
    return enc


def _output(e: Edit, ext: str, tail: str, ctx: dict, overwrite: bool) -> str:
    src = Path(e.clips[0])
    folder = Path(e.output.get("out_dir") or src.parent)
    tpl = (e.output.get("name") or "").strip()
    base = (render(tpl, ctx) if tpl else "") or f"{src.stem}{tail}"
    return str(resolve(folder, base, ext, overwrite))


def scene_times(times, ranges) -> list[float]:
    """Manual scene cuts (source time on the joined clips) as times in the trimmed output. Cuts in removed parts
    or right on a join are dropped: the join already cuts there."""
    out = []
    for t in times or []:
        t = float(t)
        if any(a + 0.02 < t < b - 0.02 for a, b in ranges):
            out.append(round(source_to_output(t, ranges), 3))
    return out


def compile_job(tc, settings, edit: Edit) -> Job:
    e = edit.copy()
    msgs = check(e, tc)
    if errors := [m for lvl, m in msgs if lvl == "error"]:
        raise ValueError(errors[0])
    notes = [m for lvl, m in msgs if lvl == "warning"]
    infos = [probe(tc.ffprobe, c) for c in e.clips]
    if bad := [Path(i.path).name for i in infos if not i.width]:
        raise ValueError("Not readable as video: " + ", ".join(bad))
    joined = len(infos) > 1
    lossless_join = joined and not compat_issues(infos)
    info = infos[0]
    tracks_all = [] if joined else audio_tracks(tc.ffprobe, e.clips[0])
    sel = [int(x) for x in (e.audio.get("tracks") or [0]) if 0 <= int(x) < len(tracks_all)] or [0]
    if tracks_all:
        t0 = tracks_all[sel[0]]
        info = replace(info, acodec=t0.get("codec") or info.acodec, channels=t0.get("channels") or info.channels,
                       sample_rate=t0.get("sample_rate") or info.sample_rate)
    if joined:
        info = replace(info, duration=sum(i.duration for i in infos), has_audio=any(i.has_audio for i in infos),
                       acodec=info.acodec if lossless_join else "flac",
                       sample_rate=info.sample_rate if lossless_join else 48000,
                       channels=info.channels if lossless_join else 2)
    t, c, m, a, v, o = e.trim, e.crop, e.motion, e.audio, e.encode, e.output
    ranges = kept_ranges(t, info.duration)
    if not ranges:
        raise ValueError("Every part of the video is removed: keep at least one segment in Trim.")
    multi_cut = len(ranges) > 1
    if multi_cut:
        start = end = None
    else:
        start = ranges[0][0] if ranges[0][0] > 1e-3 else None
        end = ranges[0][1] if ranges[0][1] < info.duration - 1e-3 else None
    crop, rot, hf, vf, scale, max_h, filters = _transform(c)
    motion = bool(m.get("on"))
    copy_video = v.get("video") == "copy" and not motion and not filters
    if copy_video and joined and not lossless_join:
        copy_video = False
        notes.append("The clips don't match, so they're converted and the video is re-encoded.")
    overwrite = o.get("exists") == "overwrite"
    bw, bh = (crop[0], crop[1]) if isinstance(crop, list) else (info.width, info.height)
    size = output_size(bw, bh, rot, scale, max_h)
    label = Path(e.clips[0]).name + (f" + {len(infos) - 1} more" if joined else "")
    spec = dict(src=e.clips[0], start=start, end=end, crop=crop, scale=scale, max_h=max_h,
                transform={"rotate": rot, "hflip": hf, "vflip": vf}, device=str(settings.gpu_device or "0"),
                out_dir=o.get("out_dir") or "", edit=e.to_dict(), fps_mode="keep", fps_value=0.0, slowmo=1.0,
                model=None)
    src_suffix = Path(e.clips[0]).suffix.lower()
    needs_mkv = joined or multi_cut

    def ctx(model, slow, fps, vmaf, enc_key) -> dict:
        now, f = datetime.now(), float(fps or info.fps or 0)
        return {"name": Path(e.clips[0]).stem, "model": model or "none", "slowmo": f"{slow:g}",
                "fps": f"{f:.3f}".rstrip("0").rstrip("."), "res": f"{size[0]}x{size[1]}", "encoder": enc_key,
                "vmaf": f"{vmaf:g}" if vmaf else "cq", "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H-%M")}

    def fix_audio(aplan, ext, slow, enc_key):
        if aplan["mode"] == "copy" and tracks_all and any(not passthrough_ok(tracks_all[i]["codec"], ext) for i in sel):
            aplan = plan_audio({**a, "mode": "convert", "codec": "auto"}, info, ext, slow, tc, enc_key)
            aplan["warning"] = aplan.get("warning") or ("One of the chosen audio tracks can't be copied into this "
                                                        "container, so the audio is converted.")
        return aplan

    if copy_video:
        ext, ext_w = pick_ext(e, None, True, src_suffix, needs_mkv)
        aplan = fix_audio(plan_audio(a, info, ext, 1.0, tc), ext, 1.0, "")
        job = Job("edit", label, _output(e, ext, "_ralenti", ctx(None, 1.0, info.fps, None, "copy"), overwrite), spec)
        tmp = Path(_tmp(settings, job.id))
        job.plan = dict(remux=True, audio_out=aplan, audio_step=False, tmp=str(tmp), part=part_path(job.output))
        steps = [Step("remux", "Copying (no re-encode)", 1)]
        if t.get("on") and not joined:
            kfs = keyframes(tc.ffprobe, e.clips[0])
            early = [r[0] - prev_key(kfs, r[0]) for r in ranges if r[0] > 1e-3] if kfs else []
            if early and max(early) > 0.05:
                notes.append(f"Lossless cuts start on keyframes: a segment may start up to {max(early):.1f} s before "
                             "your cut. Snap cuts to keyframes in Trim, or re-encode for exact cuts.")

    elif not motion:
        enc = _encoder(v, tc)
        ext, ext_w = pick_ext(e, enc, False, src_suffix, needs_mkv)
        use_vmaf = bool(v.get("vmaf")) and enc.has_quality and tc.has_vmaf
        needs_ref = use_vmaf and bool(filters or start or end)
        aplan = fix_audio(plan_audio(a, info, ext, 1.0, tc, enc.key), ext, 1.0, enc.key)
        audio_step = aplan["mode"] != "drop"
        spec.update(encoder=enc.key, vmaf=float(v["vmaf"]) if use_vmaf else None, q=None if use_vmaf else v.get("q"))
        out = _output(e, ext, "_ralenti" + (f"_vmaf{v['vmaf']:g}" if use_vmaf else ""),
                      ctx(None, 1.0, info.fps, v.get("vmaf") if use_vmaf else None, enc.key), overwrite)
        job = Job("edit", label, out, spec)
        tmp = Path(_tmp(settings, job.id))
        inter = _intermediate(settings, tc.binary_for)
        job.plan = dict(tmp=str(tmp), part=part_path(job.output), video_tmp=str(tmp / f"video{ext}"),
                        audio_tmp=str(tmp / "audio.mka"), intermediate=str(tmp / "intermediate.mkv"),
                        intermediate_enc=inter, vmaf=use_vmaf,
                        vmaf_ref=str(tmp / "intermediate.mkv") if needs_ref else None, two_pass=needs_ref,
                        engine_temp=0, audio_step=audio_step, audio_out=aplan, audio_mode=_legacy(aplan),
                        segmented=False)
        steps = [Step("analyze", "Analyzing", 1)]
        if audio_step:
            steps.append(Step("audio", "Processing audio", 2))
        if not use_vmaf or needs_ref:
            steps.append(Step("render", "Applying the edit" if use_vmaf else f"Rendering ({enc.label})",
                              50 if use_vmaf else 90))
        if use_vmaf:
            steps += [Step("vmaf_search", f"VMAF search (target {v['vmaf']:g})", 20),
                      Step("encode", f"Encoding ({enc.label})", 30)]
        if audio_step:
            steps.append(Step("mux", "Muxing", 1))

    else:
        enc = _encoder(v, tc)
        ext, ext_w = pick_ext(e, enc, False, src_suffix, needs_mkv)
        s = {**spec, "model": m.get("model") or "auto", "fps_mode": m.get("fps_mode") or "keep",
             "fps_value": float(m.get("fps_value") or 0), "slowmo": float(m.get("slowmo") or 1),
             "scene": m.get("scene") or "normal", "engine_opts": dict(m.get("engine_opts") or {}),
             "cuts_add": scene_times(m.get("cuts_add"), ranges) + (boundaries(ranges) if multi_cut else []),
             "cuts_remove": scene_times(m.get("cuts_remove"), ranges), "encoder": enc.key}
        eng = _prepare(tc, s)
        use_vmaf = bool(v.get("vmaf")) and enc.has_quality and tc.has_vmaf
        s["vmaf"], s["q"] = (float(v["vmaf"]) if use_vmaf else None), (None if use_vmaf else v.get("q"))
        two_pass = use_vmaf or not eng.direct_encode(tc, enc.codec)
        aplan = fix_audio(plan_audio(a, info, ext, s["slowmo"], tc, enc.key), ext, s["slowmo"], enc.key)
        audio_step = aplan["mode"] != "drop"
        s["audio"] = _legacy(aplan)
        tgt = target_fps(info.fps, s["fps_mode"], s["fps_value"] or 2.0) or info.fps
        mode_tag = {"multiply": f"_{s['fps_value']:g}x", "fixed": f"_{s['fps_value']:g}fps"}.get(s["fps_mode"], "")
        tail = (f"_ralenti_{s['model']}{mode_tag}" + (f"_slow{s['slowmo']:g}x" if s["slowmo"] != 1 else "")
                + (f"_vmaf{v['vmaf']:g}" if use_vmaf else ""))
        out = _output(e, ext, tail, ctx(s["model"], s["slowmo"], tgt, v.get("vmaf") if use_vmaf else None, enc.key),
                      overwrite)
        job = Job("interpolate", label, out, s)
        tmp = Path(_tmp(settings, job.id))
        inter = _intermediate(settings, lambda codec: eng.direct_encode(tc, codec))
        job.plan = dict(audio_mode=_legacy(aplan), vmaf=use_vmaf, two_pass=two_pass, audio_step=audio_step,
                        engine=eng.key, engine_temp=eng.temp_bytes(), tmp=str(tmp), part=part_path(job.output),
                        intermediate=str(tmp / "intermediate.mkv"), intermediate_enc=inter,
                        video_tmp=str(tmp / f"video{ext}"), audio_tmp=str(tmp / "audio.mka"),
                        segmented=bool(getattr(eng, "supports_segments", False)), audio_out=aplan)
        steps = [Step("analyze", "Analyzing", 1)]
        if audio_step:
            steps.append(Step("audio", "Processing audio", 2))
        steps.append(Step("interpolate", f"Interpolating ({eng.name})", 60 if two_pass else 90))
        if use_vmaf:
            steps.append(Step("vmaf_search", f"VMAF search (target {v['vmaf']:g})", 15))
        if two_pass:
            steps.append(Step("encode", f"Encoding ({enc.label})", 20))
        if audio_step:
            steps.append(Step("mux", "Muxing", 1))

    if ext_w:
        notes.append(ext_w)
    if multi_cut:
        job.plan.update(cut_ranges=[list(r) for r in ranges], cut_lossless=copy_video,
                        cut_audio=aplan["mode"] != "drop" and info.has_audio)
        steps.insert(0, Step("cutjoin", f"Joining {len(ranges)} segments" + (" (lossless)" if copy_video else ""),
                             2 if copy_video else 15))
    if joined:
        tmp = Path(job.plan["tmp"])
        info = replace(info, path=str(tmp / "joined.mkv"))
        job.plan.update(join=True, clips=list(e.clips), join_lossless=lossless_join,
                        join_enc="ref_nvenc" if tc.binary_for("hevc_nvenc") else "ffv1")
        steps.insert(0, Step("join", f"Joining {len(infos)} clips" + ("" if lossless_join else " (converting)"),
                             2 if lossless_join else 20))
    job.plan.update(info=info_to_dict(info), tracks=sel, overwrite=overwrite)
    job.steps = steps + [Step("finalize", "Finishing", 0.5)]
    if aplan.get("warning"):
        notes.append(aplan["warning"])
    job.state["notes"] = notes
    return job
