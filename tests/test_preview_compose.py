from fractions import Fraction

from ralenti.core.commands import build_preview_compose
from ralenti.core.encoders import ENCODERS
from ralenti.core.probe import MediaInfo
from ralenti.core.tools import Toolchain


def test_original_audio_is_stretched_and_video_stacked():
    info = MediaInfo(path="a.mp4", duration=10, width=1920, height=1080, fps=Fraction(30000, 1001),
                     pix_fmt="yuv420p", has_audio=True, acodec="aac", sample_rate=48000, channels=2)
    cmd = build_preview_compose(Toolchain(ffmpeg="ffmpeg.exe"), info, "ai.mp4", 2.0, "v.mp4", ["o.m4a", "p.m4a"],
                                ENCODERS["h264_nvenc"], fps=Fraction(30000, 1001), start=1.0, duration=3.0)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[ao0]atempo=0.5[ao]" in graph and "vstack" in graph
    assert cmd[-1] == "p.m4a"
