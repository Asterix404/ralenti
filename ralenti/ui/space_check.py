from PySide6.QtWidgets import QMessageBox

from ..core import space
from ..core.jobs import Status


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB"


def confirm_space(parent, qm, new_jobs) -> bool:
    """Warns before queueing jobs that probably won't fit. True = go ahead."""
    pending = [j for j in qm.jobs if j.status == Status.QUEUED] + list(new_jobs)
    short = space.shortfalls(pending)
    if not short:
        return True
    lines = "\n".join(f"•  {d}  needs about {_gb(n)}, only {_gb(f)} free" for d, n, f in short)
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Not enough disk space")
    box.setText("These jobs probably won't fit on your disk.")
    box.setInformativeText(
        f"{lines}\n\nThe estimate covers every queued output plus temporary files for VMAF. "
        "To fix it: free up space, choose another output or temp folder (Settings), lower the "
        "processing resolution, or turn VMAF off.\n\nIf space runs out anyway, Ralenti stops the "
        "job safely and you can resume it later.")
    add = box.addButton("Add anyway", QMessageBox.AcceptRole)
    box.addButton("Cancel", QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is add