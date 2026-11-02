from ralenti.core.naming import AUTO, render
from ralenti.core.waveform import listen_cmd


def test_labelled_placeholders_vanish_with_their_separator():
    ctx = dict(name="beach", ai="", frames="", slow="", target="vmaf95")
    assert render(AUTO, ctx) == "beach_ralenti_vmaf95"
    ctx.update(ai="rife-v4.6", frames="2x", slow="slow2x")
    assert render(AUTO, ctx) == "beach_ralenti_rife-v4.6_2x_slow2x_vmaf95"
    assert render("{slow}_{name}", dict(name="b", slow="")) == "b"
    assert render("{name}_x", dict(name="b")) == "b_x"


def test_listen_cmd_joins_kept_parts():
    cmd = listen_cmd("ffmpeg", "a.mp4", 1, [(0, 2), (5, 6)], "atempo=0.5", "o.wav")
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:a:1]asplit=2[i0][i1]" in fc and "concat=n=2:v=0:a=1[c]" in fc and fc.endswith("[c]atempo=0.5[o]")
    assert cmd[cmd.index("-map") + 1] == "[o]"
    one = listen_cmd("ffmpeg", "a.mp4", 0, [(1, 3)], None, "o.wav")
    assert one[one.index("-map") + 1] == "[s0]"
