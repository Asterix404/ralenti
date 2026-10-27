from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QLabel, QListWidgetItem,
                               QMessageBox, QVBoxLayout, QWidget)

from ...core import pipeline
from ...core.encoders import ENCODERS
from ...core.merge import compat_issues, pick_target
from ...core.probe import probe
from .. import theme
from ..space_check import confirm_space
from ..util import fmt_ts, notify
from ..widgets.choice import RichCombo
from ..widgets.drop_zone import DropZone, paths_from_mime
from ..widgets.fields import Field, button, page_header
from ..widgets.file_list import FileList
from ..widgets.panel import OutputField, Section, SummaryPanel, side_column
from ..widgets.pickers import QualityPicker

MODES = [("auto", "Auto", "Recommended", "Lossless when clips match; re-encodes only when they don't."),
         ("copy", "Lossless only", "", "Never re-encodes. Instant, but mismatched clips may play incorrectly."),
         ("reencode", "Always re-encode", "", "Converts every clip to one format. Slower, always plays correctly.")]
RES = [("first", "Match the first clip", "", "Uses clip 1's size and frame rate; others are scaled and padded."),
       ("largest", "Largest clip", "", "Uses the biggest resolution and highest frame rate in the list.")]


class MergePage(QWidget):
    def __init__(self, qm, settings):
        super().__init__()
        self.qm, self.settings = qm, settings
        self.infos = {}
        self._auto_out = True
        self.setAcceptDrops(True)
        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(8)
        root.addWidget(page_header("Merge", "Join clips end to end. Matching clips merge instantly with no "
                                   "quality loss; mismatched clips can be converted to one format."))
        body = QHBoxLayout(); body.setSpacing(18); root.addLayout(body, 1)

        left = QVBoxLayout(); left.setSpacing(10)
        drop = DropZone("Add clips", "drop or click · drag inside the list to reorder", min_height=90)
        drop.files_dropped.connect(self.add_files)
        self.files = FileList("No clips yet", "Add two or more clips. Drag them inside the list to change "
                              "the order.", "stack", "Add clips")
        self.files.add_requested.connect(drop.browse)
        self.files.setDragDropMode(QAbstractItemView.InternalMove)
        self.files.model().rowsMoved.connect(lambda *_: self._analyze())
        row = QHBoxLayout(); row.setSpacing(4)
        self.count = QLabel(); self.count.setObjectName("Muted")
        row.addWidget(self.count, 1)
        for text, fn, ic in (("Up", lambda: self._move(-1), "arrow-up"), ("Down", lambda: self._move(1), "arrow-down"),
                             ("Remove", self._remove, "minus-circle"), ("Clear all", self._clear, "trash")):
            row.addWidget(button(text, fn, "ghost", icon=ic))
        left.addWidget(drop); left.addWidget(self.files, 1); left.addLayout(row)
        body.addLayout(left, 1)

        content = QWidget(); cv = QVBoxLayout(content); cv.setContentsMargins(18, 14, 18, 16); cv.setSpacing(12)
        self.verdict = QLabel(); self.verdict.setWordWrap(True)
        cv.addWidget(Section("merge.check", "Compatibility", "seal-check", self.verdict, settings, divider=False))

        fmt = QWidget(); fv = QVBoxLayout(fmt); fv.setContentsMargins(0, 0, 0, 0); fv.setSpacing(14)
        self.mode, self.res, self.encoder = RichCombo(), RichCombo(), RichCombo()
        for combo, items in ((self.mode, MODES), (self.res, RES)):
            for key, title, tag, blurb in items:
                combo.add(title, key, blurb, tag)
        self.quality = QualityPicker()
        fv.addWidget(Field("Mode", self.mode))
        self.lossless_note = QLabel("Clips are joined exactly as they are: no re-encode, no quality loss.")
        self.lossless_note.setObjectName("Hint"); self.lossless_note.setWordWrap(True)
        fv.addWidget(self.lossless_note)
        self.re_box = QWidget(); rv = QVBoxLayout(self.re_box); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(14)
        rv.addWidget(Field("Re-encode to", self.res)); rv.addWidget(Field("Encoder", self.encoder))
        rv.addWidget(Field("Quality", self.quality))
        fv.addWidget(self.re_box)
        cv.addWidget(Section("merge.format", "Format", "sliders", fmt, settings))

        self.out = OutputField("file", "Output file", "Videos (*.mp4 *.mkv *.mov)")
        self.out.edit.textEdited.connect(lambda _: setattr(self, "_auto_out", False))
        cv.addWidget(Section("merge.output", "Output", "export", Field("Save as", self.out), settings))
        cv.addStretch(1)

        self.summary = SummaryPanel(); self.summary.add_clicked.connect(self._queue)
        body.addWidget(side_column(content, self.summary))

        self.mode.currentIndexChanged.connect(self._analyze)
        self.res.currentIndexChanged.connect(self._analyze)
        self.encoder.currentIndexChanged.connect(self._enc_changed)
        self.quality.changed.connect(self._analyze)
        self.reload_options()

    # ---------- options ----------
    def reload_options(self) -> None:
        cur = self.encoder.currentData() or "hevc_nvenc"
        self.encoder.blockSignals(True); self.encoder.clear()
        for key, e in ENCODERS.items():
            if not e.hidden and self.qm.tc.binary_for(e.codec):
                self.encoder.add(e.label, key, e.blurb, e.tag)
        if not self.encoder.select(cur):
            self.encoder.setCurrentIndex(0)
        self.encoder.blockSignals(False)
        self._enc_changed()

    def _enc_changed(self, *_) -> None:
        self.quality.set_encoder(ENCODERS.get(self.encoder.currentData() or ""))
        self._analyze()

    # ---------- files ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if paths := paths_from_mime(e.mimeData()):
            self.add_files(paths)

    def add_files(self, paths):
        bad = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for p in paths:
                if p not in self.infos:
                    info = probe(self.qm.tc.ffprobe, p)
                    if not info.width:
                        bad.append(Path(p).name); continue
                    self.infos[p] = info
                it = QListWidgetItem(); it.setData(Qt.UserRole, p); self.files.addItem(it)
        finally:
            QApplication.restoreOverrideCursor()
        self._analyze()
        if bad:
            QMessageBox.warning(self, "Skipped", "These files aren't readable as video:\n" + "\n".join(bad))

    def _paths(self):
        return [self.files.item(i).data(Qt.UserRole) for i in range(self.files.count())]

    def _move(self, d):
        r = self.files.currentRow()
        if r < 0 or not 0 <= r + d < self.files.count():
            return
        self.files.insertItem(r + d, self.files.takeItem(r))
        self.files.setCurrentRow(r + d)
        self._analyze()

    def _remove(self):
        for it in self.files.selectedItems():
            self.files.takeItem(self.files.row(it))
        self._analyze()

    def _clear(self):
        self.files.clear(); self._auto_out = True; self._analyze()

    # ---------- analysis ----------
    def _reencode(self, issues) -> bool:
        mode = self.mode.currentData()
        return mode == "reencode" or (mode == "auto" and bool(issues))

    def _analyze(self, *_):
        paths = self._paths()
        for i, p in enumerate(paths):
            info = self.infos[p]
            self.files.item(i).setText(
                f"{i + 1}.  {Path(p).name}\n{info.width}×{info.height} · {info.fps_float:.3f} fps · "
                f"{info.vcodec}/{info.acodec or 'no audio'} · {fmt_ts(info.duration, False)}")
        n = len(paths)
        total = sum(self.infos[p].duration for p in paths)
        self.count.setText(f"{n} clips · {fmt_ts(total, False)}" if n else "No clips yet")

        issues = compat_issues([self.infos[p] for p in paths]) if n >= 2 else []
        reencode = self._reencode(issues) if n >= 2 else self.mode.currentData() == "reencode"
        self.re_box.setVisible(reencode)
        self.lossless_note.setVisible(not reencode)
        e = ENCODERS.get(self.encoder.currentData() or "")

        if n < 2:
            self.verdict.setText("Add at least two clips to check whether they can be joined losslessly.")
            self.verdict.setStyleSheet(f"color: {theme.MUTED};")
        elif not issues:
            text = "All clips match: lossless merge, no quality loss."
            if reencode:
                text += "\nRe-encoding anyway (forced in Mode)."
            self.verdict.setText(text); self.verdict.setStyleSheet(f"color: {theme.OK};")
        else:
            text = "Clips differ:\n• " + "\n• ".join(issues)
            text += ("\nThey'll be converted to one format." if reencode
                     else "\nA lossless merge will probably play incorrectly.")
            self.verdict.setText(text)
            self.verdict.setStyleSheet(f"color: {theme.WARN if reencode else theme.DANGER};")

        if self._auto_out and paths:
            first = Path(paths[0])
            ext = e.ext if reencode and e else first.suffix
            self.out.setText(str(first.with_name(f"{first.stem}_merged{ext}")))
        self._summary(paths, total, issues, reencode, e)

    def _summary(self, paths, total, issues, reencode, e) -> None:
        n = len(paths)
        if not paths:
            self.summary.set_media(None)
        else:
            i = self.infos[paths[0]]
            self.summary.set_media(paths[0], f"{n} clip{'s' if n != 1 else ''} · {fmt_ts(total, False)}",
                                   f"Starts with {Path(paths[0]).name}")
        lines, warn = [], None
        if n >= 2:
            if reencode and e:
                t = pick_target([self.infos[p] for p in paths], self.res.currentData())
                q = self.quality.value()
                lines += [("arrows-out", "Format", f"<b>{t.width}×{t.height}</b> · {float(t.rate):.3f} fps"),
                          ("gauge", "Quality", f"<b>{e.label}</b>" + (f" · {e.q_flag.upper()} {q}" if q is not None else ""))]
            else:
                lines.append(("seal-check", "Mode", "Lossless join · no re-encode · instant"))
            lines.append(("export", "File", Path(self.out.text()).name if self.out.text() else ""))
            if issues and not reencode:
                warn = "Clips don't match: the result may not play correctly."
        self.summary.set_lines(lines, warn)
        self.summary.set_action(f"Merge {n} clips" if n >= 2 else "Add at least two clips", n >= 2)

    # ---------- queue ----------
    def _queue(self):
        paths = self._paths()
        out = self.out.text()
        if len(paths) < 2 or not out:
            return
        mode = self.mode.currentData()
        if mode == "copy" and compat_issues([self.infos[p] for p in paths]):
            r = QMessageBox.question(self, "Ralenti", "These clips don't match. A lossless merge will "
                                     "probably play incorrectly. Continue anyway?")
            if r != QMessageBox.StandardButton.Yes:
                return
        key = self.encoder.currentData()
        try:
            job = pipeline.make_merge_job(self.qm.tc, self.settings, paths, out, mode=mode, encoder=key,
                                          q=self.quality.value(), res=self.res.currentData())
        except Exception as ex:
            QMessageBox.warning(self, "Ralenti", str(ex)); return
        if not confirm_space(self, self.qm, [job]):
            return
        self.qm.add(job)
        notify(self, "Merge added to the queue", "ok")
        self._clear()
        if self.summary.autostart.isChecked():
            self.qm.start()