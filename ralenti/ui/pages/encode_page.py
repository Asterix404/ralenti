from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidgetItem, QMessageBox,
                               QVBoxLayout, QWidget)

from ...core import pipeline
from ...core.encoders import ENCODERS
from ...core.probe import probe
from ..space_check import confirm_space
from ..util import fmt_ts, notify
from ..widgets.choice import RichCombo
from ..widgets.drop_zone import DropZone, paths_from_mime
from ..widgets.fields import Field, button, page_header
from ..widgets.file_list import FileList
from ..widgets.panel import OutputField, Section, SummaryPanel, side_column
from ..widgets.pickers import QualityPicker, VmafPicker

AUDIO = [("auto", "Keep original when possible", "Recommended",
          "Copies the audio untouched if the container allows it; otherwise converts it."),
         ("encode", "Always convert", "", "AAC 256k for MP4, FLAC for MKV, PCM for MOV."),
         ("drop", "No audio", "", "Video only.")]
AUDIO_SHORT = {"auto": "Original audio when possible", "encode": "Audio converted", "drop": "No audio"}


class EncodePage(QWidget):
    def __init__(self, qm, settings):
        super().__init__()
        self.qm, self.settings = qm, settings
        self.infos = {}
        self.setAcceptDrops(True)
        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(8)
        root.addWidget(page_header("Encode", "Shrink videos without visible quality loss. Ralenti tests short "
                                   "samples and keeps the smallest file that still reaches your quality target."))
        body = QHBoxLayout(); body.setSpacing(18); root.addLayout(body, 1)

        left = QVBoxLayout(); left.setSpacing(10)
        drop = DropZone(min_height=100); drop.files_dropped.connect(self.add_files)
        self.files = FileList("No videos yet", "Drop videos to shrink them to a quality target.", "gauge")
        self.files.add_requested.connect(drop.browse)
        self.files.currentItemChanged.connect(lambda *_: self._sync())
        row = QHBoxLayout(); row.setSpacing(4)
        self.count = QLabel(); self.count.setObjectName("Muted")
        row.addWidget(self.count, 1)
        row.addWidget(button("Remove", self._remove, "ghost", icon="minus-circle"))
        row.addWidget(button("Clear all", self._clear, "ghost", icon="trash"))
        left.addWidget(drop); left.addWidget(self.files, 1); left.addLayout(row)
        body.addLayout(left, 1)

        content = QWidget(); cv = QVBoxLayout(content); cv.setContentsMargins(18, 14, 18, 16); cv.setSpacing(12)
        video = QWidget(); vv = QVBoxLayout(video); vv.setContentsMargins(0, 0, 0, 0); vv.setSpacing(14)
        self.encoder = RichCombo(); vv.addWidget(Field("Encoder", self.encoder))
        self.vmaf = VmafPicker(); vv.addWidget(Field("Quality", self.vmaf))
        self.quality = QualityPicker()
        self.q_field = Field("Constant quality level", self.quality)
        vv.addWidget(self.q_field)
        cv.addWidget(Section("encode.video", "Video", "film-slate", video, settings, divider=False))
        self.audio = RichCombo()
        for key, title, tag, blurb in AUDIO:
            self.audio.add(title, key, blurb, tag)
        cv.addWidget(Section("encode.audio", "Audio", "speaker-high", Field("Audio", self.audio), settings))
        self.out_dir = OutputField("dir")
        cv.addWidget(Section("encode.output", "Output", "export", Field("Save to", self.out_dir), settings))
        cv.addStretch(1)

        self.summary = SummaryPanel(); self.summary.add_clicked.connect(self._queue)
        body.addWidget(side_column(content, self.summary))

        self.encoder.currentIndexChanged.connect(self._enc_changed)
        for sig in (self.vmaf.changed, self.quality.changed, self.audio.currentIndexChanged):
            sig.connect(lambda *_: self._sync())
        self.reload_options(); self._count()

    def reload_options(self) -> None:
        cur = self.encoder.currentData() or "av1_nvenc"
        self.encoder.blockSignals(True); self.encoder.clear()
        for key, e in ENCODERS.items():
            if not e.hidden and self.qm.tc.binary_for(e.codec):
                self.encoder.add(e.label, key, e.blurb, e.tag)
        if not self.encoder.select(cur):
            self.encoder.setCurrentIndex(0)
        self.encoder.blockSignals(False)
        self._enc_changed()

    def _enc_changed(self, *_):
        e = ENCODERS.get(self.encoder.currentData() or "")
        if not e:
            return
        self.quality.set_encoder(e)
        if not e.has_quality:
            self.vmaf.set_available(False, f"{e.label} uses a fixed profile; there's no quality to tune.")
        elif not self.qm.tc.has_vmaf:
            self.vmaf.set_available(False, "VMAF needs the full ffmpeg build (see Settings).")
        else:
            self.vmaf.set_available(True)
        self._sync()

    def _sync(self, *_):
        e = ENCODERS.get(self.encoder.currentData() or "")
        if not e:
            return
        v = self.vmaf.effective()
        self.q_field.setVisible(e.has_quality and v is None)
        paths = self._paths()
        n = len(paths)
        it = self.files.currentItem()
        focus = it.data(Qt.UserRole) if it else (paths[0] if paths else None)
        if focus and focus in self.infos:
            i = self.infos[focus]
            self.summary.set_media(focus, Path(focus).name,
                                   f"{i.width}×{i.height} · {i.fps_float:.3f} fps · {i.vcodec} · "
                                   f"{fmt_ts(i.duration, False)}")
        else:
            self.summary.set_media(None)
        lines = []
        if n:
            total = sum(self.infos[p].duration for p in paths if p in self.infos)
            lines.append(("film-strip", "Files", f"<b>{n}</b> file{'s' if n != 1 else ''} · {fmt_ts(total, False)} total"))
        if v is not None:
            lines.append(("gauge", "Quality", f"<b>{e.label}</b> · VMAF {v:g}, sample tested"))
        elif e.has_quality:
            lines.append(("gauge", "Quality", f"<b>{e.label}</b> · {e.q_flag.upper()} {self.quality.value()}"))
        else:
            lines.append(("gauge", "Quality", f"<b>{e.label}</b> · fixed profile"))
        lines.append(("speaker-high", "Audio", AUDIO_SHORT.get(self.audio.currentData(), "")))
        lines.append(("export", "Save to", self.out_dir.text() or "Next to each source file"))
        self.summary.set_lines(lines)
        self.summary.set_action(f"Add {n} file{'s' if n != 1 else ''} to queue" if n
                                else "Add videos to get started", n > 0)

    # ---------- files ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if paths := paths_from_mime(e.mimeData()):
            self.add_files(paths)

    def _paths(self):
        return [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())]

    def add_files(self, paths):
        existing = set(self._paths())
        bad = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for p in paths:
                if p in existing:
                    continue
                info = probe(self.qm.tc.ffprobe, p)
                if not info.width:
                    bad.append(Path(p).name); continue
                self.infos[p] = info
                it = QListWidgetItem(f"{Path(p).name}\n{info.width}×{info.height} · {info.fps_float:.3f} fps · "
                                     f"{info.vcodec} · {fmt_ts(info.duration, False)}")
                it.setData(Qt.UserRole, p); it.setToolTip(p); self.files.addItem(it)
        finally:
            QApplication.restoreOverrideCursor()
        self._count()
        if bad:
            QMessageBox.warning(self, "Skipped", "These files aren't readable as video:\n" + "\n".join(bad))

    def _remove(self):
        for it in self.files.selectedItems():
            self.files.takeItem(self.files.row(it))
        self._count()

    def _clear(self):
        self.files.clear(); self._count()

    def _count(self):
        n = self.files.count()
        self.count.setText(f"{n} file{'s' if n != 1 else ''}" if n else "No files yet")
        self._sync()

    # ---------- queue ----------
    def _queue(self):
        paths = self._paths()
        if not paths:
            return
        key = self.encoder.currentData(); e = ENCODERS[key]
        v = self.vmaf.effective()
        opts = dict(encoder=key, vmaf=v, q=self.quality.value() if (e.has_quality and v is None) else None,
                    audio=self.audio.currentData(), out_dir=self.out_dir.text())
        errors, new = [], []
        for p in paths:
            try:
                new.append(pipeline.make_encode_job(self.qm.tc, self.settings, p, **opts))
            except Exception as ex:
                errors.append(f"{Path(p).name}: {ex}")
        if new and not confirm_space(self, self.qm, new):
            return
        for j in new:
            self.qm.add(j)
        self._clear()
        if new:
            notify(self, f"Added {len(new)} encode job{'s' if len(new) != 1 else ''}", "ok")
        if errors:
            QMessageBox.warning(self, "Some files were skipped", "\n".join(errors))
        if self.summary.autostart.isChecked():
            self.qm.start()