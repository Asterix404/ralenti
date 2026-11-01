"""The edit model: one video (one or more clips joined in order) and its steps: Trim, Crop & Transform,
Motion, Audio, Output. compile_job() turns an edit into ONE queue job that runs every step in a single pass,
picking the cheapest route: lossless copy, plain edit (no AI) or interpolation."""
from __future__ import annotations

import copy as _copy
from dataclasses import dataclass, field, replace
from pathlib import Path

from .audio_out import plan_audio
from .encoders import ENCODERS
from .engines import resolve_model
from .jobs import Job, Step
from .merge import compat_issues
from .pipeline import BYTES_PER_PIXEL, _prepare, _tmp, info_to_dict, part_path, unique_path
from .probe import probe

STEPS = ("trim", "crop", "motion", "audio", "output")
DEFAULTS = {
    "trim": {"on": False, "start": None, "end": None},
    "crop": {"on": False, "crop": "auto", "rotate": 0, "hflip": False, "vflip": False, "scale": 1.0, "max_h": 0},
    "motion": {"on": True, "model": "auto", "fps_mode": "keep", "fps_value": 0.0, "slowmo": 2.0, "scene": "normal",
               "engine_opts": {}, "cuts_add": [], "cuts_remove": []},
    "audio": {"mode": "auto", "codec": "auto", "quality": "high", "slow": "natural"},
    "output": {"video": "encode", "encoder": "av1_nvenc", "vmaf": 95.0, "q": None, "out_dir": "", "name": ""},
}


def _merge(base: dict, over) -> dict:
    out = _copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k in out:
            out[k] = _copy.deepcopy(v)
    return out


def _d(name):
    return lambda: _copy.deepcopy(DEFAULTS[name])


@dataclass
class Edit:
    clips: list = field(default_factory=list)
    trim: dict = field(default_factory=_d("trim"))
    crop: dict = field(default_factory=_d("crop"))
    motion: dict = field(default_factory=_d("motion"))
    audio: dict = field(default_factory=_d("audio"))
    output: dict = field(default_factory=_d("output"))

    def to_dict(self) -> dict:
        return {"version": 1, "clips": list(self.clips), **{k: _copy.deepcopy(getattr(self, k)) for k in STEPS}}

    @classmethod
    def from_dict(cls, d) -> "Edit":
        d = d or {}
        return cls(clips=list(d.get("clips") or []), **{k: _merge(DEFAULTS[k], d.get(k)) for k in STEPS})

    def copy(self) -> "Edit":
        return Edit.from_dict(self.to_dict())

    def active(self) -> list[str]:
        return [k for k in STEPS if k in ("audio", "output") or getattr(self, k).get("on")]

    def recipe(self) -> dict:
        """The settings without what belongs to one video (clips, trim points, a drawn crop, file name)."""
        d = self.to_dict()
        d.pop("clips")
        d["trim"] = _copy.deepcopy(DEFAULTS["trim"])
        if isinstance(d["crop"].get("crop"), list):
            d["crop"]["crop"] = "auto"
        d["output"]["name"] = ""
        d["motion"]["cuts_add"], d["motion"]["cuts_remove"] = [], []
        return d

    def apply_recipe(self, r: dict) -> None:
        clips, trim = self.clips, self.trim
        new = Edit.from_dict({**(r or {}), "clips": clips})
        for k in STEPS:
            setattr(self, k, getattr(new, k))
        self.trim = trim


def _transform(c: dict):
    if not c.get("on"):
        return "none", 0, False, False, 1.0, 0, False
    crop = list(c["crop"]) if isinstance(c.get("crop"), (list, tuple)) else (c.get("crop") or "none")
    rot = int(c.get("rotate") or 0) % 360
    hf, vf = bool(c.get("hflip")), bool(c.get("vflip"))
    scale, max_h = float(c.get("scale") or 1.0), int(c.get("max_h") or 0)
    filters = crop != "none" or bool(rot) or hf or vf or scale < 0.999 or max_h > 0
    return crop, rot, hf, vf, scale, max_h, filters


