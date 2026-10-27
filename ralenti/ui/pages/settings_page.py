import os
import sys
import time
import traceback

from PySide6.QtCore import QProcess, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ...core import extensions, gpu
from ...core.config import LOGS_DIR, TEMP_DIR
from ...core.engines import engines
from ...core.tools import Toolchain
from .. import theme
from ..thumbs import thumbs
from ..util import mono_font, notify
from ..widgets.choice import RichCombo
from ..widgets.fields import Field, button, page_header
from ..widgets.panel import Section, options_scroll
from ..workers import run_async

TOOL_FIELDS = [
    ("full_ffmpeg", "FFmpeg", "file", "Leave empty to use the copy from Downloads, or point to your own full build."),
    ("full_ffprobe", "ffprobe", "file", "Reads video information. Usually found next to ffmpeg.exe automatically."),
    ("rife_exe", "RIFE", "file", "rife-ncnn-vulkan.exe. Leave empty to use the copy from Downloads."),
]
INTERMEDIATES = [("ref_nvenc", "HEVC near-lossless · NVENC", "Recommended",
                  "Visually identical VMAF reference. GPU-fast and 4–5× smaller than FFV1."),
                 ("ffv1", "FFV1 lossless", "Exact",
                  "Bit-exact reference. Very large: roughly 6–10 GB per minute at 1080p60.")]
FALLBACK = [("-2", "Automatic", "", "Lets the engine pick the device."),
            ("-1", "CPU only", "Very slow", "No GPU. Only useful for troubleshooting.")]
ACCENTS = [("Teal", "#2dd4bf"), ("Violet", "#a78bfa"), ("Blue", "#60a5fa"),
           ("Amber", "#fbbf24"), ("Rose", "#fb7185"), ("Lime", "#a3e635")]


def detect_devices():
    nv = gpu.nvidia_gpus()
    opts, note = [], ""
    if nv:
        best = max(nv, key=lambda g: g.vram_mb).index
        for g in nv:
            opts.append((str(g.index), f"GPU {g.index} · {g.short}", "Recommended" if g.index == best else "",
                         f"{g.name} · {g.vram_gb} GB VRAM"))
        if len(nv) > 1:
            opts.append((".".join(str(g.index) for g in nv), "All NVIDIA GPUs", "Multi-GPU",
                         "Shares the work across every NVIDIA card (engines that support it)."))
        others = [a.name for a in gpu.windows_adapters() if not a.nvidia]
        if others:
            note = "Also in this PC: " + ", ".join(others) + "."
    else:
        for a in gpu.windows_adapters():
            opts.append((str(a.index), f"GPU {a.index} · {a.name}", "",
                         "Detected by Windows. The engine may number devices differently."))
        note = "nvidia-smi wasn't found, so the names come from Windows."
    return opts + FALLBACK, note


