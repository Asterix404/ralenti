"""What the summary shows: one line per step, warnings and the time estimate."""
from pathlib import Path

from ...core.audio_out import plan_audio
from ...core.edit import _transform, check, pick_ext
from ...core.encoders import ENCODERS
from ...core.engines import all_models, resolve_model
from ...core.estimate import describe, estimate
from ...core.fps import target_fps
from ...core.segments import kept_ranges
from ...core.transform import output_size
from ..util import fmt_ts


def dur(s: float) -> str:
    if s < 60:
        return f"{s:.1f}".rstrip("0").rstrip(".") + " s"
    m, sec = divmod(int(round(s)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def model_label(tc, code) -> str:
    return next((m.label for _e, m in all_models(tc) if m.code == code), code or "No engine")


def analyse(session):
    e, tc = session.edit, session.qm.tc
    info = session.media()
    if info is None:
        return None
    m, v = e.motion, e.encode
    ranges = kept_ranges(e.trim, info.duration) or [(0.0, 0.0)]
    d = sum(b - a for a, b in ranges)
    crop, rot, hf, vf, scale, max_h, filters = _transform(e.crop)
    motion = bool(m.get("on"))
    slow = float(m.get("slowmo") or 1) if motion else 1.0
    copy = v.get("video") == "copy" and not motion and not filters
    multi = len(e.clips) > 1
    enc = ENCODERS.get(v.get("encoder") or "")
    ext, ext_warn = pick_ext(e, None if copy else enc, copy, Path(e.clips[0]).suffix.lower(), multi or len(ranges) > 1)
    bw, bh = (crop[0], crop[1]) if isinstance(crop, list) else (info.width, info.height)
    size = output_size(bw, bh, rot, scale, max_h)
    fps = target_fps(info.fps, m.get("fps_mode") or "keep", m.get("fps_value") or 2.0) if motion else info.fps
    vmaf = v.get("vmaf") if (enc and enc.has_quality and tc.has_vmaf and not copy) else None
    aplan = plan_audio(e.audio, info, ext, slow, tc, enc.key if enc and not copy else "")
    return dict(info=info, ranges=ranges, start=None, end=d, dur=d, out_dur=d * slow, crop=crop, rot=rot, hf=hf,
                vf=vf, filters=filters, motion=motion, slow=slow, copy=copy, multi=multi, enc=enc, ext=ext,
                ext_warn=ext_warn, size=size, fps=fps or info.fps, vmaf=vmaf, aplan=aplan,
                model=resolve_model(tc, m.get("model")) if motion else None)


def rows(session, an) -> dict:
    if an is None:
        return {}
    e, tc, info = session.edit, session.qm.tc, an["info"]
    n = len(e.clips)
    out = {"clips": (f"<b>{Path(e.clips[0]).name}</b>" + (f" + {n - 1} more, joined" if n > 1 else "")
                     + f" · {dur(info.duration)}", True)}
    if e.trim.get("on"):
        r = an["ranges"]
        if len(r) == 1:
            out["trim"] = (f"{fmt_ts(r[0][0], False)} → {fmt_ts(r[0][1], False)} · <b>{dur(an['dur'])}</b>", True)
        else:
            out["trim"] = (f"<b>{len(r)} segments</b> kept and joined · {dur(an['dur'])}", True)
    else:
        out["trim"] = ("Whole video", False)
    w, h = an["size"]
    if e.crop.get("on") and an["filters"]:
        bits, crop = [], an["crop"]
        if isinstance(crop, list):
            bits.append(f"crop {crop[0]}×{crop[1]}")
        elif crop == "auto":
            bits.append("black bars removed")
        if an["rot"]:
            bits.append(f"rotated {an['rot']}°")
        if an["hf"]:
            bits.append("flipped horizontally")
        if an["vf"]:
            bits.append("flipped vertically")
        out["crop"] = (" · ".join(bits + [f"<b>{w}×{h}</b>"]), True)
    else:
        out["crop"] = (f"Original frame · {w}×{h}", False)
    if an["motion"]:
        speed = f"{an['slow']:g}× slower" if an["slow"] != 1 else "real-time"
        out["motion"] = (f"<b>{model_label(tc, an['model'])}</b> · {speed} · {float(an['fps'] or 0):.3f} fps"
                         f" · {dur(an['out_dur'])} long", True)
    else:
        out["motion"] = ("Off · no AI", False)
    out["audio"] = (an["aplan"]["label"], True)
    enc, v, o = an["enc"], e.encode, e.output
    if an["copy"]:
        out["output"] = (f"<b>Copy</b> · no re-encode, lossless · {an['ext']}", True)
    elif enc:
        if an["vmaf"]:
            q = f"VMAF {an['vmaf']:g}, sample tested"
        elif enc.has_quality:
            q = f"constant quality, {enc.q_flag.upper()} {v.get('q') if v.get('q') is not None else enc.q_default}"
        else:
            q = "fixed profile"
        where = o.get("out_dir") or "next to the source"
        out["output"] = (f"<b>{enc.label}</b> · {q} · {an['ext']} · saved {where}", True)
    else:
        out["output"] = ("Pick an encoder", True)
    return out


def messages(session, an) -> list:
    msgs = check(session.edit, session.qm.tc)
    if an and an["aplan"].get("warning"):
        msgs.append(("warning", an["aplan"]["warning"]))
    if an and an.get("ext_warn"):
        msgs.append(("warning", an["ext_warn"]))
    return msgs


def estimate_html(session, an):
    if not an or not an["motion"] or not an["model"]:
        return None
    m = session.edit.motion
    opts = dict(model=an["model"], fps_mode=m.get("fps_mode") or "keep", fps_value=m.get("fps_value") or 0,
                slowmo=an["slow"], encoder=session.edit.encode.get("encoder"), vmaf=an["vmaf"])
    return describe(estimate(an["info"], opts, an["size"], None, an["dur"], session.qm.rates))