def check(e: Edit, tc) -> list[tuple[str, str]]:
    """[(level, message)], level = 'error' (can't queue) or 'warning'."""
    out = []
    if not tc.ffmpeg:
        out.append(("error", "FFmpeg is missing. It's required: get it from Downloads."))
    if not e.clips:
        out.append(("error", "Open a video first."))
    t = e.trim
    if t.get("on") and t.get("start") is not None and t.get("end") is not None \
            and float(t["end"]) <= float(t["start"]):
        out.append(("error", "The trim ends before it starts."))
    m = e.motion
    if m.get("on"):
        if not resolve_model(tc, m.get("model")):
            out.append(("error", "No AI engine yet: install RIFE (free) from Downloads, or turn Motion off."))
        elif float(m.get("slowmo") or 1) == 1 and (m.get("fps_mode") or "keep") == "keep":
            out.append(("error", "Nothing to interpolate: turn on slow motion or pick a higher frame rate."))
    filters = _transform(e.crop)[6]
    o = e.output
    copy_ok = o.get("video") == "copy" and not m.get("on") and not filters
    if o.get("video") == "copy" and not copy_ok:
        out.append(("warning", "The video can't be copied with Motion or Crop & Transform on: it's re-encoded "
                               "with the chosen encoder."))
    if not copy_ok:
        enc = ENCODERS.get(o.get("encoder") or "")
        if enc is None or enc.hidden:
            out.append(("error", "Pick an encoder in Output."))
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


def _output(e: Edit, ext: str, tail: str) -> str:
    src = Path(e.clips[0])
    d = Path(e.output.get("out_dir") or src.parent)
    name = (e.output.get("name") or "").strip()
    return str(unique_path(d / ((name or f"{src.stem}{tail}") + ext)))