class SettingsPage(QWidget):
    saved = Signal()
    setup_requested = Signal()
    open_downloads = Signal()

    def __init__(self, qm, settings):
        super().__init__()
        self.qm, self.settings, self.edits = qm, settings, {}
        ext = extensions.load()
        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(page_header("Settings", "Changes are saved automatically. Tool paths can stay empty: "
                                   "Ralenti finds them on its own."), 1)
        self.saved_lbl = QLabel(); self.saved_lbl.setObjectName("SavedLbl")
        head.addWidget(self.saved_lbl, 0, Qt.AlignBottom)
        root.addLayout(head)

        content = QWidget(); content.setObjectName("OptionsBody"); content.setAttribute(Qt.WA_StyledBackground)
        cv = QVBoxLayout(content); cv.setContentsMargins(22, 14, 22, 20); cv.setSpacing(12)

        cv.addWidget(Section("set.appearance", "Appearance", "palette",
                             Field("Accent color", self._accent_row(),
                                   "Highlights, buttons and progress bars. Icons fully recolor after a restart."),
                             settings, divider=False))

        proc = QWidget(); pv = QVBoxLayout(proc); pv.setContentsMargins(0, 0, 0, 0); pv.setSpacing(14)
        self.device = RichCombo()
        self.device.add("Detecting GPUs…", settings.gpu_device or "0", "Reading your graphics cards.")
        pv.addWidget(Field("AI device", self.device))
        self.gpu_note = QLabel(); self.gpu_note.setObjectName("Hint"); self.gpu_note.setWordWrap(True)
        pv.addWidget(self.gpu_note); self.gpu_note.hide()
        self.inter = RichCombo()
        for key, title, tag, blurb in INTERMEDIATES:
            self.inter.add(title, key, blurb, tag)
        self.inter.select(settings.intermediate)
        pv.addWidget(Field("VMAF reference file", self.inter))
        pv.addWidget(Field("Temp folder", self._path_row("temp_dir", "dir"),
                           "Intermediate files for VMAF jobs and previews. Pick a fast drive with plenty of space."))
        cv.addWidget(Section("set.processing", "Processing", "graphics-card", proc, settings))

        tools = QWidget(); tv = QVBoxLayout(tools); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(14)
        srow = QHBoxLayout(); srow.setSpacing(10)
        self.tools_lbl = QLabel(); self.tools_lbl.setTextFormat(Qt.RichText); self.tools_lbl.setWordWrap(True)
        srow.addWidget(self.tools_lbl, 1)
        srow.addWidget(button("Manage downloads", lambda: self.open_downloads.emit(), "accent", icon="download"))
        tv.addLayout(srow)
        for key, label, kind, hint in TOOL_FIELDS:
            tv.addWidget(Field(label, self._path_row(key, kind), hint))
        cv.addWidget(Section("set.tools", "Tools", "wrench", tools, settings))

        for factory in ext.settings_sections:
            try:
                key, title, ic, widget = factory(self)
                cv.addWidget(Section(key, title, ic, widget, settings))
            except Exception:
                traceback.print_exc()

        cv.addWidget(Section("set.ext", "Extensions", "package", self._ext_section(), settings))
        upd = QWidget(); uv = QVBoxLayout(upd); uv.setContentsMargins(0, 0, 0, 0); uv.setSpacing(12)
        self.auto_update = QCheckBox("Check for updates when Ralenti starts")
        self.auto_update.setChecked(settings.check_updates)
        uv.addWidget(self.auto_update)
        self.repo = QLineEdit(settings.update_repo); self.repo.setPlaceholderText("owner/ralenti")
        rw = QWidget(); rh = QHBoxLayout(rw); rh.setContentsMargins(0, 0, 0, 0); rh.setSpacing(6)
        rh.addWidget(self.repo, 1); rh.addWidget(button("Check now", self._check_now, icon="arrow-clockwise"))
        uv.addWidget(Field("Release source (GitHub)", rw, "Ralenti asks GitHub for the newest release of this "
                           "repository. Nothing else is sent."))
        if ext.update_hint:
            hn = QLabel(ext.update_hint); hn.setObjectName("Hint"); hn.setWordWrap(True)
            uv.addWidget(hn)
        if ext.disable_updates:
            note = QLabel("Update checks are off in this build: update it from its own source.")
            note.setObjectName("Hint"); note.setWordWrap(True)
            uv.insertWidget(0, note)
            self.auto_update.setEnabled(False); rw.setEnabled(False)
        cv.addWidget(Section("set.updates", "Updates", "arrow-clockwise", upd, settings))

        diag = QWidget(); dv = QVBoxLayout(diag); dv.setContentsMargins(0, 0, 0, 0); dv.setSpacing(12)
        brow = QHBoxLayout(); brow.setSpacing(6)
        brow.addWidget(button("Run setup", lambda: self.setup_requested.emit(), icon="magic-wand"))
        brow.addWidget(button("Open logs", lambda: os.startfile(LOGS_DIR), "ghost", icon="file-text"))
        brow.addWidget(button("Open temp folder", lambda: os.startfile(self.settings.temp_dir or TEMP_DIR),
                              "ghost", icon="folder-open"))
        brow.addStretch(1)
        dv.addLayout(brow)
        self.report = QPlainTextEdit(); self.report.setReadOnly(True)
        self.report.setFont(mono_font()); self.report.setMinimumHeight(180)
        dv.addWidget(self.report)
        cv.addWidget(Section("set.diag", "Diagnostics", "pulse", diag, settings, expanded=False))
        cv.addStretch(1)

        card = QFrame(); card.setObjectName("Card")
        cl = QVBoxLayout(card); cl.setContentsMargins(1, 1, 1, 1)
        cl.addWidget(options_scroll(content))
        root.addWidget(card, 1)

        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(500)
        self._timer.timeout.connect(self._commit)
        self.device.currentIndexChanged.connect(self._dirty)
        self.inter.currentIndexChanged.connect(self._dirty)
        self.auto_update.toggled.connect(self._dirty)
        self.repo.editingFinished.connect(self._dirty)
        self._refresh_info()
        run_async(detect_devices, self._fill_devices, lambda m: self._fill_devices((FALLBACK, m)))

    # ---------- fields ----------
    def _path_row(self, key: str, kind: str) -> QWidget:
        e = QLineEdit(getattr(self.settings, key, "") or ""); e.setPlaceholderText("Auto-detect")
        e.editingFinished.connect(self._dirty)
        self.edits[key] = e
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        h.addWidget(e, 1)
        h.addWidget(button("Browse", lambda _=False: self._browse(e, kind), icon="folder-open"))
        return w

    def _browse(self, edit: QLineEdit, kind: str) -> None:
        path = (QFileDialog.getExistingDirectory(self, "Choose folder") if kind == "dir"
                else QFileDialog.getOpenFileName(self, "Choose file", "", "Programs (*.exe)")[0])
        if path:
            edit.setText(path)
            self._dirty()

    def _fill_devices(self, result) -> None:
        opts, note = result
        cur = self.settings.gpu_device or "0"
        self.device.blockSignals(True)
        self.device.clear()
        for key, title, tag, blurb in opts:
            self.device.add(title, key, blurb, tag)
        if not self.device.select(cur):
            self.device.add(f"Custom ({cur})", cur, "Device setting from an earlier version.")
            self.device.select(cur)
        self.device.blockSignals(False)
        self.gpu_note.setText(note or ""); self.gpu_note.setVisible(bool(note))

    # ---------- saving ----------
    def _dirty(self, *_) -> None:
        self._timer.start()

    def _commit(self) -> None:
        for key, e in self.edits.items():
            setattr(self.settings, key, e.text().strip())
        self.settings.intermediate = self.inter.currentData() or "ref_nvenc"
        self.settings.gpu_device = self.device.currentData() or "0"
        self.settings.check_updates = self.auto_update.isChecked()
        self.settings.update_repo = self.repo.text().strip()
        self.settings.save()
        self.qm.tc = Toolchain.resolve(self.settings)
        for eng in engines():
            eng.refresh()
        thumbs().configure(self.qm.tc)
        self._refresh_info()
        self.saved.emit()
        self.saved_lbl.setText(f"All changes saved · {time.strftime('%H:%M')}")

    def save(self, quiet: bool = False) -> None:
        self._timer.stop()
        self._commit()

    def sync_from_settings(self) -> None:
        for key, e in self.edits.items():
            e.setText(getattr(self.settings, key, "") or "")
        self._refresh_info()
        self.saved.emit()

    def _refresh_info(self) -> None:
        tc = self.qm.tc
        self.report.setPlainText(tc.report())
        items = [(bool(tc.ffmpeg), "FFmpeg (required)", False)] + [(e.available(tc), e.name, True) for e in engines()]
        parts = []
        for ok, name, optional in items:
            color = theme.OK if ok else (theme.FAINT if optional else theme.DANGER)
            parts.append(f"<span style='color:{color}'>●</span>&nbsp;{name}")
        self.tools_lbl.setText("&nbsp;&nbsp;&nbsp;".join(parts))

    def _check_now(self) -> None:
        self.save()
        self.window().check_updates(manual=True)

    # ---------- appearance ----------
    def _accent_row(self) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10)
        self._swatches = QButtonGroup(self); self._swatches.setExclusive(True)
        cur = (self.settings.accent or "#2dd4bf").lower()
        for name, color in ACCENTS:
            b = QPushButton(); b.setCheckable(True); b.setToolTip(name); b.setFixedSize(30, 30)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ background: {color}; border-radius: 15px; padding: 0; "
                            f"border: 2px solid {theme.SURFACE}; }}"
                            f"QPushButton:hover {{ border: 2px solid {theme.FAINT}; }}"
                            f"QPushButton:checked {{ background: {color}; border: 3px solid {theme.TEXT}; }}")
            b.setChecked(color == cur)
            b.clicked.connect(lambda _=False, c=color: self._set_accent(c))
            self._swatches.addButton(b); h.addWidget(b)
        h.addStretch(1)
        self.restart_btn = button("Restart to finish", self._restart, "ghost", icon="arrow-clockwise")
        h.addWidget(self.restart_btn); self.restart_btn.hide()
        return w

    def _set_accent(self, color: str) -> None:
        if color.lower() == (self.settings.accent or "").lower():
            return
        self.settings.accent = color
        self.settings.save()
        theme.apply(QApplication.instance(), color)
        self.restart_btn.show()
        notify(self, "Accent changed. Restart to recolor every icon.", "info")

    def _restart(self) -> None:
        if self.qm.is_running:
            notify(self, "Finish or stop the queue before restarting", "warn")
            return
        args = sys.argv[1:] if getattr(sys, "frozen", False) else ["-m", "ralenti", *sys.argv[1:]]
        if QProcess.startDetached(sys.executable, args):
            self.window().close()



