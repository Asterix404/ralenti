from ralenti.core import tools


def test_adapt_renames_and_drops(monkeypatch):
    monkeypatch.setattr(tools, "encoder_options", lambda ff, c: frozenset({"preset", "spatial-aq"}))
    cmd = ["ff", "-f", "image2pipe", "-c:v", "png", "-i", "-", "-c:v", "av1_nvenc", "-preset", "p7",
           "-spatial_aq", "1", "-temporal_aq", "1", "out.mkv"]
    assert tools.adapt_cmd(cmd) == ["ff", "-f", "image2pipe", "-c:v", "png", "-i", "-", "-c:v", "av1_nvenc",
                                    "-preset", "p7", "-spatial-aq", "1", "out.mkv"]