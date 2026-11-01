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


def tr(codec, lang=""):
    return dict(n=0, codec=codec, channels=2, sample_rate=48000, lang=lang, title="", label="")


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


def build(monkeypatch, tc, infos, tracks=None, **parts):
    m = {i.path: i for i in infos}
    monkeypatch.setattr(E, "probe", lambda _fp, p: m[p])
    monkeypatch.setattr(E, "audio_tracks", lambda _fp, p: tracks if tracks is not None else [tr("aac")])
    monkeypatch.setattr(E, "keyframes", lambda _fp, p: [0.0, 2.0, 4.0, 6.0, 8.0])
    e = E.Edit(clips=[i.path for i in infos])
    for k, v in parts.items():
        getattr(e, k).update(v)
    return E.compile_job(tc, Settings(), e)


def kinds(job):
    return [s.kind for s in job.steps]


def test_transforms():
    assert transform_filter(90) == "transpose=clock"
    assert output_size(1920, 1080, 90) == (1080, 1920)
    assert output_size(3840, 2160, 0, 1.0, 720) == (1280, 720)


def test_audio_plans(tc):
    assert plan_audio({"mode": "auto"}, info(), ".mp4", 1.0, tc)["mode"] == "copy"
    assert plan_audio({"mode": "convert"}, info(), ".mkv", 1.0, tc)["codec"] == "opus"
    pcm = plan_audio({"mode": "convert", "codec": "pcm"}, info(), ".mp4", 1.0, tc)
    assert pcm["codec"] == "aac" and pcm["warning"]


def test_roundtrip_and_v1_migration():
    e = E.Edit(clips=["a.mp4"])
    e.crop["rotate"] = 90
    assert E.Edit.from_dict(e.to_dict()) == e
    old = E.Edit.from_dict({"clips": ["a"], "output": {"encoder": "x265", "vmaf": 0, "name": "n"}})
    assert old.encode["encoder"] == "x265" and old.output["name"] == "n" and "encoder" not in old.output


def test_single_trim_copies(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], trim={"on": True, "start": 2.0, "end": 6.0}, motion={"on": False},
                encode={"video": "copy"})
    assert kinds(job) == ["remux", "finalize"] and job.output.endswith(".mp4")


def test_segments_copy_losslessly(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], trim={"on": True, "cuts": [2, 4, 6], "drop": [1]},
                motion={"on": False}, encode={"video": "copy"})
    assert kinds(job) == ["cutjoin", "remux", "finalize"]
    assert job.plan["cut_lossless"] and job.plan["cut_ranges"] == [[0.0, 2.0], [4.0, 10.0]]
    assert job.output.endswith(".mkv")


def test_segments_with_interpolation_mark_join_points(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], trim={"on": True, "cuts": [2, 4], "drop": [1]})
    assert job.kind == "interpolate" and kinds(job)[0] == "cutjoin"
    assert job.spec["start"] is None and 2.0 in job.spec["cuts_add"]


def test_all_removed_is_an_error(monkeypatch, tc):
    with pytest.raises(ValueError):
        build(monkeypatch, tc, [info()], trim={"on": True, "cuts": [5], "drop": [0, 1]})


def test_rotate_with_vmaf_prepares_a_reference(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], crop={"on": True, "crop": "none", "rotate": 90}, motion={"on": False})
    assert kinds(job) == ["analyze", "audio", "render", "vmaf_search", "encode", "mux", "finalize"]


def test_no_audio_constant_quality(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], motion={"on": False}, audio={"mode": "remove"}, encode={"vmaf": 0})
    assert kinds(job) == ["analyze", "render", "finalize"]


def test_template_container_overwrite(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], encode={"encoder": "hevc_nvenc", "vmaf": 0},
                output={"name": "{name}_{slowmo}x_{res}", "container": "mkv", "exists": "overwrite"})
    assert job.output.endswith("a_2x_1920x1080.mkv") and job.plan["overwrite"]


def test_bad_container_falls_back(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], output={"container": "mov"})
    assert job.output.endswith(".mp4") and any("can't be stored" in n for n in job.state["notes"])


def test_audio_tracks(monkeypatch, tc):
    job = build(monkeypatch, tc, [info()], tracks=[tr("aac", "eng"), tr("ac3", "fra")], audio={"tracks": [1]},
                motion={"on": False}, encode={"video": "copy"})
    assert job.plan["tracks"] == [1] and job.plan["audio_out"]["mode"] == "copy"


def test_joined_clips_with_interpolation(monkeypatch, tc):
    job = build(monkeypatch, tc, [info(), info("b.mp4", 1280, 720)])
    k = kinds(job)
    assert job.kind == "interpolate" and k[0] == "join" and k.index("audio") < k.index("interpolate")
    assert job.plan["join_lossless"] is False and job.plan["info"]["path"].endswith("joined.mkv")
