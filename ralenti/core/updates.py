"""Checks GitHub for a newer published release. Sends nothing but the request itself."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

API = "https://api.github.com/repos/{repo}/releases/latest"


@dataclass
class Update:
    version: str
    url: str
    notes: str


def _ver(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3]) or (0,)


def check(repo: str, current: str, timeout: float = 6) -> Update | None:
    repo = repo.strip().strip("/")
    if repo.count("/") != 1:
        raise ValueError("Release source must look like owner/name")
    req = urllib.request.Request(API.format(repo=repo), headers={
        "Accept": "application/vnd.github+json", "User-Agent": "Ralenti"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("no public release found. The repository is private or has no "
                               "releases yet.") from None
        if e.code in (403, 429):
            raise RuntimeError("GitHub's rate limit was reached. Try again in an hour.") from None
        raise RuntimeError(f"GitHub returned HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"no connection ({e.reason})") from None
    tag = data.get("tag_name") or ""
    if data.get("draft") or data.get("prerelease") or _ver(tag) <= _ver(current):
        return None
    return Update(tag.lstrip("vV"), data.get("html_url", ""), (data.get("body") or "").strip()[:600])
