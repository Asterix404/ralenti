from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from ...core import downloads as core
from ...core.config import TOOLS_DIR
from .. import theme
from ..downloads import downloads, mb
from ..icons import pixmap
from ..util import notify
from ..widgets.anim import SmoothBar
from ..widgets.fields import Field, button, page_header, v_scroll
from ..widgets.panel import Section

SOURCE_HINTS = {
    "ffmpeg": "GitHub repository (owner/name) whose latest release has the full_build zip. Leave empty for the default.",
    "rife": "GitHub repository whose latest release has a Windows zip with rife-ncnn-vulkan.exe. Forks that package "
            "newer RIFE 4.x models work too. Leave empty for the default.",
}


def _inside(path: str, root: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _open(target: str) -> None:
    QDesktopServices.openUrl(QUrl(target) if "://" in target else QUrl.fromLocalFile(target))


class ToolCard(QFrame):
    def __init__(self, page, key: str):
        super().__init__()
        self.page, self.key, self.t = page, key, core.TOOLS[key]
        self.setObjectName("ToolCard")
        v = QVBoxLayout(self); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(12)
        top = QHBoxLayout(); top.setSpacing(16)
        ic = QLabel(); ic.setObjectName("ToolIcon"); ic.setFixedSize(52, 52); ic.setAlignment(Qt.AlignCenter)
        ic.setPixmap(pixmap(self.t.icon, 26, "accent"))
        texts = QVBoxLayout(); texts.setSpacing(4)
        nrow = QHBoxLayout(); nrow.setSpacing(8)
        name = QLabel(self.t.name); name.setObjectName("ToolName")
        tag = QLabel({"ffmpeg": "Required", "rife": "Recommended · free"}.get(key, "Optional"))
        tag.setObjectName("ToolTag")
        tag.setProperty("req", not self.t.optional)
        lic = QLabel(self.t.license); lic.setObjectName("ToolTag")
        for w in (name, tag, lic):
            nrow.addWidget(w)
        nrow.addStretch(1)
        blurb = QLabel(self.t.blurb); blurb.setObjectName("Muted"); blurb.setWordWrap(True)
        self.status = QLabel(); self.status.setWordWrap(True); self.status.setTextFormat(Qt.RichText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        texts.addLayout(nrow); texts.addWidget(blurb); texts.addWidget(self.status)
        top.addWidget(ic, 0, Qt.AlignTop); top.addLayout(texts, 1)

        self.prog = QWidget()
        ph = QHBoxLayout(self.prog); ph.setContentsMargins(0, 0, 0, 0); ph.setSpacing(12)
        self.bar = SmoothBar()
        self.prog_lbl = QLabel(); self.prog_lbl.setObjectName("Hint"); self.prog_lbl.setMinimumWidth(260)
        ph.addWidget(self.bar, 1); ph.addWidget(self.prog_lbl)
        self.prog.hide()

        btns = QHBoxLayout(); btns.setSpacing(6)
        self.primary = button("Install", self._install, "accent", icon="download")
        self.reinstall = button("Reinstall", self._install, "ghost", icon="arrow-clockwise")
        self.cancel_btn = button("Cancel", lambda: downloads().cancel(key), "ghost", icon="x")
        self.folder_btn = button("Open folder", self._folder, "ghost", icon="folder-open")
        self.remove_btn = button("Remove", self._remove, "ghost", icon="trash")
        self.page_btn = button("Release page", self._release, "ghost", icon="arrow-square-out")
        for b in (self.primary, self.reinstall, self.cancel_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        for b in (self.folder_btn, self.remove_btn, self.page_btn):
            btns.addWidget(b)
        v.addLayout(top); v.addWidget(self.prog); v.addLayout(btns)
        self.refresh()

    def refresh(self) -> None:
        mgr, tc = downloads(), self.page.qm.tc
        inst = core.installed(self.key)
        latest = mgr.latest.get(self.key) or {}
        busy = mgr.busy(self.key)
        found = tc.ffmpeg if self.key == "ffmpeg" else getattr(tc, "rife", "")
        external = found if found and not _inside(found, TOOLS_DIR) else ""
        lv = latest.get("version") or ""
        asset = latest.get("asset") or {}
        size = mb(asset["size"]) if asset.get("size") else self.t.size
        newer = bool(inst and lv and core.is_newer(lv, inst["version"]))
        if busy:
            text = f"<b style='color:{theme.ACCENT}'>Installing</b>"
        elif inst:
            text = f"<b style='color:{theme.OK}'>Installed</b> · version {inst['version']}"
            if newer:
                text += f" · <b style='color:{theme.WARN}'>update available: {lv}</b>"
            elif lv:
                text += " · up to date"
            text += f"<br><span style='color:{theme.FAINT}'>{inst['exe']}</span>"
        elif external:
            text = (f"<b style='color:{theme.OK}'>Using your own copy</b><br>"
                    f"<span style='color:{theme.FAINT}'>{external}</span>")
        else:
            text = f"<b style='color:{theme.MUTED}'>Not installed</b> · {size} download"
        if latest.get("error") and not busy:
            text += (f"<br><span style='color:{theme.FAINT}'>Couldn't check for the latest version: "
                     f"{latest['error']}</span>")
        self.status.setText(text)
        self.prog.setVisible(busy)
        if busy and (d := mgr.describe(self.key)):
            self.bar.set_smooth(d[0] * 1000)
            self.prog_lbl.setText(d[1])
        self.primary.setVisible(not busy and (not inst or newer))
        self.primary.setText(f"Update to {lv}" if newer else ("Install managed copy" if external else "Install"))
        self.reinstall.setVisible(not busy and bool(inst) and not newer)
        self.cancel_btn.setVisible(busy)
        self.remove_btn.setVisible(not busy and bool(inst))
        self.folder_btn.setVisible(bool(inst))

    def _in_use(self) -> bool:
        qm = self.page.qm
        return self.key == "ffmpeg" and bool(core.installed("ffmpeg")) and (qm.is_running or qm.preview_running)

    def _install(self) -> None:
        if self._in_use():
            notify(self, "Stop the queue first: running jobs may be using FFmpeg.", "warn")
            return
        downloads().install(self.key)
        self.refresh()

    def _remove(self) -> None:
        qm = self.page.qm
        if qm.is_running or qm.preview_running:
            notify(self, "Stop the queue before removing tools it may be using.", "warn")
            return
        r = QMessageBox.question(self, "Ralenti", f"Remove {self.t.name}? You can install it again any time.")
        if r != QMessageBox.StandardButton.Yes:
            return
        try:
            downloads().remove(self.key)
        except Exception as e:
            notify(self, str(e), "error")

    def _folder(self) -> None:
        if inst := core.installed(self.key):
            _open(str(Path(inst["exe"]).parent))

    def _release(self) -> None:
        _open(f"https://github.com/{downloads().repo(self.key)}/releases/latest")


class DownloadsPage(QWidget):
    def __init__(self, qm, settings):
        super().__init__()
        self.qm, self.settings = qm, settings
        mgr = downloads(settings)
        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(8)
        root.addWidget(page_header("Downloads", "One-click installs for the tools Ralenti uses. Everything goes "
                                   "into Ralenti's own data folder; nothing else on your PC is changed."))
        body = QWidget(); body.setObjectName("ScrollBody")
        bv = QVBoxLayout(body); bv.setContentsMargins(0, 0, 6, 0); bv.setSpacing(14)
        self.cards = {k: ToolCard(self, k) for k in core.TOOLS}
        for c in self.cards.values():
            bv.addWidget(c)

        src = QWidget(); sv = QVBoxLayout(src); sv.setContentsMargins(0, 0, 0, 0); sv.setSpacing(14)
        for k, t in core.TOOLS.items():
            e = QLineEdit(getattr(settings, f"{k}_repo", "") or ""); e.setPlaceholderText(t.repo)
            e.editingFinished.connect(lambda k=k, e=e: self._set_repo(k, e.text()))
            sv.addWidget(Field(f"{t.name} source", e, SOURCE_HINTS[k]))
        card = QFrame(); card.setObjectName("Card")
        cl = QVBoxLayout(card); cl.setContentsMargins(18, 10, 18, 14)
        cl.addWidget(Section("dl.sources", "Download sources", "package", src, settings, divider=False, expanded=False))
        bv.addWidget(card)

        loc = QHBoxLayout()
        where = QLabel(f"Installed into {TOOLS_DIR}"); where.setObjectName("Hint")
        where.setTextInteractionFlags(Qt.TextSelectableByMouse)
        loc.addWidget(where, 1)
        loc.addWidget(button("Open tools folder", lambda: (TOOLS_DIR.mkdir(parents=True, exist_ok=True),
                                                           _open(str(TOOLS_DIR))), "ghost", icon="folder-open"))
        bv.addLayout(loc)
        bv.addStretch(1)
        sc = v_scroll(body)
        sc.viewport().setAutoFillBackground(True)   # solid background: no ghosting when sections collapse
        root.addWidget(sc, 1)

        mgr.progress.connect(lambda k, *_: self._card(k))
        mgr.latest_ready.connect(self._card)

    def _card(self, key: str) -> None:
        if c := self.cards.get(key):
            c.refresh()

    def refresh_all(self) -> None:
        for c in self.cards.values():
            c.refresh()

    def _set_repo(self, key: str, text: str) -> None:
        if (getattr(self.settings, f"{key}_repo", "") or "") == text.strip():
            return
        setattr(self.settings, f"{key}_repo", text.strip())
        self.settings.save()
        downloads().latest.pop(key, None)
        downloads().check_latest()
        self._card(key)

    def showEvent(self, e):
        super().showEvent(e)
        downloads().check_latest()
        self.refresh_all()
