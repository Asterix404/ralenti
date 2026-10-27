from fractions import Fraction

from ralenti.core.pipeline import plan_segments, segment_limits
from ralenti.core.queue import AutoScaler


def test_segments_cover_the_clip_and_frames_add_up():
    segs = plan_segments(1000, 300)
    assert segs[0] == [0, 300] and segs[-1] == [900, 1000]
    assert sum(segment_limits(segs, Fraction(5, 2))) == 2500


def test_autoscaler_tries_one_more_and_steps_back_without_gain():
    s, t = AutoScaler(), 0.0
    for _ in range(12):
        t += 4; s.feed(10.0, 50, 0.3, 0.3, True, None, now=t)
    assert s.level == 2
    for _ in range(12):
        t += 4; s.feed(10.2, 60, 0.4, 0.3, True, None, now=t)
    assert s.level == 1


def test_autoscaler_respects_a_saturated_gpu():
    s, t = AutoScaler(), 0.0
    for _ in range(15):
        t += 4; s.feed(10.0, 50, 0.3, 0.3, True, 95, now=t)
    assert s.level == 1
