"""Icons: Phosphor (MIT) via qtawesome, Material Design fallbacks, and a few painted glyphs.
Check every icon the app uses with:  python scripts/check_icons.py"""
import sys

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from . import theme

try:
    import qtawesome as qta
except ImportError:
    qta = None

# Names differ between qtawesome versions: candidates are tried in order.
ALIASES = {
    "list-checks": ["ph.list-checks", "ph.list-dashes", "mdi6.playlist-check", "mdi.playlist-check", "ph.list"],
    "broom": ["ph.broom", "mdi6.broom", "mdi.broom", "ph.trash-simple"],
    "tray-arrow-down": ["ph.tray-arrow-down", "ph.download-simple", "mdi6.tray-arrow-down", "mdi.download"],
    "star-fill": ["ph.star-fill", "mdi6.star", "mdi.star", "ph.star"],
    "timer": ["ph.timer", "ph.clock", "mdi6.timer-outline", "mdi.timer-outline"],
    "sparkle": ["ph.sparkle", "ph.magic-wand", "mdi6.creation", "mdi.creation"],
    "crosshair-simple": ["ph.crosshair-simple", "ph.crosshair", "mdi6.crosshairs", "mdi.crosshairs"],
}
_resolved: dict[str, str | None] = {}


def _tone(t: str | None) -> str:
    return {"muted": theme.MUTED, "text": theme.TEXT, "faint": theme.FAINT, "accent": theme.ACCENT,
            "on_accent": theme.ON_ACCENT, "ok": theme.OK, "warn": theme.WARN,
            "danger": theme.DANGER}.get(t, t)


def resolve(name: str) -> str | None:
    """Short name -> full qtawesome name that exists in this install (or None)."""
    if name in PAINTED:
        return f"painted.{name}"
    if name in _resolved:
        return _resolved[name]
    full = None
    if qta is not None:
        for cand in ALIASES.get(name, []) + [f"ph.{name}", f"mdi6.{name}", f"mdi.{name}"]:
            try:
                qta.icon(cand)
                full = cand
                break
            except Exception:
                continue
        if full is None:
            print(f"[ralenti] no icon found for '{name}'", file=sys.stderr)
    _resolved[name] = full
    return full


