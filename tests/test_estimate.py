from fractions import Fraction

from ralenti.core.estimate import estimate
from ralenti.core.probe import MediaInfo

B = {"models": {"rife-v4.6": {"mpfs": 30.0}}, "encoders": {"av1_nvenc": 300.0}, "vmaf": 200.0}
O = dict(model="rife-v4.6", fps_mode="keep", slowmo=2.0, encoder="av1_nvenc", vmaf=None)


def _i():
    return MediaInfo(path="a.mp4", duration=10, width=1920, height=1080, fps=Fraction(30000, 1001))


def _t(o, size):
    return estimate(_i(), o, size, rates={}, bench=B)[0] - 5


def test_scale_and_slowmo():
    full = _t(O, (1920, 1080))
    assert abs(_t(O, (960, 540)) - full / 4) < 0.5
    assert abs(_t({**O, "slowmo": 4.0}, (1920, 1080)) - 2 * full) < 0.5
    assert _t({**O, "vmaf": 95.0}, (1920, 1080)) > full


def test_past_jobs_win_and_unknown_model():
    assert estimate(_i(), O, (1920, 1080), rates={"interpolate:rife-v4.6:direct": 2.0}, bench=B)[1] == "past"
    assert estimate(_i(), {**O, "model": "x"}, (1920, 1080), rates={}, bench=B) is None
