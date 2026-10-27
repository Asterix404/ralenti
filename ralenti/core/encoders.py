"""Encoder profiles. '{q}' is replaced with the quality value (CQ/CRF)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Encoder:
    key: str
    label: str
    codec: str
    ext: str
    args: tuple[str, ...]
    pix_fmt: str
    q_flag: str = ""
    q_default: int = 0
    q_range: tuple[int, int] = (0, 0)
    mp4_tag: str = ""
    blurb: str = ""
    tag: str = ""
    hidden: bool = False

    @property
    def has_quality(self) -> bool:
        return bool(self.q_flag)

    def build(self, q: int | None = None) -> list[str]:
        q = self.q_default if q is None else q
        return [a.replace("{q}", str(q)) for a in self.args] + ["-pix_fmt", self.pix_fmt]


_NV = ("-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "{q}", "-b:v", "0",
       "-rc-lookahead", "32", "-spatial_aq", "1")

ENCODERS: dict[str, Encoder] = {e.key: e for e in [
    Encoder("av1_nvenc", "AV1 · NVENC", "av1_nvenc", ".mp4",
            ("-c:v", "av1_nvenc", *_NV, "-multipass", "fullres"), "p010le", "cq", 28, (18, 45),
            blurb="Small files, very fast on RTX 40. Plays in modern browsers, phones and TVs.",
            tag="GPU · Recommended"),
    Encoder("hevc_nvenc", "HEVC · NVENC 10-bit", "hevc_nvenc", ".mp4",
            ("-c:v", "hevc_nvenc", *_NV, "-temporal_aq", "1", "-multipass", "fullres"),
            "p010le", "cq", 22, (14, 36), mp4_tag="hvc1",
            blurb="Very fast and widely supported (Apple, TVs). About 20–30% larger than AV1.",
            tag="GPU · Compatible"),
    Encoder("h264_nvenc", "H.264 · NVENC", "h264_nvenc", ".mp4",
            ("-c:v", "h264_nvenc", "-profile:v", "high", *_NV, "-temporal_aq", "1"),
            "yuv420p", "cq", 19, (12, 32),
            blurb="Plays on anything, including old devices. Largest of the GPU options.",
            tag="GPU · Plays everywhere"),
    Encoder("svtav1", "AV1 · SVT", "libsvtav1", ".mp4",
            ("-c:v", "libsvtav1", "-preset", "5", "-crf", "{q}"), "yuv420p10le", "crf", 30, (15, 50),
            blurb="Smallest files at the same quality. CPU encode, several times slower.",
            tag="CPU · Smallest file"),
    Encoder("x265", "HEVC · x265", "libx265", ".mp4",
            ("-c:v", "libx265", "-preset", "slow", "-crf", "{q}"), "yuv420p10le", "crf", 20, (12, 30),
            mp4_tag="hvc1",
            blurb="Excellent quality per MB with HEVC compatibility. Slow CPU encode.",
            tag="CPU · Efficient"),
    Encoder("prores", "ProRes 422 HQ", "prores_ks", ".mov",
            ("-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0"), "yuv422p10le",
            blurb="For editing in Premiere, Resolve or Final Cut. Huge files, smooth scrubbing.",
            tag="Editing"),
    Encoder("ffv1", "FFV1 lossless", "ffv1", ".mkv",
            ("-c:v", "ffv1", "-level", "3", "-slices", "24", "-slicecrc", "1", "-g", "1"),
            "yuv420p10le",
            blurb="Mathematically lossless archive. Enormous files; few players support it.",
            tag="Archive"),
    Encoder("ref_nvenc", "HEVC NVENC near-lossless (intermediate)", "hevc_nvenc", ".mkv",
            ("-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "constqp",
             "-qp", "4", "-bf", "0"), "p010le", hidden=True),
]}

INTERMEDIATE = "ffv1"