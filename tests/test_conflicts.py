from types import SimpleNamespace

from ralenti.core.edit import Edit, compile_job, conflicts


def _tc():
    return SimpleNamespace(ffmpeg="ffmpeg", ffprobe="ffprobe", rife="", binary_for=lambda c: "ffmpeg",
                           has_vmaf=False, has_rubberband=False)


def _edit(**parts):
    e = Edit()
    e.clips = ["a.mp4"]
    for step, kw in parts.items():
        getattr(e, step).update(kw)
    return e


def _errors(e, step=None):
    return [c for c in conflicts(e, _tc()) if c["level"] == "error" and (step is None or c["step"] == step)]


def test_copy_with_interpolation_blocks_with_fixes():
    errs = _errors(_edit(encode={"video": "copy"}, motion={"on": True}), "encode")
    assert errs and ("Re-encode", "encode", {"video": "encode"}) in errs[0]["fixes"]


def test_copy_alone_is_fine():
    assert not _errors(_edit(encode={"video": "copy"}, motion={"on": False}))


def test_container_that_cannot_hold_the_encoder():
    errs = _errors(_edit(motion={"on": False}, encode={"encoder": "av1_nvenc"}, output={"container": "mov"}), "output")
    assert errs and errs[0]["fixes"][0][2] == {"container": "auto"}


def test_keep_original_audio_with_slow_motion():
    e = _edit(motion={"on": True, "slowmo": 2.0}, audio={"mode": "copy"})
    assert _errors(e, "audio")


def test_codec_not_allowed_in_the_container():
    e = _edit(motion={"on": False}, audio={"mode": "convert", "codec": "flac"})
    assert _errors(e, "audio")


def test_compile_refuses_a_conflict():
    import pytest
    with pytest.raises(ValueError):
        compile_job(_tc(), SimpleNamespace(gpu_device="0"), _edit(encode={"video": "copy"}, motion={"on": True}))