def _encoder(o: dict, tc):
    enc = ENCODERS.get(o.get("encoder") or "")
    if enc is None or enc.hidden or not tc.binary_for(enc.codec):
        raise ValueError("The chosen encoder isn't available with your FFmpeg build.")
    return enc


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
    if joined:
        info = replace(info, duration=sum(i.duration for i in infos), has_audio=any(i.has_audio for i in infos),
                       acodec=info.acodec if lossless_join else "flac",
                       sample_rate=info.sample_rate if lossless_join else 48000,
                       channels=info.channels if lossless_join else 2)
    t, c, m, a, o = e.trim, e.crop, e.motion, e.audio, e.output
    start = float(t["start"]) if t.get("on") and t.get("start") else None
    end = float(t["end"]) if t.get("on") and t.get("end") else None
    crop, rot, hf, vf, scale, max_h, filters = _transform(c)
    motion = bool(m.get("on"))
    copy_video = o.get("video") == "copy" and not motion and not filters
    if copy_video and joined and not lossless_join:
        copy_video = False
        notes.append("The clips don't match, so they're converted and the video is re-encoded.")
    label = Path(e.clips[0]).name + (f" + {len(infos) - 1} more" if joined else "")
    spec = dict(src=e.clips[0], start=start, end=end, crop=crop, scale=scale, max_h=max_h,
                transform={"rotate": rot, "hflip": hf, "vflip": vf}, device=str(settings.gpu_device or "0"),
                out_dir=o.get("out_dir") or "", edit=e.to_dict(), fps_mode="keep", fps_value=0.0, slowmo=1.0,
                model=None)

    if copy_video:
        ext = ".mkv" if joined else (Path(e.clips[0]).suffix.lower() or ".mkv")
        aplan = plan_audio(a, info, ext, 1.0, tc)
        job = Job("edit", label, _output(e, ext, "_ralenti"), spec)
        tmp = Path(_tmp(settings, job.id))
        job.plan = dict(remux=True, audio_out=aplan, audio_step=False, tmp=str(tmp), part=part_path(job.output))
        steps = [Step("remux", "Copying (no re-encode)", 1)]

    elif not motion:
        enc = _encoder(o, tc)
        use_vmaf = bool(o.get("vmaf")) and enc.has_quality and tc.has_vmaf
        needs_ref = use_vmaf and bool(filters or start or end or joined)
        aplan = plan_audio(a, info, enc.ext, 1.0, tc, enc.key)
        audio_step = aplan["mode"] != "drop"
        spec.update(encoder=enc.key, vmaf=float(o["vmaf"]) if use_vmaf else None, q=None if use_vmaf else o.get("q"))
        job = Job("edit", label, _output(e, enc.ext, "_ralenti" + (f"_vmaf{o['vmaf']:g}" if use_vmaf else "")), spec)
        tmp = Path(_tmp(settings, job.id))
        inter = _intermediate(settings, tc.binary_for)
        job.plan = dict(tmp=str(tmp), part=part_path(job.output), video_tmp=str(tmp / f"video{enc.ext}"),
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
            steps += [Step("vmaf_search", f"VMAF search (target {o['vmaf']:g})", 20),
                      Step("encode", f"Encoding ({enc.label})", 30)]
        if audio_step:
            steps.append(Step("mux", "Muxing", 1))

    else:
        enc = _encoder(o, tc)
        s = {**spec, "model": m.get("model") or "auto", "fps_mode": m.get("fps_mode") or "keep",
             "fps_value": float(m.get("fps_value") or 0), "slowmo": float(m.get("slowmo") or 1),
             "scene": m.get("scene") or "normal", "engine_opts": dict(m.get("engine_opts") or {}),
             "cuts_add": list(m.get("cuts_add") or []), "cuts_remove": list(m.get("cuts_remove") or []),
             "encoder": enc.key}
        eng = _prepare(tc, s)
        use_vmaf = bool(o.get("vmaf")) and enc.has_quality and tc.has_vmaf
        s["vmaf"], s["q"] = (float(o["vmaf"]) if use_vmaf else None), (None if use_vmaf else o.get("q"))
        two_pass = use_vmaf or not eng.direct_encode(tc, enc.codec)
        aplan = plan_audio(a, info, enc.ext, s["slowmo"], tc, enc.key)
        audio_step = aplan["mode"] != "drop"
        s["audio"] = _legacy(aplan)
        mode_tag = {"multiply": f"_{s['fps_value']:g}x", "fixed": f"_{s['fps_value']:g}fps"}.get(s["fps_mode"], "")
        tail = (f"_ralenti_{s['model']}{mode_tag}" + (f"_slow{s['slowmo']:g}x" if s["slowmo"] != 1 else "")
                + (f"_vmaf{o['vmaf']:g}" if use_vmaf else ""))
        job = Job("interpolate", label, _output(e, enc.ext, tail), s)
        tmp = Path(_tmp(settings, job.id))
        inter = _intermediate(settings, lambda codec: eng.direct_encode(tc, codec))
        job.plan = dict(audio_mode=_legacy(aplan), vmaf=use_vmaf, two_pass=two_pass, audio_step=audio_step,
                        engine=eng.key, engine_temp=eng.temp_bytes(), tmp=str(tmp), part=part_path(job.output),
                        intermediate=str(tmp / "intermediate.mkv"), intermediate_enc=inter,
                        video_tmp=str(tmp / f"video{enc.ext}"), audio_tmp=str(tmp / "audio.mka"),
                        segmented=bool(getattr(eng, "supports_segments", False)), audio_out=aplan)
        steps = [Step("analyze", "Analyzing", 1)]
        if audio_step:
            steps.append(Step("audio", "Processing audio", 2))
        steps.append(Step("interpolate", f"Interpolating ({eng.name})", 60 if two_pass else 90))
        if use_vmaf:
            steps.append(Step("vmaf_search", f"VMAF search (target {o['vmaf']:g})", 15))
        if two_pass:
            steps.append(Step("encode", f"Encoding ({enc.label})", 20))
        if audio_step:
            steps.append(Step("mux", "Muxing", 1))

    if joined:
        tmp = Path(job.plan["tmp"])
        info = replace(info, path=str(tmp / "joined.mkv"))
        job.plan.update(join=True, clips=list(e.clips), join_lossless=lossless_join,
                        join_enc="ref_nvenc" if tc.binary_for("hevc_nvenc") else "ffv1")
        steps.insert(0, Step("join", f"Joining {len(infos)} clips" + ("" if lossless_join else " (converting)"),
                             2 if lossless_join else 20))
    job.plan["info"] = info_to_dict(info)
    job.steps = steps + [Step("finalize", "Finishing", 0.5)]
    if aplan.get("warning"):
        notes.append(aplan["warning"])
    job.state["notes"] = notes
    return job
