"""Parses CHANGELOG.md:  ## 1.2.3 · 2026-10-27 · Optional title / ### New / - item"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HEAD = re.compile(r"^##\s+v?(\d+(?:\.\d+)*)\s*(.*)$")
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_SEP = re.compile(r"^[·\-–—|\s]+")


def parse(text: str) -> list[dict]:
    """[{version, date, title, sections: [[name, [items]]]}], newest first as written."""
    entries, cur, cat = [], None, None
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        s = line.strip()
        m = _HEAD.match(s)
        if m:
            rest = _SEP.sub("", m[2])
            date = ""
            if d := _DATE.match(rest):
                date, rest = d[1], _SEP.sub("", rest[len(d[1]):])
            cur, cat = {"version": m[1], "date": date, "title": rest.strip(), "sections": []}, None
            entries.append(cur)
            continue
        if cur is None:
            continue
        if s.startswith("### "):
            cat = [s[4:].strip(), []]
            cur["sections"].append(cat)
        elif s.startswith(("- ", "* ")):
            if cat is None:
                cat = ["Changes", []]
                cur["sections"].append(cat)
            cat[1].append(s[2:].strip())
        elif s and cat and cat[1] and line.startswith("  "):
            cat[1][-1] += " " + s
    for e in entries:
        e["sections"] = [c for c in e["sections"] if c[1]]
    return entries


def path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", "")) / "ralenti" / "resources" / "CHANGELOG.md"
    return Path(__file__).resolve().parents[2] / "CHANGELOG.md"


def load() -> list[dict]:
    try:
        return parse(path().read_text(encoding="utf-8"))
    except OSError:
        return []