# ---------- extensions ----------
def _ext_section(self):
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(12)
    self.ext_status = QLabel(); self.ext_status.setWordWrap(True); self.ext_status.setTextFormat(Qt.RichText)
    v.addWidget(self.ext_status)
    row = QHBoxLayout(); row.setSpacing(6)
    row.addWidget(button("Install from folder", self._ext_install_dir, icon="folder-open"))
    row.addWidget(button("Install from zip", self._ext_install_zip, icon="package"))
    self.ext_remove_btn = button("Remove", self._ext_remove, "ghost", icon="trash")
    row.addWidget(self.ext_remove_btn)
    row.addWidget(button("Open folder", self._ext_open, "ghost", icon="folder-open"))
    row.addStretch(1)
    self.ext_restart_btn = button("Restart to apply", self._restart, "accent", icon="arrow-clockwise")
    row.addWidget(self.ext_restart_btn)
    v.addLayout(row)
    hint = QLabel("Extensions add optional engines or features. They're kept in your Ralenti data folder, "
                  "so app updates don't remove them.")
    hint.setObjectName("Hint"); hint.setWordWrap(True)
    v.addWidget(hint)
    self.ext_restart_btn.hide()
    self._ext_refresh()
    return w


def _ext_refresh(self, changed: bool = False) -> None:
    api = extensions.load()
    if api.error:
        text = (f"<b style='color:{theme.DANGER}'>Couldn't load the extension</b> · {api.error}<br>"
                f"<span style='color:{theme.FAINT}'>Details in logs\\extensions.log</span>")
    elif api.name or api.engines:
        names = ", ".join(e.name for e in api.engines)
        text = f"<b style='color:{theme.OK}'>Loaded</b> · {api.name or 'Extension'}" + (f" · {names}" if names else "")
    elif extensions.installed():
        text = f"<b style='color:{theme.WARN}'>Installed</b> · restart Ralenti to load it"
    else:
        text = f"<span style='color:{theme.MUTED}'>No extension installed.</span>"
    if changed:
        text += f"<br><b style='color:{theme.WARN}'>Restart Ralenti to apply the change.</b>"
        self.ext_restart_btn.show()
    self.ext_status.setText(text)
    self.ext_remove_btn.setVisible(extensions.installed())


