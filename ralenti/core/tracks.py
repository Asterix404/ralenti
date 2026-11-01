"""Audio tracks of a file (videos often have several: languages, commentary)."""
from __future__ import annotations

import json

from .tools import run

LANGS = {"eng": "English", "fra": "French", "fre": "French", "deu": "German", "ger": "German", "spa": "Spanish",
         "ita": "Italian", "jpn": "Japanese", "por": "Portuguese", "rus": "Russian", "chi": "Chinese",
         "zho": "Chinese", "kor": "Korean"}


def _layout(ch: int) -> str:
    return {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(ch, f"{ch} ch" if ch else "")


def audio_tracks(ffprobe: str, path: str) -> list[dict]:
    if not ffprobe or not path:
        return []
    try:
        r = run([ffprobe, "-v", "error", "-select_streams", "a", "-show_entries",
                 "stream=codec_name,channels,sample_rate:stream_tags=language,title", "-of", "json", path], timeout=30)
        streams = json.loads(r.stdout or "{}").get("streams") or []
    except Exception:
        return []
    out = []
    for n, s in enumerate(streams):
        tags = s.get("tags") or {}
        lang = (tags.get("language") or "").lower()
        lang = "" if lang in ("und", "") else lang
        title, ch = tags.get("title") or "", int(s.get("channels") or 0)
        bits = [LANGS.get(lang, lang.upper()) if lang else "", title, (s.get("codec_name") or "").upper(), _layout(ch)]
        out.append(dict(n=n, codec=s.get("codec_name") or "", channels=ch, sample_rate=int(s.get("sample_rate") or 0),
                        lang=lang, title=title, label=f"Track {n + 1} · " + " · ".join(b for b in bits if b)))
    return out
