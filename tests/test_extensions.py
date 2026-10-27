from ralenti.core import config, extensions


def test_install_and_remove(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXT_DIR", tmp_path / "ext")
    src = tmp_path / "repo" / "ralenti_ext"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("def register(api): pass\n")
    extensions.install(str(tmp_path / "repo"))
    assert extensions.installed()
    extensions.remove()
    assert not extensions.installed()
