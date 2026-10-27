"""External tools Ralenti can install: the FFmpeg full build and RIFE (ncnn-vulkan).
Downloads come from GitHub releases and go to %APPDATA%\\Ralenti\\tools."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import TOOLS_DIR

MANIFEST = TOOLS_DIR / "manifest.json"
STAGING = TOOLS_DIR / ".staging"
HEADERS = {"User-Agent": "Ralenti", "Accept": "application/vnd.github+json"}


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class ToolDef:
    key: str
    name: str
    blurb: str
    repo: str
    pattern: str
    exe: str
    files: tuple[str, ...]   # files kept next to the exe; empty = keep the whole folder
    license: str
    icon: str
    optional: bool
    size: str


TOOLS: dict[str, ToolDef] = {t.key: t for t in [
    ToolDef("ffmpeg", "FFmpeg · full build",
            "Needed for VMAF quality targets, black-bar detection, Rubber Band audio, SVT-AV1 and the "
            "lossless tools. Gyan's full Windows build.",
            "GyanD/codexffmpeg", r"^ffmpeg-[\d.]+-full_build\.zip$", "ffmpeg.exe",
            ("ffmpeg.exe", "ffprobe.exe"), "GPL v3", "film-strip", False, "about 200 MB"),
    ToolDef("rife", "RIFE · ncnn-vulkan",
            "Free, open-source frame interpolation for any Vulkan GPU (NVIDIA, AMD, Intel). "
            "Ralenti's main AI engine: no account or license needed.",
            "nihui/rife-ncnn-vulkan", r"^rife-ncnn-vulkan-\d+-windows\.zip$", "rife-ncnn-vulkan.exe",
            (), "MIT", "sparkle", False, "a few hundred MB"),
]}


# ---------- GitHub ----------
def _get(url: str, timeout: float = 15):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout)


def latest_release(repo: str) -> dict:
    repo = repo.strip().strip("/")
    if repo.count("/") != 1:
        raise RuntimeError("The source must look like owner/name.")
    try:
        with _get(f"https://api.github.com/repos/{repo}/releases/latest") as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(f"No releases found for {repo}.") from None
        if e.code in (403, 429):
            raise RuntimeError("GitHub's rate limit was reached. Try again in an hour.") from None
        raise RuntimeError(f"GitHub returned HTTP {e.code}.") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"No connection ({e.reason}).") from None


def pick_asset(assets: list[dict], pattern: str) -> dict | None:
    rx = re.compile(pattern, re.I)
    hit = next((a for a in assets if rx.search(a.get("name", ""))), None)
    if hit:
        return hit
    return next((a for a in assets if a.get("name", "").lower().endswith(".zip")
                 and "win" in a.get("name", "").lower()), None)


def version_key(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", s or "")) or (0,)


def is_newer(latest: str, installed: str) -> bool:
    return version_key(latest) > version_key(installed)


def release_info(key: str, repo: str) -> dict:
    rel = latest_release(repo)
    a = pick_asset(rel.get("assets", []), TOOLS[key].pattern)
    return {"version": rel.get("tag_name", ""), "url": rel.get("html_url", ""),
            "asset": {"name": a["name"], "size": int(a.get("size") or 0)} if a else None}


# ---------- manifest ----------
def read_manifest() -> dict:
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_manifest(d: dict) -> None:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(d, indent=2), encoding="utf-8")


def installed(key: str) -> dict | None:
    info = read_manifest().get(key)
    return info if info and Path(info.get("exe", "")).is_file() else None


# ---------- install / remove ----------
def _download(url: str, dest: Path, size: int, progress, cancel: threading.Event) -> None:
    part = dest.with_name(dest.name + ".part")
    done = 0
    try:
        with _get(url, timeout=30) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or size or 0)
            last = 0.0
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last > 0.15:
                    progress("download", done, total)
                    last = now
        progress("download", done, total)
        if total and done < total:
            raise RuntimeError("The download was interrupted. Try again.")
        part.replace(dest)
    except Cancelled:
        part.unlink(missing_ok=True)
        raise
    except OSError as e:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Download failed ({getattr(e, 'reason', e)}).") from None


def _sha256(path: Path, cancel: threading.Event) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            if cancel.is_set():
                raise Cancelled()
            h.update(chunk)
    return h.hexdigest()


def install(key: str, repo: str, progress: Callable[[str, float, float], None],
            cancel: threading.Event) -> dict:
    t = TOOLS[key]
    rel = latest_release(repo)
    asset = pick_asset(rel.get("assets", []), t.pattern)
    if not asset:
        raise RuntimeError(f"No Windows zip in the latest release of {repo}. "
                           "Use Release page to download it manually.")
    STAGING.mkdir(parents=True, exist_ok=True)
    size = int(asset.get("size") or 0)
    if size and shutil.disk_usage(TOOLS_DIR).free < size * 3 + 300e6:
        raise RuntimeError(f"Not enough disk space: about {size * 3 / 1e9:.1f} GB is needed while installing.")
    zpath = STAGING / asset["name"]
    out, new, old, dest = (STAGING / f"{key}_x", STAGING / f"{key}_new",
                           STAGING / f"{key}_old", TOOLS_DIR / key)
    try:
        _download(asset["browser_download_url"], zpath, size, progress, cancel)
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            progress("verify", 0, 0)
            if _sha256(zpath, cancel) != digest.split(":", 1)[1].lower():
                raise RuntimeError("The download was corrupted (checksum mismatch). Try again.")
        progress("extract", 0, 0)
        shutil.rmtree(out, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            members = z.infolist()
            for i, m in enumerate(members):
                if cancel.is_set():
                    raise Cancelled()
                z.extract(m, out)
                if i % 25 == 0:
                    progress("extract", i, len(members))
        exe = next(out.rglob(t.exe), None)
        if exe is None:
            raise RuntimeError(f"{t.exe} wasn't found inside the download.")
        shutil.rmtree(new, ignore_errors=True)
        if t.files:
            new.mkdir(parents=True)
            for name in t.files:
                if (exe.parent / name).is_file():
                    shutil.copy2(exe.parent / name, new / name)
            lic = next((p for p in out.rglob("LICENSE*") if p.is_file()), None)
            if lic:
                shutil.copy2(lic, new / lic.name)
        else:
            shutil.move(str(exe.parent), str(new))
        if dest.exists():
            shutil.rmtree(old, ignore_errors=True)
            try:
                dest.rename(old)
            except OSError:
                raise RuntimeError(f"{t.name} is in use. Close anything using it and try again.") from None
        new.rename(dest)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(new, ignore_errors=True)
        zpath.unlink(missing_ok=True)
    info = {"version": rel.get("tag_name", ""), "exe": str(dest / t.exe), "asset": asset["name"],
            "repo": repo, "installed": time.strftime("%Y-%m-%d %H:%M")}
    m = read_manifest(); m[key] = info; _write_manifest(m)
    return info


def remove(key: str) -> None:
    dest = TOOLS_DIR / key
    if dest.exists():
        try:
            shutil.rmtree(dest)
        except OSError:
            raise RuntimeError(f"{TOOLS[key].name} is in use. Close anything using it and try again.") from None
    m = read_manifest(); m.pop(key, None); _write_manifest(m)