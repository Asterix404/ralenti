"""First-run setup. FFmpeg is required; RIFE (free, open source) is the AI engine.
Missing tools download in one click. Extensions can add their own rows."""
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core import extensions, gpu
from ..core.rife import scan_models as rife_models
from ..core.tools import Toolchain
from .downloads import downloads
from .icons import pixmap
from .thumbs import thumbs
from .widgets.anim import SmoothBar
from .widgets.fields import button

STATUS = {"ok": ("check-circle", "ok"), "warn": ("warning-circle", "warn"), "bad": ("x-circle", "danger"),
          "info": ("info", "accent"), "busy": ("download", "accent"), "skip": ("minus-circle", "faint")}


class Row(QFrame):
    def __init__(self, title: str, tag: str = ""):
        super().__init__()
        self.setObjectName("SetupRow")
        v = QVBoxLayout(self); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(8)
        h = QHBoxLayout(); h.setSpacing(12)
        self.ic = QLabel(); self.ic.setFixedSize(24, 24)
        texts = QVBoxLayout(); texts.setSpacing(2)
        trow = QHBoxLayout(); trow.setSpacing(8)
        self.title = QLabel(title); self.title.setObjectName("SetupTitle")
        trow.addWidget(self.title)
        if tag:
            t = QLabel(tag); t.setObjectName("ToolTag"); t.setProperty("req", tag.startswith(("Required", "Recommended")))
            trow.addWidget(t)
        trow.addStretch(1)
        self.detail = QLabel(); self.detail.setObjectName("Hint"); self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        texts.addLayout(trow); texts.addWidget(self.detail)
        self.btns = QHBoxLayout(); self.btns.setSpacing(6)
        h.addWidget(self.ic, 0, Qt.AlignTop); h.addLayout(texts, 1); h.addLayout(self.btns)
        self.prog = QWidget()
        ph = QHBoxLayout(self.prog); ph.setContentsMargins(36, 0, 0, 0); ph.setSpacing(12)
        self.bar = SmoothBar()
        self.plbl = QLabel(); self.plbl.setObjectName("Hint"); self.plbl.setMinimumWidth(240)
        ph.addWidget(self.bar, 1); ph.addWidget(self.plbl)
        v.addLayout(h); v.addWidget(self.prog)
        self.prog.hide()

    def add(self, text, fn, kind=None, ic=None):
        b = button(text, fn, kind, icon=ic)
        self.btns.addWidget(b, 0, Qt.AlignVCenter)
        return b

    def set(self, status: str, detail: str) -> None:
        name, tone = STATUS[status]
        self.ic.setPixmap(pixmap(name, 22, tone))
        self.detail.setText(detail)

    def show_progress(self, d) -> None:
        self.prog.setVisible(d is not None)
        if d:
            self.bar.set_smooth(d[0] * 1000)
            self.plbl.setText(d[1])


def _heading(text: str, sub: str = "") -> QWidget:
    w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(2, 10, 2, 0); v.setSpacing(2)
    t = QLabel(text.upper()); t.setObjectName("FieldLabel"); v.addWidget(t)
    if sub:
        s = QLabel(sub); s.setObjectName("Hint"); s.setWordWrap(True); v.addWidget(s)
    return w


