"""Rotate / flip / output size. Order in every pipeline: crop (source coordinates) -> rotate/flip -> scale."""
from __future__ import annotations


def transform_filter(rotate=0, hflip=False, vflip=False) -> str:
    r = int(rotate or 0) % 360
    f = {90: ["transpose=clock"], 180: ["hflip", "vflip"], 270: ["transpose=cclock"]}.get(r, [])
    if hflip:
        f.append("hflip")
    if vflip:
        f.append("vflip")
    return ",".join(f)


def rotated(w: int, h: int, rotate=0) -> tuple[int, int]:
    return (h, w) if int(rotate or 0) % 360 in (90, 270) else (w, h)


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def output_size(w: int, h: int, rotate=0, scale=1.0, max_h=0) -> tuple[int, int]:
    """Final picture size: rotated, fitted within max_h (if set), then scaled."""
    w, h = rotated(w, h, rotate)
    k = 1.0
    if max_h and h > max_h:
        k = max_h / h
    k *= float(scale or 1.0)
    if k >= 0.999:
        return w, h
    return _even(w * k), _even(h * k)


def video_chain(st: dict) -> list[str]:
    """FFmpeg filters for a job's analysed state (crop, transform, size)."""
    chain = []
    if st.get("crop"):
        w, h, x, y = st["crop"]
        chain.append(f"crop={w}:{h}:{x}:{y}")
    if st.get("transform"):
        chain.append(st["transform"])
    if st.get("size"):
        w, h = st["size"]
        chain.append(f"scale={w}:{h}:flags=lanczos")
    return chain
