from ralenti.core import edit_presets as P
from ralenti.core.edit import Edit


def test_diff_and_partial_apply_keep_per_video_choices():
    e = Edit(clips=["a.mp4"])
    e.trim.update(on=True, cuts=[2.0], drop=[0])
    e.crop["crop"] = [100, 100, 0, 0]
    e.output["name"] = "keep"
    r = P.get("Slow-mo 4×")
    motion = next(x for x in P.diff(e, r) if x["step"] == "motion")
    assert ("Slow motion", "2×", "4×") in motion["changes"]
    P.apply(e, P.get("Edit-ready ProRes"), ["audio"])
    assert e.audio["codec"] == "pcm" and e.encode["encoder"] == "av1_nvenc"
    P.apply(e, r)
    assert e.motion["slowmo"] == 4.0
    assert e.trim["cuts"] == [2.0] and e.crop["crop"] == [100, 100, 0, 0] and e.output["name"] == "keep"


def test_legacy_conversion():
    r = P.from_legacy({"model": "apo-8", "slowmo": 4, "audio": "keep_pitch", "encoder": "x265", "vmaf": 0,
                       "crop": "none", "scale": 0.5})
    assert r["motion"]["model"] == "apo-8" and r["audio"]["slow"] == "keep_pitch"
    assert r["encode"]["vmaf"] is None and r["crop"]["on"] is True and r["crop"]["scale"] == 0.5
