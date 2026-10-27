"""AI interpolation engines. RIFE is built in; extensions can register more."""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


@dataclass(frozen=True)
class Model:
    code: str
    label: str
    blurb: str = ""
    tag: str = ""
    installed: bool = True


@dataclass(frozen=True)
class Option:
    """An engine-specific setting, shown as chips. choices: ((label, value, tooltip), ...)"""
    key: str
    title: str
    choices: tuple
    default: object
    hint: str = ""


@dataclass
class Render:
    """Everything an engine needs to render the AI video (no audio)."""
    info: object
    model: str
    start: float | None
    dur: float
    crop: object
    size: tuple | None
    target: Fraction
    slowmo: float
    device: str
    opts: dict
    tmp: Path

    @property
    def total(self) -> float:
        return self.dur * self.slowmo

    @property
    def frames(self) -> int:
        return round(self.total * float(self.target))


class Engine:
    key = ""
    name = ""
    group = ""
    options: tuple = ()

    def available(self, tc) -> bool:
        return False

    def status(self, tc) -> str:
        return "available" if self.available(tc) else "not installed"

    def models(self, tc) -> list[Model]:
        return []

    def owns(self, code: str) -> bool:
        return False

    def direct_encode(self, tc, codec: str) -> bool:
        """Can render() write straight to this encoder?"""
        return bool(tc.binary_for(codec))

    def temp_bytes(self) -> float:
        return 0.0

    def defaults(self) -> dict:
        return {o.key: o.default for o in self.options}

    def refresh(self) -> None:
        """Forget cached detection results (e.g. after paths change)."""

    def confirm(self, parent, settings, tc=None):
        """UI hook before the engine is used: True = go ahead, False = cancel, a model code = switch to it."""
        return True

    def render(self, r: Render, tc, ctl, enc, out: str, q=None, span=(0.0, 1.0)) -> None:
        raise NotImplementedError


_engines: list | None = None


def engines() -> list:
    global _engines
    if _engines is None:
        from . import extensions
        from .rife import RifeEngine
        _engines = [RifeEngine()] + list(extensions.load().engines)
    return _engines


def available_engines(tc) -> list:
    return [e for e in engines() if e.available(tc)]


def engine_for(code: str | None):
    return next((e for e in engines() if code and e.owns(code)), None)


def all_models(tc) -> list:
    return [(e, m) for e in available_engines(tc) for m in e.models(tc)]


def best_model(tc) -> str | None:
    hits = all_models(tc)
    return hits[0][1].code if hits else None


def resolve_model(tc, code: str | None) -> str | None:
    """The requested model if it's installed, otherwise the best installed one (or None)."""
    if code and code != "auto":
        e = engine_for(code)
        if e and e.available(tc) and any(m.code == code for m in e.models(tc)):
            return code
    return best_model(tc)
