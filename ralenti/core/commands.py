"""Pure FFmpeg argument builders. Nothing here runs a process."""
from __future__ import annotations

from pathlib import Path

from .audio import AudioMode, atempo_chain, audio_codec_args, audio_filter
from .cropdetect import Crop
from .encoders import Encoder
from .probe import MediaInfo
from .tools import Toolchain

SWS = "spline+accurate_rnd+full_chroma_int"
_MATRIX = {"bt709": "bt709", "bt2020nc": "bt2020", "bt2020c": "bt2020",
           "smpte170m": "smpte170m", "bt470bg": "bt470bg", "smpte240m": "smpte240m"}


def _head(ffmpeg: str) -> list[str]:
    return [ffmpeg, "-hide_banner", "-nostdin", "-y", "-nostats", "-progress", "pipe:1"]


def _seek(start: float | None, duration: float | None) -> list[str]:
    a = []
    if start:
        a += ["-ss", f"{start:.3f}"]
    if duration:
        a += ["-t", f"{duration:.3f}"]
    return a


# ---------- colour handling (prevents bt601/bt709 shifts through RGB conversions) ----------
def color_matrix(info: MediaInfo) -> str:
    return _MATRIX.get(info.color_space) or ("bt709" if info.height >= 720 else "smpte170m")


def color_range(info: MediaInfo) -> str:
    return "pc" if info.color_range in ("pc", "jpeg") else "tv"


def color_tags(info: MediaInfo) -> list[str]:
    hd = info.height >= 720
    tags = []
    for flag, val in (("-colorspace", info.color_space), ("-color_primaries", info.color_primaries),
                      ("-color_trc", info.color_trc)):
        val = val or ("bt709" if hd else "")
        if val:
            tags += [flag, val]
    return tags + ["-color_range", color_range(info)]


def _to_rgb(info: MediaInfo, size=None) -> str:
    dims = f"w={size[0]}:h={size[1]}:" if size else ""
    return (f"scale={dims}in_color_matrix={color_matrix(info)}:in_range={color_range(info)}"
            f":flags={SWS},format=rgb48le")


def _to_yuv(info: MediaInfo, pix_fmt: str) -> str:
    return (f"scale=out_color_matrix={color_matrix(info)}:out_range={color_range(info)}"
            f":flags={SWS},format={pix_fmt}")


def _video_out(enc: Encoder, q: int | None, info: MediaInfo, output: str) -> list[str]:
    args = enc.build(q) + color_tags(info) + ["-map_metadata", "0"]
    if Path(output).suffix.lower() in (".mp4", ".mov"):
        if enc.mp4_tag:
            args += ["-tag:v", enc.mp4_tag]
        args += ["-movflags", "+faststart"]
    return args + [str(output)]


def scaled_size(w: int, h: int, scale: float) -> tuple[int, int]:
    if scale >= 0.999:
        return w, h
    return max(64, int(w * scale) // 2 * 2), max(64, int(h * scale) // 2 * 2)


# ---------- audio ----------
def build_audio(tc: Toolchain, info: MediaInfo, mode: AudioMode, slowmo: float, output: str,
                final_ext: str, start: float | None = None, duration: float | None = None) -> list[str]:
    """Audio-only render to a temp .mka. The codec follows the final container."""
    cmd = _head(tc.ffmpeg) + _seek(start, duration) + ["-i", info.path, "-map", "0:a:0", "-vn", "-sn", "-dn"]
    if mode == AudioMode.COPY:
        return cmd + ["-c:a", "copy", output]
    af = audio_filter(mode, slowmo, info.sample_rate, rubberband=tc.has_rubberband)
    if af:
        cmd += ["-af", af]
    return cmd + audio_codec_args(final_ext) + [output]


def build_mux(tc: Toolchain, video: str, audio: str | None, output: str, vtag: str | None = None) -> list[str]:
    cmd = _head(tc.ffmpeg) + ["-i", video] + (["-i", audio] if audio else [])
    cmd += ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio else []) + ["-c", "copy", "-map_metadata", "0"]
    if Path(output).suffix.lower() in (".mp4", ".mov"):
        if vtag:
            cmd += ["-tag:v", vtag]
        cmd += ["-movflags", "+faststart"]
    return cmd + [output]


# ---------- encode / cut / merge ----------
def build_encode(tc: Toolchain, src: str, info: MediaInfo, enc: Encoder, output: str, *,
                 q: int | None = None, audio: list[str] | None = None,
                 start: float | None = None, duration: float | None = None) -> list[str]:
    cmd = _head(tc.binary_for(enc.codec) or tc.ffmpeg) + _seek(start, duration) + ["-i", src, "-map", "0:v:0"]
    cmd += ["-map", "0:a?", *audio] if audio else ["-an"]
    return cmd + _video_out(enc, q, info, output)