def _ext_install(self, path: str) -> None:
    try:
        extensions.install(path)
    except Exception as e:
        notify(self, f"Install failed: {e}", "error")
        return
    notify(self, "Extension installed. Restart Ralenti to load it.", "ok")
    self._ext_refresh(changed=True)


def _ext_install_dir(self) -> None:
    p = QFileDialog.getExistingDirectory(self, "Choose the extension folder (ralenti_ext or the folder containing it)")
    if p:
        self._ext_install(p)


def _ext_install_zip(self) -> None:
    p, _ = QFileDialog.getOpenFileName(self, "Choose an extension zip", "", "Zip files (*.zip)")
    if p:
        self._ext_install(p)


def _ext_remove(self) -> None:
    from PySide6.QtWidgets import QMessageBox
    if QMessageBox.question(self, "Ralenti", "Remove the installed extension?") != QMessageBox.StandardButton.Yes:
        return
    try:
        extensions.remove()
    except Exception as e:
        notify(self, f"Couldn't remove it: {e}", "error")
        return
    self._ext_refresh(changed=True)


def _ext_open(self) -> None:
    from ...core.config import EXT_DIR
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    os.startfile(EXT_DIR)


for _n, _f in {"_ext_section": _ext_section, "_ext_refresh": _ext_refresh, "_ext_install": _ext_install,
               "_ext_install_dir": _ext_install_dir, "_ext_install_zip": _ext_install_zip,
               "_ext_remove": _ext_remove, "_ext_open": _ext_open}.items():
    setattr(SettingsPage, _n, _f)
