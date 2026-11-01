from ralenti.core.pipeline import plan_segments, segment_limits
from ralenti.core.scenes import apply_manual, parse

LOG = ("[scdet @ 0x1] lavfi.scd.score: 31.2, lavfi.scd.time: 2.0\n"
       "[scdet @ 0x1] lavfi.scd.score: 14.0, lavfi.scd.time: 2.1\n"
       "[scdet @ 0x1] lavfi.scd.score: 40.0, lavfi.scd.time: 7.5\n")


def test_parse_merges_flashes():
    assert parse(LOG, 30.0, 300, 12) == [60, 225]


def test_segments_split_at_cuts_and_cover_the_clip():
    segs = plan_segments(1000, 300, [100, 640])
    assert segs[0][0] == 0 and segs[-1][1] == 1000
    ends = {b for _a, b in segs}
    assert 100 in ends and 640 in ends
    assert all(b - a <= 300 for a, b in segs)
    assert all(a2 == b1 for (_a1, b1), (a2, _b2) in zip(segs, segs[1:]))


def test_without_cuts_parts_are_even():
    segs = plan_segments(1000, 300)
    assert len(segs) == 4 and segs[-1][1] == 1000


def test_manual_edits():
    assert apply_manual([60, 225], [5.0], [2.0], 30.0, 300) == [150, 225]


def test_limits_add_up():
    from fractions import Fraction
    segs = plan_segments(1000, 300, [100])
    assert sum(segment_limits(segs, Fraction(5, 2))) == 2500