class SetupDialog(QDialog):
    def __init__(self, settings, qm, parent=None):
        super().__init__(parent)
        self.settings, self.qm = settings, qm
        self.mgr = downloads(settings)
        self._errors: dict[str, str] = {}
        self.setWindowTitle("Welcome to Ralenti")
        self.setMinimumWidth(760)
        v = QVBoxLayout(self); v.setContentsMargins(26, 24, 26, 22); v.setSpacing(10)
        head = QLabel("Let's get you set up"); head.setObjectName("PageTitle")
        sub = QLabel("Ralenti needs FFmpeg for everything it does, plus RIFE to interpolate. Both are free and "
                     "download in one click.")
        sub.setObjectName("PageSub"); sub.setWordWrap(True)
        v.addWidget(head); v.addWidget(sub)

        v.addWidget(_heading("Required"))
        self.r_ffmpeg = Row("FFmpeg · full build", "Required")
        self.ff_dl = self.r_ffmpeg.add("Download", lambda: self._download("ffmpeg"), "accent", "download")
        self.ff_cancel = self.r_ffmpeg.add("Cancel", lambda: self.mgr.cancel("ffmpeg"), "ghost", "x")
        self.r_ffmpeg.add("Locate", lambda: self._locate_file("full_ffmpeg", "ffmpeg.exe"), ic="folder-open")
        v.addWidget(self.r_ffmpeg)

        v.addWidget(_heading("AI engine", "Needed to interpolate. Trim, Merge and Encode work without it."))
        self.r_rife = Row("RIFE", "Recommended · free")
        self.rife_dl = self.r_rife.add("Download", lambda: self._download("rife"), "accent", "download")
        self.rife_cancel = self.r_rife.add("Cancel", lambda: self.mgr.cancel("rife"), "ghost", "x")
        self.r_rife.add("Locate", lambda: self._locate_file("rife_exe", "rife-ncnn-vulkan.exe"), ic="folder-open")
        v.addWidget(self.r_rife)
        self.ext_rows = []
        for factory in extensions.load().setup_rows:
            try:
                row = factory(self)
                v.addWidget(row)
                self.ext_rows.append(row)
            except Exception:
                traceback.print_exc()

        v.addWidget(_heading("System"))
        self.r_gpu = Row("Graphics card")
        v.addWidget(self.r_gpu)
        self.rows = {"ffmpeg": self.r_ffmpeg, "rife": self.r_rife}

        v.addSpacing(6)
        foot = QHBoxLayout(); foot.setSpacing(8)
        self.summary = QLabel(); self.summary.setObjectName("Muted"); self.summary.setWordWrap(True)
        self.done_btn = button("Start using Ralenti", self.accept, "accent", icon="check")
        foot.addWidget(self.summary, 1)
        foot.addWidget(button("Re-check", self.refresh, "ghost", icon="arrow-clockwise"))
        foot.addWidget(self.done_btn)
        v.addLayout(foot)
        self.mgr.progress.connect(self._on_progress)
        self.mgr.finished.connect(self._on_finished)
        self.refresh()

    # ---------- downloads ----------
    def _download(self, key: str) -> None:
        self._errors.pop(key, None)
        self.mgr.install(key)
        self.refresh()

    def _on_progress(self, key, *_):
        if row := self.rows.get(key):
            row.show_progress(self.mgr.describe(key))

    def _on_finished(self, key, ok, msg):
        if row := self.rows.get(key):
            row.show_progress(None)
        if ok:
            self._apply(save=False)
        else:
            if msg != "Cancelled":
                self._errors[key] = msg
            self.refresh()

    # ---------- locate ----------
    def _locate_file(self, key: str, what: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, f"Locate {what}", "", "Programs (*.exe)")
        if not path:
            return
        setattr(self.settings, key, path)
        if key == "full_ffmpeg" and (probe := Path(path).with_name("ffprobe.exe")).is_file():
            self.settings.full_ffprobe = str(probe)
        self._apply()

    def _apply(self, save: bool = True) -> None:
        if save:
            self.settings.save()
        self.qm.tc = Toolchain.resolve(self.settings)
        thumbs().configure(self.qm.tc)
        self.refresh()

    def _err(self, key: str, row: Row) -> None:
        if err := self._errors.get(key):
            row.detail.setText(row.detail.text() + f"\nLast attempt failed: {err}")

    # ---------- checks ----------
    def refresh(self) -> None:
        tc = self.qm.tc
        ff_busy, missing = self.mgr.busy("ffmpeg"), []
        if ff_busy:
            self.r_ffmpeg.set("busy", "Downloading the full build from Gyan's GitHub mirror…")
        elif tc.ffmpeg:
            missing = [n for n, ok in (("VMAF", tc.has_vmaf), ("crop detection", tc.has_cropdetect),
                                       ("Rubber Band audio", tc.has_rubberband)) if not ok]
            if missing:
                self.r_ffmpeg.set("warn", f"{tc.ffmpeg}\nThis build lacks {', '.join(missing)}. "
                                          "Download the full build to fix it.")
            else:
                self.r_ffmpeg.set("ok", f"{tc.ffmpeg} · VMAF, crop detection and Rubber Band available")
        else:
            self.r_ffmpeg.set("bad", "Required for everything: decoding, encoding, trimming, merging and quality "
                                     "checks. Download it in one click (about 200 MB), or locate an existing full "
                                     "build of ffmpeg.exe.")
        self._err("ffmpeg", self.r_ffmpeg)
        self.ff_dl.setVisible(not ff_busy and (not tc.ffmpeg or bool(missing)))
        self.ff_cancel.setVisible(ff_busy)
        self.r_ffmpeg.show_progress(self.mgr.describe("ffmpeg"))

        rife_busy = self.mgr.busy("rife")
        rmods = rife_models(tc.rife) if tc.rife else []
        if rife_busy:
            self.r_rife.set("busy", "Downloading RIFE ncnn-vulkan…")
        elif tc.rife and rmods:
            self.r_rife.set("ok", f"{tc.rife} · {len(rmods)} model{'s' if len(rmods) != 1 else ''}: "
                                  + ", ".join(r.label for r in rmods))
        elif tc.rife:
            self.r_rife.set("warn", f"{tc.rife}\nNo RIFE 4.x models were found next to it. Download RIFE again.")
        else:
            self.r_rife.set("info", "Free and open source (MIT). Fast frame interpolation on any Vulkan GPU: "
                                    "NVIDIA, AMD or Intel. No account or license needed. A few hundred MB.")
        self._err("rife", self.r_rife)
        self.rife_dl.setVisible(not rife_busy and not rmods)
        self.rife_cancel.setVisible(rife_busy)
        self.r_rife.show_progress(self.mgr.describe("rife"))
        for row in self.ext_rows:
            try:
                row.refresh()
            except Exception:
                traceback.print_exc()

        nv = gpu.nvidia_gpus()
        if nv:
            self.r_gpu.set("ok", " · ".join(f"GPU {g.index}: {g.short}, {g.vram_gb} GB" for g in nv))
        else:
            self.r_gpu.set("info", "No NVIDIA GPU detected. RIFE still runs on AMD and Intel GPUs through Vulkan. "
                                   "The NVENC encoders need NVIDIA; the CPU encoders work everywhere.")

        if not tc.ffmpeg:
            self.summary.setText("FFmpeg is required. Download it above or locate an existing ffmpeg.exe.")
            self.done_btn.setText("Continue anyway")
        elif not tc.has_engine:
            self.summary.setText("Trim, Merge and Encode are ready. Add RIFE (free) to interpolate.")
            self.done_btn.setText("Start using Ralenti")
        elif self.mgr.busy():
            self.summary.setText("Downloads keep going in the background if you continue.")
            self.done_btn.setText("Start using Ralenti")
        else:
            self.summary.setText("All set. You can reopen this from Settings → Diagnostics → Run setup.")
            self.done_btn.setText("Start using Ralenti")

    def done(self, r):
        for sig, slot in ((self.mgr.progress, self._on_progress), (self.mgr.finished, self._on_finished)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self.settings.setup_done = True
        self.settings.save()
        super().done(r)
