from ralenti.core import engines
from ralenti.core.tools import Toolchain


def _tc(tmp_path, names):
    exe = tmp_path / "rife-ncnn-vulkan.exe"
    exe.write_text("")
    for n in names:
        (tmp_path / n).mkdir()
        (tmp_path / n / "flownet.param").write_text("")
    return Toolchain(ffmpeg="f.exe", ffprobe="p.exe", rife=str(exe))


def test_best_model_is_newest_rife(tmp_path):
    assert engines.best_model(_tc(tmp_path, ["rife-v4", "rife-v4.6"])) == "rife-v4.6"


def test_unknown_or_auto_falls_back(tmp_path):
    tc = _tc(tmp_path, ["rife-v4", "rife-v4.6"])
    assert engines.resolve_model(tc, "some-other-model") == "rife-v4.6"
    assert engines.resolve_model(tc, "auto") == "rife-v4.6"
    assert engines.resolve_model(tc, "rife-v4") == "rife-v4"


def test_no_engine():
    assert engines.resolve_model(Toolchain(ffmpeg="f.exe"), "auto") is None
