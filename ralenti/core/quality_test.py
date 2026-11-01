"""Quality test for the Encode step: a few seconds of the edited picture (crop, rotation, flip and size applied) are
kept losslessly as a reference, encoded with the chosen encoder and measured with VMAF. With a VMAF target the
quality level is searched the way the render does it. One still is taken from each side for a visual comparison."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import vmaf
from .commands import build_encode
from .probe import probe
from .tools import adapt_cmd, run
from .transform import output_size, rotated, transform_filter

VMAF_SIZE = ((90.0, 0.55), (93.0, 0.78), (95.0, 1.0), (97.0, 1.45), (99.0, 2.4))


class Cancelled(Exception):
    pass


def picture_chain(crop, rotate, hflip, vflip, scale, max_h, w: int, h: int) -> list[str]:
    """FFmpeg filters that give the edited picture: crop (source pixels) -> rotate/flip -> size."""
    chain = []
    if isinstance(crop, (list, tuple)) and len(crop) == 4:
        cw, ch, x, y = (int(v) for v in crop)
        chain.append(f"crop={cw}:{ch}:{x}:{y}")
        w, h = cw, ch
    t = transform_filter(rotate, hflip, vflip)
    if t:
        chain.append(t)
    ow, oh = output_size(w, h, rotate, scale, max_h)
    if (ow, oh) != rotated(w, h, rotate):
        chain.append(f"scale={ow}:{oh}:flags=lanczos")
    return chain


def size_factor(enc, vmaf_target=None, q=None) -> float:
    """Rough file-size change against the encoder's default quality."""
    if enc is None or not enc.has_quality:
        return 1.0
    if vmaf_target:
        v, pts = float(vmaf_target), VMAF_SIZE
        if v <= pts[0][0]:
            return pts[0][1]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if v <= x1:
                return y0 + (y1 - y0) * (v - x0) / (x1 - x0)
        return pts[-1][1]
    if q is not None:
        return 2 ** ((enc.q_default - int(q)) / 6)
    return 1.0


def _run(cmd, cancel, timeout=900):
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    r = run(adapt_cmd(cmd), timeout=timeout)
    if r.returncode != 0:
        tail = [x for x in (r.stderr or "").strip().splitlines() if x.strip()][-1:] or ["FFmpeg failed"]
        raise RuntimeError(tail[0][:300])
    return r


def run_test(tc, info, start: float, dur: float, enc, q, target, chain, work, progress=None, cancel=None) -> dict:
    progress = progress or (lambda _t: None)
    work = Path(work)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    ff = tc.ffmpeg
    if not ff:
        raise RuntimeError("FFmpeg is required. Get it from Downloads.")
    ref = work / "ref.mkv"
    pix = "yuv420p10le" if info.bit_depth > 8 else "yuv420p"
    progress("Preparing a lossless reference")
    cmd = [ff, "-hide_banner", "-nostdin", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
           "-i", info.path, "-map", "0:v:0", "-an", "-sn", "-dn"]
    if chain:
        cmd += ["-vf", ",".join(chain)]
    _run(cmd + ["-c:v", "ffv1", "-level", "3", "-g", "1", "-pix_fmt", pix, str(ref)], cancel)
    ri = probe(tc.ffprobe, str(ref))
    if not ri.width:
        raise RuntimeError("The test reference couldn't be read.")
    rdur = ri.duration or dur
    model = vmaf.model_for(ri.width, ri.height)
    flag = (enc.q_flag or "q").upper()

    def encode(qq):
        out = work / f"enc_{qq if qq is not None else 'd'}{enc.ext}"
        progress(f"Encoding at {flag} {qq}" if qq is not None else f"Encoding with {enc.label}")
        _run(build_encode(tc, str(ref), info, enc, str(out), q=qq), cancel)
        return out

    def score(out):
        progress("Measuring VMAF")
        r = _run(vmaf.build_score(ff, str(out), str(ref), 0, rdur, model, fps=ri.fps or info.fps), cancel)
        return vmaf.parse_score(r.stderr)

    use_vmaf = bool(target) and enc.has_quality and tc.has_vmaf
    files, scores = {}, {}
    if use_vmaf:
        lo, hi = enc.q_range

        def score_at(qq):
            out = encode(qq)
            s = score(out)
            files[qq], scores[qq] = (out, s), s
            return s

        best, _ = vmaf.quality_search(score_at, lo, hi, float(target), (hi - lo + 1).bit_length())
        reached = best is not None
        if best is None:
            best = lo
        if best not in files:
            score_at(best)
        out, s = files[best]
    else:
        best = (q if q is not None else enc.q_default) if enc.has_quality else None
        out = encode(best)
        s = score(out) if tc.has_vmaf else None
        reached = True
    size = out.stat().st_size
    progress("Taking stills")
    mid = f"{rdur / 2:.3f}"
    pngs = []
    for name, src in (("ref", ref), ("enc", out)):
        png = work / f"{name}.png"
        _run([ff, "-hide_banner", "-nostdin", "-y", "-v", "error", "-ss", mid, "-i", str(src), "-frames:v", "1",
              "-pix_fmt", "rgb24", str(png)], cancel)
        pngs.append(str(png))
    for f in [ref, *[x[0] for x in files.values()], out]:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
    return dict(q=best, vmaf=s, reached=reached, searched=use_vmaf, scores=scores, size=size, dur=rdur,
                bps=size / max(rdur, 1e-3), ref_png=pngs[0], enc_png=pngs[1], width=ri.width, height=ri.height)
