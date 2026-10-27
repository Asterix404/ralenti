from ralenti.core.changelog import parse
from ralenti.core.updates import _body, is_newer


def test_parse_sections_and_titles():
    e = parse("# Changelog\n\n## 0.9.4 · 2026-10-27\n### New\n- **A** thing\n  continued\n### Fixed\n- b\n\n"
              "## 0.9.0 · 2026-10-01 · First public release\n- c\n")
    assert e[0]["version"] == "0.9.4" and e[0]["date"] == "2026-10-27"
    assert e[0]["sections"][0] == ["New", ["**A** thing continued"]]
    assert e[1]["title"] == "First public release" and e[1]["sections"][0][0] == "Changes"


def test_release_body_gets_a_version_header():
    assert _body({"tag_name": "v1.2.3", "body": "- x"}).startswith("## 1.2.3")
    assert is_newer("v0.10.0", "0.9.9")
