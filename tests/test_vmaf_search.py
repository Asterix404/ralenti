from ralenti.core.vmaf import parse_score, quality_search, sample_windows


def test_finds_highest_q_meeting_target():
    best, _ = quality_search(lambda q: 100 - q * 0.5, 0, 50, 95.0)
    assert best == 10


def test_unreachable_returns_none():
    best, _ = quality_search(lambda q: 80.0, 10, 40, 95.0)
    assert best is None


def test_parse_score():
    assert parse_score("[Parsed_libvmaf_4 @ 0x1] VMAF score: 96.412345\n") == 96.412345


def test_windows():
    assert sample_windows(12) == [(0.0, 12)]
    assert len(sample_windows(600)) == 5