# ---------- painted glyphs ----------
def _mark(p: QPainter, s: float, color: str, out: bool) -> None:
    X = (lambda x: s - x) if out else (lambda x: x)
    pen = QPen(QColor(color), s * 0.1, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    br = QPainterPath(QPointF(X(0.42 * s), 0.16 * s))
    br.lineTo(X(0.22 * s), 0.16 * s); br.lineTo(X(0.22 * s), 0.84 * s); br.lineTo(X(0.42 * s), 0.84 * s)
    p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawPath(br)
    p.setPen(Qt.NoPen); p.setBrush(QColor(color))
    p.drawPolygon(QPolygonF([QPointF(X(0.52 * s), 0.33 * s), QPointF(X(0.82 * s), 0.5 * s),
                             QPointF(X(0.52 * s), 0.67 * s)]))


PAINTED = {"mark-in": lambda p, s, c: _mark(p, s, c, False),
           "mark-out": lambda p, s, c: _mark(p, s, c, True)}


def _painted(name: str, tone: str, active: str | None) -> QIcon:
    ic = QIcon()
    for mode, color in ((QIcon.Normal, _tone(tone)),
                        (QIcon.Active, _tone(active) if active else _tone(tone)),
                        (QIcon.Disabled, theme.FAINT)):
        pm = QPixmap(64, 64); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        PAINTED[name](p, 64.0, color); p.end()
        ic.addPixmap(pm, mode, QIcon.Off); ic.addPixmap(pm, mode, QIcon.On)
    return ic


# ---------- public API ----------
def icon(name: str, tone: str = "muted", active: str | None = None,
         checked: str | None = "accent") -> QIcon:
    if name in PAINTED:
        return _painted(name, tone, active)
    full = resolve(name)
    if not full:
        return QIcon()
    color = _tone(tone)
    opts = dict(color=color, color_disabled=theme.FAINT,
                color_active=_tone(active) if active else (theme.TEXT if tone == "muted" else color))
    if checked:
        opts.update(color_on=_tone(checked), color_on_active=_tone(checked))
    try:
        return qta.icon(full, **opts)
    except Exception:
        return qta.icon(full, color=color)


def pixmap(name: str, size: int = 20, tone: str = "muted"):
    return icon(name, tone, checked=None).pixmap(size, size)


ALIASES.update({
    "graphics-card": ["ph.graphics-card", "ph.cpu", "mdi6.expansion-card", "mdi.expansion-card"],
    "check": ["ph.check", "mdi6.check", "mdi.check"],
    "speaker-low": ["ph.speaker-low", "mdi6.volume-medium", "mdi.volume-medium", "ph.speaker-high"],
})


ALIASES.update({
    "eraser": ["ph.eraser", "mdi6.eraser", "mdi.eraser", "ph.x"],
    "export": ["ph.export", "ph.upload-simple", "mdi6.export", "mdi.export"],
    "sliders": ["ph.sliders-horizontal", "ph.sliders", "mdi6.tune-variant", "mdi.tune"],
    "wave": ["ph.wave-sine", "ph.waveform", "mdi6.sine-wave", "mdi.sine-wave"],
    "seal-check": ["ph.seal-check", "ph.check-circle", "mdi6.check-decagram", "mdi.check-decagram"],
    "film-slate": ["ph.film-slate", "ph.film-strip", "mdi6.movie-open", "mdi.movie"],
    "arrows-out": ["ph.arrows-out", "ph.corners-out", "mdi6.arrow-expand", "mdi.arrow-expand"],
    "arrows-in": ["ph.arrows-in", "ph.corners-in", "mdi6.arrow-collapse", "mdi.arrow-collapse"],
})


def _mark_v2(p, s, color, out):
    """Rounded bracket plus a playhead dot: [•  and  •]"""
    X = (lambda x: s - x) if out else (lambda x: x)
    x0, xr, top, bot, r = 0.28, 0.50, 0.18, 0.82, 0.08
    path = QPainterPath(QPointF(X(xr * s), top * s))
    path.lineTo(X((x0 + r) * s), top * s)
    path.quadTo(X(x0 * s), top * s, X(x0 * s), (top + r) * s)
    path.lineTo(X(x0 * s), (bot - r) * s)
    path.quadTo(X(x0 * s), bot * s, X((x0 + r) * s), bot * s)
    path.lineTo(X(xr * s), bot * s)
    p.setPen(QPen(QColor(color), s * 0.11, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush); p.drawPath(path)
    p.setPen(Qt.NoPen); p.setBrush(QColor(color))
    p.drawEllipse(QPointF(X(0.66 * s), 0.5 * s), 0.09 * s, 0.09 * s)


PAINTED.update({"mark-in": lambda p, s, c: _mark_v2(p, s, c, False),
                "mark-out": lambda p, s, c: _mark_v2(p, s, c, True)})


_aliases_v6 = True
ALIASES.update({
    "github": ["ph.github-logo", "mdi6.github", "mdi.github", "fa5b.github"],
    "bug": ["ph.bug", "mdi6.bug", "mdi.bug"],
    "star": ["ph.star", "mdi6.star-outline", "mdi.star-outline"],
    "split-h": ["ph.square-split-horizontal", "mdi6.view-split-vertical", "mdi.view-split-vertical"],
    "columns": ["ph.columns", "mdi6.view-column", "mdi.view-column"],
    "versus": ["ph.arrows-left-right", "ph.git-diff", "mdi6.compare", "mdi.compare"],
    "scroll": ["ph.scroll", "ph.list-bullets", "mdi6.script-text-outline", "mdi.script-text-outline"],
})


_aliases_v6b = True
ALIASES.update({
    "download": ["ph.download-simple", "ph.download", "mdi6.download", "mdi.download"],
    "palette": ["ph.palette", "mdi6.palette", "mdi.palette"],
    "wrench": ["ph.wrench", "mdi6.wrench", "mdi.wrench"],
    "package": ["ph.package", "mdi6.package-variant", "mdi.package-variant"],
    "pulse": ["ph.pulse", "ph.activity", "mdi6.pulse", "mdi.pulse"],
})


# ===================== v0.8.2: view glyphs + checked colours for painted icons =====================
from PySide6.QtCore import QRectF  # noqa: E402

ALIASES.update({
    "caret-double-left": ["ph.caret-double-left", "mdi6.chevron-double-left", "mdi.chevron-double-left"],
    "caret-double-right": ["ph.caret-double-right", "mdi6.chevron-double-right", "mdi.chevron-double-right"],
})


def _view(p, s, color, kind):
    c = QColor(color)
    pen = QPen(c, s * 0.08, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    if kind == "side":
        p.setPen(pen); p.setBrush(Qt.NoBrush)
        for x0 in (0.1, 0.54):
            p.drawRoundedRect(QRectF(x0 * s, 0.3 * s, 0.36 * s, 0.4 * s), s * 0.05, s * 0.05)
        return
    r = QRectF(0.12 * s, 0.24 * s, 0.76 * s, 0.52 * s)
    p.setPen(pen); p.setBrush(Qt.NoBrush); p.drawRoundedRect(r, s * 0.07, s * 0.07)
    path = QPainterPath(); path.addRoundedRect(r, s * 0.07, s * 0.07)
    p.setClipPath(path); p.setPen(Qt.NoPen); p.setBrush(c)
    if kind == "left":
        p.drawRect(QRectF(r.left(), r.top(), r.width() / 2, r.height()))
    elif kind == "right":
        p.drawRect(QRectF(r.center().x(), r.top(), r.width() / 2, r.height()))
    else:
        p.drawRect(QRectF(r.center().x() - s * 0.03, r.top(), s * 0.06, r.height()))
    p.setClipping(False)


PAINTED.update({"view-left": lambda p, s, c: _view(p, s, c, "left"),
                "view-right": lambda p, s, c: _view(p, s, c, "right"),
                "view-split": lambda p, s, c: _view(p, s, c, "split"),
                "view-side": lambda p, s, c: _view(p, s, c, "side")})


def _painted2(name, tone, active, checked):
    ic = QIcon()
    act = _tone(active) if active else _tone(tone)
    on = _tone(checked) if checked else None
    for mode, state, color in ((QIcon.Normal, QIcon.Off, _tone(tone)), (QIcon.Active, QIcon.Off, act),
                               (QIcon.Disabled, QIcon.Off, theme.FAINT), (QIcon.Disabled, QIcon.On, theme.FAINT),
                               (QIcon.Normal, QIcon.On, on or _tone(tone)), (QIcon.Active, QIcon.On, on or act)):
        pm = QPixmap(64, 64); pm.fill(Qt.transparent)
        p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        PAINTED[name](p, 64.0, color); p.end()
        ic.addPixmap(pm, mode, state)
    return ic


_icon_v81 = icon


def icon(name: str, tone: str = "muted", active: str | None = None, checked: str | None = "accent") -> QIcon:
    if name in PAINTED:
        return _painted2(name, tone, active, checked)
    return _icon_v81(name, tone, active, checked)
