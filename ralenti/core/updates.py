"""Update checks and in-app updates from GitHub releases."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .tools import NO_WINDOW

API = "https://api.github.com/repos/{repo}/releases?per_page=20"


@dataclass
class Update:
    version: str
    url: str
    notes: str                     # markdown for every version newer than the installed one
    date: str = ""
    assets: dict = field(default_factory=dict)   # "installer" / "portable" -> name, url, size, digest


def _ver(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def is_newer(a: str, b: str) -> bool:
    return _ver(a) > _ver(b)


def _fetch(repo: str, timeout: float):
    repo = repo.strip().strip("/")
    if repo.count("/") != 1:
        raise ValueError("Release source must look like owner/name")
    req = urllib.request.Request(API.format(repo=repo), headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Ralenti"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("no public release found. The repository is private or has no releases yet.") from None
        if e.code in (403, 429):
            raise RuntimeError("GitHub's rate limit was reached. Try again in an hour.") from None
        raise RuntimeError(f"GitHub returned HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"no connection ({e.reason})") from None


def _body(r: dict) -> str:
    tag = (r.get("tag_name") or "").lstrip("vV")
    body = (r.get("body") or "").replace("\r\n", "\n").strip()
    if not re.search(r"(?m)^##\s+v?\d", body):
        body = f"## {tag}\n{body}"
    return body


def check(repo: str, current: str, timeout: float = 8) -> Update | None:
    rels = [r for r in _fetch(repo, timeout) if not r.get("draft") and not r.get("prerelease")]
    newer = sorted((r for r in rels if is_newer(r.get("tag_name", ""), current)),
                   key=lambda r: _ver(r.get("tag_name", "")), reverse=True)
    if not newer:
        return None
    latest = newer[0]
    assets = {}
    for a in latest.get("assets", []):
        n = a.get("name", "").lower()
        info = {"name": a.get("name", ""), "url": a.get("browser_download_url", ""),
                "size": int(a.get("size") or 0), "digest": a.get("digest") or ""}
        if n.endswith("-setup.exe"):
            assets["installer"] = info
        elif n.endswith("-portable.zip"):
            assets["portable"] = info
    return Update(latest.get("tag_name", "").lstrip("vV"), latest.get("html_url", ""),
                  "\n\n".join(_body(r) for r in newer), (latest.get("published_at") or "")[:10], assets)


def install_mode() -> str:
    """'installer' (setup.exe install), 'portable' (unzipped) or 'source' (python -m ralenti)."""
    if not getattr(sys, "frozen", False):
        return "source"
    d = Path(sys.executable).parent
    return "installer" if any(d.glob("unins*.exe")) else "portable"


def download(asset: dict, dest, progress, stage, cancel) -> Path:
    """progress(done, total, MB/s), stage('verify'). Raises downloads.Cancelled when cancelled."""
    from .downloads import _download, _sha256
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()

    def prog(_stage, done, total):
        el = time.monotonic() - t0
        progress(done, total, done / el / 1e6 if el > 1 else 0.0)

    _download(asset["url"], dest, asset["size"], prog, cancel)
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        stage("verify")
        if _sha256(dest, cancel) != digest.split(":", 1)[1].lower():
            dest.unlink(missing_ok=True)
            raise RuntimeError("The download was corrupted (checksum mismatch). Try again.")
    return dest


def launch_installer(path) -> None:
    """Starts the installer silently once this process has exited; the installer reopens Ralenti."""
    p = str(path).replace("'", "''")
    args = "'/SILENT','/SP-','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS','/RELAUNCH=1'"
    cmd = f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; Start-Process -FilePath '{p}' -ArgumentList {args}"
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-Command", cmd], creationflags=NO_WINDOW, close_fds=True)
