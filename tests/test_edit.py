from fractions import Fraction

import pytest

from ralenti.core import edit as E
from ralenti.core import tools
from ralenti.core.audio_out import plan_audio
from ralenti.core.config import Settings
from ralenti.core.probe import MediaInfo
from ralenti.core.tools import Caps, Toolchain
from ralenti.core.transform import output_size, transform_filter


def info(path="a.mp4", w=1920, h=1080, **k):
    base = dict(path=path, duration=10.0, width=w, height=h, fps=Fraction(30000, 1001), pix_fmt="yuv420p",
                vcodec="h264", has_audio=True, acodec="aac", sample_rate=48000, channels=2)
    return MediaInfo(**{**base, **k})


@pytest.fixture
def tc(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "caps", lambda ff: Caps(
        frozenset({"libvmaf", "cropdetect", "scdet", "rubberband"}),
        frozenset({"av1_nvenc", "hevc_nvenc", "h264_nvenc", "libopus", "prores_ks", "ffv1", "libsvtav1"})))
    exe = tmp_path / "rife-ncnn-vulkan.exe"
    exe.write_text("")
    (tmp_path / "rife-v4.6").mkdir()
    (tmp_path / "rife-v4.6" / "flownet.param").write_text("")
    return Toolchain(ffmpeg="ffmpeg.exe", ffprobe="ffprobe.exe", rife=str(exe))


def build(monkeypatch, tc, infos, **parts):
    m = {i.path: i for i in infos}
    monkeypatch.setattr(E, "probe", lambda _fp, p: m[p])
    e = E.Edit(clips=[i.path for i in infos])
    for k, v in parts.items():
        getattr(e, k).update(v)
    return E.compile_job(tc, Settings(), e)


def kinds(job):
    return [s.kind for s in job.steps]


def test_transforms():
    assert transform_filter(90) == "transpose=clock"
    assert transform_filter(270) == "transpose=cclock"
    assert transform_filter(180, hflip=True) == "hflip,vflip,hflip"
    assert output_size(1920, 1080, 90) == (1080, 1920)
    assert output_size(3840, 2160, 0, 1.0, 720) == (1280, 720)
    assert output_size(1920, 1080, 90, 1.0, 1280) == (720, 1280)


def test_audio_plans(tc):
    assert plan_audio({"mode": "auto"}, info(), ".mp4", 1.0, tc)["mode"] == "copy"
    slow = plan_audio({"mode": "auto"}, info(), ".mp4", 2.0, tc)
    assert slow["codec"] == "aac" and slow["filter"].startswith("asetrate")
    assert plan_audio({"mode": "convert"}, info(), ".mkv", 1.0, tc)["codec"] == "opus"
    assert plan_audio({"mode": "convert"}, info(), ".mov", 1.0, tc, "prores")["codec"] == "pcm"
    pcm = plan_audio({"mode": "convert", "codec": "pcm"}, info(), ".mp4", 1.0, tc)
    assert pcm["codec"] == "aac" and pcm["warning"]
    assert plan_audio({"mode": "remove"}, info(), ".mp4", 1.0, tc)["mode"] == "drop"


def test_edit_roundtrip():
    e = E.Edit(clips=["a.mp4"])
    e.crop["rotate"] = 90
    assert E.Edit.from_dict(e.to_dict()) == e
    assert E.Edit.from_dict({"clips": ["x"], "audio": {"mode": "remove", "bogus": 1}}).audio["mode"] == "remove"


def test_trim_only_copies(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], trim={"on": True, "start": 2.0, "end": 6.0}, motion={"on": False},
                output={"video": "copy"})
    assert kinds(job) == ["remux", "finalize"]
    assert job.plan["audio_out"]["mode"] == "copy" and job.output.endswith(".mp4")


def test_rotate_with_vmaf_prepares_a_reference(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], crop={"on": True, "crop": "none", "rotate": 90}, motion={"on": False})
    assert kinds(job) == ["analyze", "audio", "render", "vmaf_search", "encode", "mux", "finalize"]
    assert job.plan["vmaf_ref"]


def test_plain_reencode_uses_the_source(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], motion={"on": False})
    assert kinds(job) == ["analyze", "audio", "vmaf_search", "encode", "mux", "finalize"]


def test_no_audio_constant_quality(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], motion={"on": False}, audio={"mode": "remove"}, output={"vmaf": 0})
    assert kinds(job) == ["analyze", "render", "finalize"]


def test_joined_clips_with_motion(monkeypatch, tc):
    job = build(monkeypatch, tc, [info(), info("b.mp4", 1280, 720)])
    k = kinds(job)
    assert job.kind == "interpolate" and k[0] == "join" and k.index("audio") < k.index("interpolate")
    assert job.plan["join_lossless"] is False and job.plan["info"]["path"].endswith("joined.mkv")
    same = build(monkeypatch, tc, [info(), info("c.mp4")])
    assert same.plan["join_lossless"] is True
