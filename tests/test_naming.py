from ralenti.core.naming import render, resolve


def test_render_and_resolve(tmp_path):
    assert render("{name}_{slowmo}x:{res}", {"name": "a", "slowmo": "2", "res": "1920x1080"}) == "a_2x_1920x1080"
    assert render("{unknown}", {}) == "{unknown}"
    (tmp_path / "v_1.mp4").write_text("")
    assert resolve(tmp_path, "v_{n}", ".mp4").name == "v_2.mp4"
    (tmp_path / "x.mp4").write_text("")
    assert resolve(tmp_path, "x", ".mp4").name == "x (1).mp4"
    assert resolve(tmp_path, "x", ".mp4", overwrite=True).name == "x.mp4"
