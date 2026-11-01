from fractions import Fraction
from types import SimpleNamespace

from ralenti.core.commands import build_preview_compose2
from ralenti.core.pipeline import _preview_encoder
from ralenti.core.probe import MediaInfo


def test_preview_original_side_follows_rotation():
    tc = SimpleNamespace(ffmpeg="ffmpeg", has_rubberband=False, binary_for=lambda c: None)
    info = MediaInfo(path="a.mp4", duration=10.0, width=1280, height=720, fps=Fraction(30))
    cmd = build_preview_compose2(tc, info, "ai.mp4", 2.0, "out.mp4", None, _preview_encoder(tc),
                                 fps=Fraction(30), transform="transpose=clock,hflip")
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v:0]transpose=clock,hflip,setpts=" in fc       # the original half is rotated too
    assert "scale=562:1000" in fc                              # portrait, fitted within 1600x1000