def build_cut(tc: Toolchain, src: str, start: float, end: float, output: str) -> list[str]:
    return _head(tc.ffmpeg) + ["-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}",
                               "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", output]


def write_concat_list(files: list[str], list_path: Path) -> Path:
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text("".join("file '" + f.replace("'", "'\\''") + "'\n" for f in files), encoding="utf-8")
    return list_path


def build_merge(tc: Toolchain, list_file: Path, output: str) -> list[str]:
    return _head(tc.ffmpeg) + ["-f", "concat", "-safe", "0", "-i", str(list_file), "-map", "0", "-c", "copy", output]


# ---------- frame-based engines (RIFE) ----------
def rife_extract_cmd(tc: Toolchain, info: MediaInfo, start, duration, crop: Crop | None,
                     size: tuple[int, int] | None):
    """Decoder: constant-rate BGR24 frames on stdout. Returns (cmd, (w, h))."""
    w, h = size or ((crop.w, crop.h) if crop else (info.width, info.height))
    w, h = max(64, w // 2 * 2), max(64, h // 2 * 2)
    fps = info.fps or 30
    rate = f"{fps.numerator}/{fps.denominator}" if hasattr(fps, "numerator") else str(fps)
    chain = [f"fps={rate}"] + ([crop.filter] if crop else []) + [
        f"scale=w={w}:h={h}:in_color_matrix={color_matrix(info)}:in_range={color_range(info)}"
        f":flags={SWS},format=bgr24"]
    cmd = [tc.ffmpeg, "-hide_banner", "-nostdin", "-v", "error"] + _seek(start, duration) + [
        "-i", info.path, "-map", "0:v:0", "-vf", ",".join(chain), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    return cmd, (w, h)


def build_frames_encode(tc: Toolchain, info: MediaInfo, enc: Encoder, output: str, rate, target=None,
                        q: int | None = None) -> list[str]:
    """Encoder fed with PNG frames on stdin."""
    vf = []
    if target and target != rate:
        vf.append(f"fps={target.numerator}/{target.denominator}")
    vf.append(_to_yuv(info, enc.pix_fmt))
    cmd = [tc.binary_for(enc.codec) or tc.ffmpeg, "-hide_banner", "-y", "-nostats", "-f", "image2pipe",
           "-c:v", "png", "-framerate", f"{rate.numerator}/{rate.denominator}", "-i", "-",
           "-vf", ",".join(vf), "-fps_mode", "cfr"]
    return cmd + _video_out(enc, q, info, output)


# ---------- previews ----------
def build_preview_compose(tc: Toolchain, info: MediaInfo, ai_path: str, slowmo: float, video_out: str,
                          audio_outs, enc: Encoder, *, crop: Crop | None = None, fps=None, start=None,
                          duration=None, audio_mode: AudioMode = AudioMode.NATURAL,
                          max_w: int = 1600, max_h: int = 1000) -> list[str]:
    """Preview take: the original (frames repeated) stacked above the AI result, plus two audio
    sidecars: 0 = original stretched to the take's length, 1 = processed with the chosen method."""
    bw, bh = (crop.w, crop.h) if crop else (info.width, info.height)
    k = min(1.0, max_w / bw, max_h / bh)
    w, h = max(64, int(bw * k) // 2 * 2), max(64, int(bh * k) // 2 * 2)
    rate = f"{fps.numerator}/{fps.denominator}"
    tail = f"fps={rate},setpts=N/FRAME_RATE/TB,scale={w}:{h}:flags=lanczos,format=yuv420p"
    head = (crop.filter + ",") if crop else ""
    parts = [f"[0:v:0]{head}setpts=(PTS-STARTPTS)*{slowmo:g},{tail}[b]", f"[1:v:0]{tail}[a]",
             "[b][a]vstack=inputs=2:shortest=1[v]"]
    audio = bool(audio_outs) and info.has_audio
    if audio:
        proc = ("volume=0" if audio_mode == AudioMode.DROP else
                audio_filter(audio_mode, slowmo, info.sample_rate, rubberband=tc.has_rubberband) or "anull")
        parts += ["[0:a:0]asetpts=PTS-STARTPTS,asplit=2[ao0][ap]",
                  f"[ao0]{atempo_chain(1 / float(slowmo))}[ao]", f"[ap]{proc}[aq]"]
    cmd = _head(tc.ffmpeg) + _seek(start, duration) + [
        "-i", info.path, "-i", ai_path, "-filter_complex", ";".join(parts), "-map", "[v]", "-an",
        "-fps_mode", "cfr"] + enc.build() + ["-movflags", "+faststart", video_out]
    if audio:
        for label, path in (("[ao]", audio_outs[0]), ("[aq]", audio_outs[1])):
            cmd += ["-map", label, "-c:a", "aac", "-b:a", "192k", path]
    return cmd
