"""What the summary shows: one line per step, warnings, the estimate and the output file name."""
from datetime import datetime
from pathlib import Path

from ...core.audio_out import plan_audio
from ...core.edit import _transform, check, pick_ext
from ...core.encoders import ENCODERS
from ...core.engines import all_models, resolve_model
from ...core.estimate import describe, estimate
from ...core.fps import target_fps
from ...core.naming import render, resolve
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
    return dict(info=info, ranges=ranges, dur=d, out_dur=d * slow, crop=crop, rot=rot, hf=hf, vf=vf,
                filters=filters, motion=motion, slow=slow, copy=copy, multi=multi, enc=enc, ext=ext,
                ext_warn=ext_warn, size=size, fps=fps or info.fps, vmaf=vmaf, aplan=aplan,
                model=resolve_model(tc, m.get("model")) if motion else None)


def name_context(session, an) -> dict:
    e, enc, (w, h), now = session.edit, an["enc"], an["size"], datetime.now()
    f = float(an["fps"] or 0)
    return {"name": Path(e.clips[0]).stem, "model": an["model"] or "none", "slowmo": f"{an['slow']:g}",
            "fps": f"{f:.3f}".rstrip("0").rstrip("."), "res": f"{w}x{h}",
            "encoder": "copy" if an["copy"] else (enc.key if enc else ""),
            "vmaf": f"{an['vmaf']:g}" if an["vmaf"] else "cq", "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H-%M")}


def preview_output(session, an) -> Path:
    e, o = session.edit, session.edit.output
    src = Path(e.clips[0])
    folder = Path(o.get("out_dir") or src.parent)
    tpl = (o.get("name") or "").strip()
    base = render(tpl, name_context(session, an)) if tpl else ""
    if not base:
        base = (src.stem + "_ralenti" + (f"_{an['model']}" if an["motion"] else "")
                + (f"_slow{an['slow']:g}x" if an["slow"] != 1 else "") + (f"_vmaf{an['vmaf']:g}" if an["vmaf"] else ""))
    return resolve(folder, base, an["ext"], o.get("exists") == "overwrite")


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
    enc, v = an["enc"], e.encode
    if an["copy"]:
        out["encode"] = ("<b>Copy</b> · no re-encode, lossless", True)
    elif enc:
        if an["vmaf"]:
            q = f"VMAF {an['vmaf']:g}, sample tested"
        elif enc.has_quality:
            q = f"constant quality, {enc.q_flag.upper()} {v.get('q') if v.get('q') is not None else enc.q_default}"
        else:
            q = "fixed profile"
        out["encode"] = (f"<b>{enc.label}</b> · {q}", True)
    else:
        out["encode"] = ("Pick an encoder", True)
    p = preview_output(session, an)
    out["output"] = (f"<b>{p.name}</b> · in {p.parent.name or p.parent}", True)
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



# ===================== 0.14: problems with fixes =====================
def issues(session, an) -> list[dict]:
    from ...core.edit import conflicts
    e = session.edit
    infos = [session.info(c) for c in e.clips] if e.clips else None
    out = conflicts(e, session.qm.tc, infos)
    blocked = {c["step"] for c in out if c["level"] == "error"}
    if an and an["aplan"].get("warning") and "audio" not in blocked:
        out.append(dict(level="warning", step="audio", text=an["aplan"]["warning"], fixes=[]))
    return out


_rows_v13 = rows


def rows(session, an) -> dict:
    out = _rows_v13(session, an)
    if an and session.edit.encode.get("video") == "copy" and not an["copy"]:
        from .. import theme
        out["encode"] = (f"<span style='color:{theme.DANGER}'><b>Copy</b> · not possible with these settings"
                         f"</span>", True)
    return out
