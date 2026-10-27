from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from ...core import pipeline
from ...core.keyframes import prev_key
from ...core.probe import probe
from ..util import fmt_ts, notify, parse_ts
from ..widgets.drop_zone import DropZone, paths_from_mime
from ..widgets.fields import Field, button, page_header
from ..widgets.panel import OutputField, Section, SummaryPanel, side_column
from ..widgets.player import PlayerPanel


class TrimPage(QWidget):
    def __init__(self, qm, settings):
        super().__init__()
        self.qm, self.settings = qm, settings
        self.path = self.info = None
        self.setAcceptDrops(True)
        root = QVBoxLayout(self); root.setContentsMargins(28, 22, 28, 14); root.setSpacing(8)
        root.addWidget(page_header("Trim", "Cut without re-encoding: instant and lossless. Mark the start "
                                   "and end with I and O in the player; each segment becomes a queue job."))
        body = QHBoxLayout(); body.setSpacing(18); root.addLayout(body, 1)

        left = QVBoxLayout(); left.setSpacing(10)
        self.player = PlayerPanel(qm); self.player.snap_in = True
        self.player.range_changed.connect(lambda *_: self._sync())
        self.drop = DropZone("Open a video", "drop a file or click to browse", min_height=64)
        self.drop.files_dropped.connect(lambda ps: self.open(ps[0]))
        left.addWidget(self.player, 1); left.addWidget(self.drop)
        body.addLayout(left, 1)

        content = QWidget(); cv = QVBoxLayout(content); cv.setContentsMargins(18, 14, 18, 16); cv.setSpacing(12)
        rng = QWidget(); rv = QVBoxLayout(rng); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(12)
        self.start_edit, self.end_edit = QLineEdit(), QLineEdit()
        for title, edit, fn, key in (("Start", self.start_edit, self.player.set_in, "I"),
                                     ("End", self.end_edit, self.player.set_out, "O")):
            edit.setPlaceholderText("HH:MM:SS.mmm"); edit.editingFinished.connect(self._apply_edits)
            w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            b = button("Playhead", fn, icon="crosshair-simple"); b.setToolTip(f"Use the current playhead ({key})")
            h.addWidget(edit, 1); h.addWidget(b)
            rv.addWidget(Field(title, w))
        self.snap = QCheckBox("Snap start to keyframe (recommended)"); self.snap.setChecked(True)
        self.snap.toggled.connect(self._snap_toggled)
        self.len_lbl = QLabel(); self.len_lbl.setObjectName("Hint"); self.len_lbl.setWordWrap(True)
        rv.addWidget(self.snap); rv.addWidget(self.len_lbl)
        cv.addWidget(Section("trim.range", "Range", "scissors", rng, settings, divider=False))

        self.out_dir = OutputField("dir"); self.out_dir.changed.connect(lambda *_: self._sync())
        self.out_field = Field("Save to", self.out_dir)
        cv.addWidget(Section("trim.output", "Output", "export", self.out_field, settings))
        cv.addStretch(1)

        self.summary = SummaryPanel(); self.summary.add_clicked.connect(self._queue)
        body.addWidget(side_column(content, self.summary))
        self._sync()

    # ---------- file ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        if paths := paths_from_mime(e.mimeData()):
            self.open(paths[0])

    def open(self, path: str) -> None:
        info = probe(self.qm.tc.ffprobe, path)
        if not info.width:
            QMessageBox.warning(self, "Ralenti", f"Not readable as video:\n{path}"); return
        self.path, self.info = path, info
        self.player.load(path, info)
        self._sync()

    # ---------- range ----------
    def _range(self) -> tuple[float, float]:
        s = self.player.in_s or 0.0
        e = self.player.out_s if self.player.out_s is not None else self.info.duration
        return s, e

    def _snap_toggled(self, on: bool) -> None:
        self.player.snap_in = on
        self._apply_edits()

    def _apply_edits(self) -> None:
        if not self.info:
            return
        d = self.info.duration
        try:
            s = max(0.0, min(parse_ts(self.start_edit.text() or "0"), d))
            e = max(0.0, min(parse_ts(self.end_edit.text() or str(d)), d))
        except ValueError:
            self._sync(); return
        if self.snap.isChecked() and self.player.keys:
            s = prev_key(self.player.keys, s)
        if e <= s:
            e = d
        self.player.set_range(s if s > 0.001 else None, e if e < d - 0.001 else None)
        self._sync()

    def _out_path(self, s: float, e: float) -> Path:
        src = Path(self.path)
        d = Path(self.out_dir.text()) if self.out_dir.text() else src.parent
        return d / f"{src.stem}_cut_{int(s)}s-{int(e)}s{src.suffix}"

    def _sync(self) -> None:
        if not self.info:
            for w in (self.start_edit, self.end_edit):
                w.clear()
            self.len_lbl.setText("Open a video, then press I and O in the player.")
            self.out_field.set_hint("")
            self.summary.set_media(None)
            self.summary.set_lines([("seal-check", "Mode", "Lossless · no re-encode · instant")])
            self.summary.set_action("Open a video to get started", False)
            return
        s, e = self._range()
        self.start_edit.setText(fmt_ts(s)); self.end_edit.setText(fmt_ts(e))
        note = None
        if not self.player.keys:
            self.len_lbl.setText("Loading keyframes…")
        else:
            k = prev_key(self.player.keys, s)
            if not self.snap.isChecked() and abs(k - s) > 0.01:
                note = f"Lossless output will actually start at the keyframe {fmt_ts(k)}"
            self.len_lbl.setText("Lossless cuts can only start on a keyframe (the ticks under the timeline).")
        out = self._out_path(s, e)
        self.out_field.set_hint(f"File name: {out.name}")
        i = self.info
        self.summary.set_media(self.path, Path(self.path).name,
                               f"{i.width}×{i.height} · {i.fps_float:.3f} fps · {fmt_ts(i.duration, False)}")
        self.summary.set_lines([("scissors", "Segment", f"{fmt_ts(s)} → {fmt_ts(e)}"),
                                ("timer", "Length", f"<b>{fmt_ts(e - s, False)}</b> of {fmt_ts(i.duration, False)}"),
                                ("seal-check", "Mode", "Lossless · no re-encode · instant"),
                                ("export", "File", out.name)], note)
        self.summary.set_action("Add segment to queue", e - s >= 0.05)

    # ---------- queue ----------
    def _queue(self) -> None:
        if not self.path:
            return
        s, e = self._range()
        if e - s < 0.05:
            return
        out = pipeline.unique_path(self._out_path(s, e))
        self.qm.add(pipeline.make_cut_job(self.qm.tc, self.settings, self.path, s, e, str(out)))
        notify(self, f"Segment {fmt_ts(s, False)}–{fmt_ts(e, False)} added", "ok")
        if self.summary.autostart.isChecked():
            self.qm.start()