from fractions import Fraction

from ralenti.core.merge import compat_issues, pick_target
from ralenti.core.probe import MediaInfo


def mi(**k):
    base = dict(path="a.mp4", duration=10, width=1920, height=1080, fps=Fraction(30000, 1001),
                pix_fmt="yuv420p", vcodec="h264", has_audio=True, acodec="aac",
                sample_rate=48000, channels=2)
    return MediaInfo(**{**base, **k})


def test_identical_is_compatible():
    assert compat_issues([mi(), mi()]) == []


def test_resolution_mismatch():
    assert any("Resolution" in x for x in compat_issues([mi(), mi(width=1280, height=720)]))


def test_missing_audio_detected():
    assert any("Audio track" in x for x in compat_issues([mi(), mi(has_audio=False)]))


def test_target_largest():
    t = pick_target([mi(width=1280, height=720), mi()], "largest")
    assert (t.width, t.height) == (1920, 1080)