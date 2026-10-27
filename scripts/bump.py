"""Usage: python scripts/bump.py 0.9.5
Sets the version in pyproject.toml and ralenti/__init__.py and adds a CHANGELOG section to fill in."""
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
v = (sys.argv[1] if len(sys.argv) > 1 else "").lstrip("vV")
if not re.fullmatch(r"\d+\.\d+\.\d+", v):
    sys.exit("Usage: python scripts/bump.py X.Y.Z")
for rel, pat in (("pyproject.toml", r'(?m)^version = "[^"]+"'), ("ralenti/__init__.py", r'(?m)^__version__ = "[^"]+"')):
    f = ROOT / rel
    f.write_text(re.sub(pat, lambda m: m.group(0).split("=")[0] + f'= "{v}"', f.read_text(encoding="utf-8"), 1),
                 encoding="utf-8")
c = ROOT / "CHANGELOG.md"
t = c.read_text(encoding="utf-8")
if not re.search(rf"(?m)^##\s+v?{re.escape(v)}\b", t):
    c.write_text(t.replace("# Changelog\n\n", f"# Changelog\n\n## {v} · {datetime.date.today()}\n### New\n- \n"
                                              f"### Improved\n- \n### Fixed\n- \n\n", 1), encoding="utf-8")
print(f"Version {v}. Fill in its section in CHANGELOG.md (delete empty headings), then commit and tag v{v}.")
