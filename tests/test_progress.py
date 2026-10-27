from ralenti.core.progress import ProgressParser, fmt_eta


def test_block():
    p = ProgressParser()
    snap = None
    for line in ["frame=120", "fps=9.8", "out_time_us=2000000", "speed=0.16x", "progress=continue"]:
        snap = p.feed(line) or snap
    assert snap.out_time == 2.0 and snap.frame == 120
    assert abs(snap.speed - 0.16) < 1e-9 and not snap.done


def test_na_falls_back_to_hms():
    p = ProgressParser()
    p.feed("out_time_us=N/A"); p.feed("out_time_ms=N/A"); p.feed("out_time=00:00:01.500000")
    assert p.feed("progress=end").out_time == 1.5


def test_eta():
    assert fmt_eta(75) == "1:15" and fmt_eta(3725) == "1:02:05" and fmt_eta(-1) == ""