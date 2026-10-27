import platform

from PySide6 import __version__ as PYSIDE_VERSION
from PySide6.QtCore import Qt, QUrl, qVersion
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import AUTHOR, REPO_URL, __version__
from ...core import extensions
from ...core.config import APP_DIR
from ...core.engines import engines
from .. import theme
from ..util import resource
from ..widgets.fields import button, page_header, v_scroll


def _open(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class AboutPage(QWidget):
    def __init__(self, qm, settings):
        super().__init__()
        self.qm = qm
        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(8)
        root.addWidget(page_header("About", "Version, credits and links."))
        body = QWidget(); body.setObjectName("ScrollBody")
        bv = QVBoxLayout(body); bv.setContentsMargins(0, 0, 6, 0); bv.setSpacing(16)

        hero = QFrame(); hero.setObjectName("AboutHero")
        hl = QHBoxLayout(hero); hl.setContentsMargins(28, 26, 28, 26); hl.setSpacing(26)
        logo = QLabel()
        dpr = self.devicePixelRatioF()
        pm = QPixmap(str(resource("ralenti.ico")))
        if not pm.isNull():
            pm = pm.scaled(int(112 * dpr), int(112 * dpr), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            logo.setPixmap(pm)
        texts = QVBoxLayout(); texts.setSpacing(6)
        name = QLabel("Ralenti"); name.setObjectName("AboutName")
        tag = QLabel("Motion & encoding studio"); tag.setObjectName("AboutTag")
        meta = QHBoxLayout(); meta.setSpacing(8)
        build = extensions.load().name
        ver = QLabel(f"Version {__version__}" + (f" · {build}" if build else "")); ver.setObjectName("Badge")
        by = QLabel(f"Made by {AUTHOR}"); by.setObjectName("Hint")
        meta.addWidget(ver); meta.addWidget(by); meta.addStretch(1)
        desc = QLabel("AI frame interpolation, quality-targeted encoding and lossless video tools in one queue. "
                      "Ralenti interpolates with RIFE, a free and open-source AI model that runs on any modern GPU, "
                      "adds VMAF-based encoding, lossless trim and merge, and keeps everything resumable.")
        desc.setWordWrap(True); desc.setObjectName("Muted")
        btns = QHBoxLayout(); btns.setSpacing(8)
        btns.addWidget(button("GitHub", lambda: _open(REPO_URL), "accent", icon="github"))
        btns.addWidget(button("Check for updates", lambda: self.window().check_updates(manual=True),
                              icon="arrow-clockwise"))
        btns.addWidget(button("Report an issue", lambda: _open(REPO_URL + "/issues"), "ghost", icon="bug"))
        btns.addWidget(button("Changelog", lambda: _open(REPO_URL + "/blob/main/CHANGELOG.md"), "ghost",
                              icon="scroll"))
        btns.addStretch(1)
        for w in (name, tag):
            texts.addWidget(w)
        texts.addLayout(meta); texts.addSpacing(4); texts.addWidget(desc); texts.addSpacing(6); texts.addLayout(btns)
        hl.addWidget(logo, 0, Qt.AlignTop); hl.addLayout(texts, 1)
        bv.addWidget(hero)

        info = QFrame(); info.setObjectName("Card")
        iv = QVBoxLayout(info); iv.setContentsMargins(22, 18, 22, 20); iv.setSpacing(12)
        title = QLabel("Details"); title.setObjectName("SectionTitle")
        iv.addWidget(title)
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(20); self.grid.setVerticalSpacing(10)
        self.grid.setColumnStretch(1, 1)
        iv.addLayout(self.grid)
        row = QHBoxLayout(); row.addStretch(1)
        row.addWidget(button("Open data folder", lambda: _open(QUrl.fromLocalFile(str(APP_DIR)).toString()),
                             "ghost", icon="folder-open"))
        iv.addLayout(row)
        bv.addWidget(info)

        credits = QFrame(); credits.setObjectName("Card")
        cv = QVBoxLayout(credits); cv.setContentsMargins(22, 18, 22, 20); cv.setSpacing(8)
        ct = QLabel("Credits"); ct.setObjectName("SectionTitle")
        cl = QLabel("Built with Qt / PySide6, FFmpeg, RIFE (rife-ncnn-vulkan, ncnn), Phosphor Icons and qtawesome. "
                    "Released under the MIT license.")
        cl.setWordWrap(True); cl.setObjectName("Muted")
        cv.addWidget(ct); cv.addWidget(cl)
        bv.addWidget(credits)
        bv.addStretch(1)
        root.addWidget(v_scroll(body), 1)
        self._fill()

    def _fill(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        tc = self.qm.tc
        link = f"<a href='{REPO_URL}' style='color:{theme.ACCENT}; text-decoration:none'>{REPO_URL}</a>"
        rows = [("Version", __version__), ("Author", AUTHOR), ("Source code", link), ("License", "MIT"),
                ("Python", platform.python_version()), ("Qt / PySide6", f"{qVersion()} / {PYSIDE_VERSION}"),
                ("FFmpeg", tc.ffmpeg or "Not found")]
        rows += [(e.name, e.status(tc)) for e in engines()]
        rows.append(("Data folder", str(APP_DIR)))
        for i, (k, v) in enumerate(rows):
            key = QLabel(k.upper()); key.setObjectName("InfoKey")
            val = QLabel(v); val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextBrowserInteraction)
            val.setOpenExternalLinks(True)
            self.grid.addWidget(key, i, 0, Qt.AlignTop); self.grid.addWidget(val, i, 1)

    def showEvent(self, e):
        super().showEvent(e)
        self._fill()
