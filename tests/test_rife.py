from fractions import Fraction

from ralenti.core.rife import plan, scan_models, write_bmp
from ralenti.core.tools import Toolchain


def test_plan_slowmo_keeps_source_rate():
    assert plan(Fraction(30000, 1001), Fraction(30000, 1001), 2.0) == (2, Fraction(30000, 1001))


def test_plan_film_to_60_is_exact():
    m, rate = plan(Fraction(24000, 1001), Fraction(60000, 1001), 1.0)
    assert m == 5 and rate == Fraction(120000, 1001)


def test_bmp_rows_are_padded(tmp_path):
    p = tmp_path / "a.bmp"
    write_bmp(p, 2, 2, bytes(12))
    data = p.read_bytes()
    assert data[:2] == b"BM" and len(data) == 54 + 2 * 8


def test_scan_lists_v4_models_newest_first(tmp_path):
    exe = tmp_path / "rife-ncnn-vulkan.exe"; exe.write_text("")
    for name in ("rife-v4", "rife-v4.6", "rife-anime"):
        (tmp_path / name).mkdir(); (tmp_path / name / "flownet.param").write_text("")
    assert [m.code for m in scan_models(str(exe))] == ["rife-v4.6", "rife-v4"]


def test_ffmpeg_required_engine_optional():
    problems = Toolchain().problems()
    assert any("FFmpeg" in p for p in problems) and any("AI engine" in p for p in problems)
    assert Toolchain(ffmpeg="f.exe", ffprobe="p.exe", rife="r.exe").problems() == []