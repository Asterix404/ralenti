"""Preset bar (in the editor header) and the dialog listing what a preset changes."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
                               QVBoxLayout, QWidget)

from ...core import edit_presets as P
from ..util import notify
from ..widgets.choice import RichCombo
from ..widgets.fields import button
from .summary import model_label


class ApplyDialog(QDialog):
    def __init__(self, parent, name, changes):
        super().__init__(parent)
        self.setWindowTitle("Apply preset")
        self.setMinimumWidth(540)
        v = QVBoxLayout(self); v.setContentsMargins(24, 22, 24, 18); v.setSpacing(12)
        t = QLabel(f"Apply “{name}”?"); t.setObjectName("Section")
        sub = QLabel("These settings will change. Your cuts, drawn crop, audio tracks, folder and file name stay "
                     "as they are. Untick a step to leave it alone. Ctrl+Z undoes everything.")
        sub.setObjectName("Hint"); sub.setWordWrap(True)
        v.addWidget(t); v.addWidget(sub)
        self.boxes = []
        for c in changes:
            card = QFrame(); card.setObjectName("SegRow")
            cl = QVBoxLayout(card); cl.setContentsMargins(12, 10, 12, 10); cl.setSpacing(4)
            cb = QCheckBox(c["title"]); cb.setChecked(True); cb.setStyleSheet("font-weight: 700;")
            lines = "<br>".join(f"{f}: <span style='text-decoration: line-through'>{old}</span> → <b>{new}</b>"
                                for f, old, new in c["changes"])
            lb = QLabel(lines); lb.setTextFormat(Qt.RichText); lb.setWordWrap(True); lb.setObjectName("Muted")
            lb.setContentsMargins(26, 0, 0, 0)
            cb.toggled.connect(lb.setEnabled); cb.toggled.connect(self._sync)
            cl.addWidget(cb); cl.addWidget(lb)
            v.addWidget(card)
            self.boxes.append((c["step"], cb))
        self.dont = QCheckBox("Don't ask again (turn it back on in Settings › Editor)")
        v.addWidget(self.dont)
        row = QHBoxLayout(); row.addStretch(1)
        row.addWidget(button("Cancel", self.reject, "ghost"))
        self.ok = button("Apply", self.accept, "accent", icon="check")
        row.addWidget(self.ok)
        v.addLayout(row)

    def _sync(self, *_) -> None:
        self.ok.setEnabled(any(cb.isChecked() for _s, cb in self.boxes))

    def steps(self) -> list[str]:
        return [s for s, cb in self.boxes if cb.isChecked()]


class PresetBar(QWidget):
    def __init__(self, session, settings):
        super().__init__()
        self.s, self.settings, self._applying, self._name = session, settings, False, None
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        lbl = QLabel("PRESET"); lbl.setObjectName("FieldLabel")
        self.combo = RichCombo(440); self.combo.setMinimumContentsLength(16)
        self.combo.activated.connect(self._pick)
        self.save_b = button("", self._save, "ghost", icon="floppy-disk")
        self.save_b.setToolTip("Save the current settings as a preset")
        self.star = button("", self._default, "ghost", icon="star")
        self.star.setToolTip("Make this preset the default for new videos")
        self.del_b = button("", self._delete, "ghost", icon="trash")
        self.del_b.setToolTip("Delete this preset")
        h.addWidget(lbl); h.addSpacing(4); h.addWidget(self.combo)
        for b in (self.save_b, self.star, self.del_b):
            h.addWidget(b)
        session.changed.connect(self._changed)
        self.fill()

    def fill(self, select=None) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.add("Custom", None, "Your current settings. Save them to reuse them on other videos.")
        default, groups = P.get_default(), P.all_presets()
        for title, builtin in (("Built-in", True), ("Your presets", False)):
            rows = [g for g in groups if g[2] == builtin]
            if rows:
                self.combo.add_header(title)
                for name, _r, _b, tag, blurb in rows:
                    self.combo.add(name, name, blurb, "Default" if name == default else tag)
        if not (select and self.combo.select(select)):
            self.combo.setCurrentIndex(0)
            select = None
        self.combo.blockSignals(False)
        self._name = select
        self.del_b.setEnabled(bool(select) and select not in P.BUILTIN)

    def _changed(self, _what) -> None:
        if not self._applying and self._name:
            self._name = None
            self.combo.setCurrentIndex(0)
            self.del_b.setEnabled(False)

    def _pick(self, i: int) -> None:
        name = self.combo.itemData(i)
        if not name:
            self._name = None
            self.del_b.setEnabled(False)
            return
        recipe = P.get(name)
        changes = P.diff(self.s.edit, recipe, lambda c: model_label(self.s.qm.tc, c))
        if not changes:
            self.fill(name)
            notify(self, f"Your settings already match “{name}”", "info")
            return
        steps = None
        if not getattr(self.settings, "edit_preset_noconfirm", False):
            d = ApplyDialog(self, name, changes)
            if not d.exec():
                self.fill(self._name)
                return
            steps = d.steps()
            if d.dont.isChecked():
                self.settings.edit_preset_noconfirm = True
                self.settings.save()
        self._applying = True
        try:
            self.s.apply_preset(recipe, steps)
        finally:
            self._applying = False
        self.fill(name)
        notify(self, f"Applied “{name}” · Ctrl+Z to undo", "ok")

    def _save(self) -> None:
        name, ok = QInputDialog.getText(self, "Save preset", "Name this preset:")
        name = name.strip()
        if not ok or not name:
            return
        if name in P.BUILTIN:
            QMessageBox.information(self, "Ralenti", "A built-in preset already uses that name.")
            return
        P.save_user(name, self.s.edit.recipe())
        self.fill(name)
        notify(self, f"Saved preset “{name}”", "ok")

    def _default(self) -> None:
        name = self._name
        if not name:
            notify(self, "Pick or save a preset first, then make it the default.", "info")
            return
        if P.get_default() == name:
            P.set_default(None)
            notify(self, f"“{name}” is no longer the default", "info")
        else:
            P.set_default(name)
            notify(self, f"New videos start with “{name}”", "ok")
        self.fill(name)

    def _delete(self) -> None:
        name = self._name
        if not name or name in P.BUILTIN:
            return
        if QMessageBox.question(self, "Ralenti", f"Delete the preset “{name}”?") != QMessageBox.StandardButton.Yes:
            return
        P.delete_user(name)
        self.fill()
        notify(self, f"Deleted “{name}”", "info")
