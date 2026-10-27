"""Sidebar system meter: GPU (nvidia-smi) with a load sparkline, plus CPU and RAM (psutil).
While Ralenti is processing, a light runs around the border and an IN USE badge shows."""
from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...core import gpu
from .. import theme
from ..icons import pixmap
from ..workers import run_async

try:
    import psutil
except ImportError:
    psutil = None


def _system():
    if psutil is None:
        return None
    vm = psutil.virtual_memory()
    return psutil.cpu_percent(interval=None), vm.used, vm.total


class Sparkline(QWidget):
    def __init__(self, n: int = 40):
        super().__init__()
        self.data = deque([0] * n, maxlen=n)
        self.setFixedHeight(28)

    def push(self, v: float) -> None:
        self.data.append(max(0, min(100, v)))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h, n = self.width(), self.height(), len(self.data)
        pts = [QPointF(i * (w - 1) / (n - 1), h - 2 - v / 100 * (h - 4)) for i, v in enumerate(self.data)]
        line = QPainterPath(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)
        area = QPainterPath(line); area.lineTo(w - 1, h); area.lineTo(0, h); area.closeSubpath()
        fill = QColor(theme.ACCENT); fill.setAlpha(45)
        p.fillPath(area, fill)
        p.setPen(QPen(QColor(theme.ACCENT), 1.6)); p.drawPath(line)


class MiniBar(QWidget):
    def __init__(self):
        super().__init__()
        self.frac, self.warn = 0.0, False
        self.setFixedHeight(6)

    def set(self, frac: float, warn: bool = False) -> None:
        self.frac, self.warn = max(0.0, min(1.0, frac)), warn
        self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()); rad = r.height() / 2
        p.setPen(Qt.NoPen); p.setBrush(QColor(theme.SURFACE3)); p.drawRoundedRect(r, rad, rad)
        if self.frac > 0:
            p.setBrush(QColor(theme.WARN if self.warn else theme.ACCENT))
            p.drawRoundedRect(QRectF(r.left(), r.top(), max(r.height(), r.width() * self.frac), r.height()), rad, rad)


class _Row(QWidget):
    def __init__(self, label: str):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        lbl = QLabel(label); lbl.setObjectName("SysLabel"); lbl.setFixedWidth(30)
        self.bar = MiniBar()
        self.val = QLabel(); self.val.setObjectName("SysValue"); self.val.setMinimumWidth(78)
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(lbl); h.addWidget(self.bar, 1); h.addWidget(self.val)


class GpuMeter(QFrame):
    def __init__(self, qm):
        super().__init__()
        self.qm = qm
        self.setObjectName("GpuMeter")
        self.setToolTip("GPU load over the last minute, memory and temperature, plus CPU and RAM use.")
        self._busy, self._gpu_misses = False, 0
        self._active, self._phase = False, 0.0
        v = QVBoxLayout(self); v.setContentsMargins(12, 10, 12, 10); v.setSpacing(7)

        self.gpu_box = QWidget()
        gv = QVBoxLayout(self.gpu_box); gv.setContentsMargins(0, 0, 0, 2); gv.setSpacing(5)
        top = QHBoxLayout(); top.setSpacing(6)
        ic = QLabel(); ic.setPixmap(pixmap("graphics-card", 15, "muted"))
        self.name = QLabel("GPU"); self.name.setObjectName("GpuName")
        self.badge = QLabel("IN USE"); self.badge.setObjectName("GpuBadge"); self.badge.hide()
        self.util = QLabel(); self.util.setObjectName("GpuName")
        top.addWidget(ic); top.addWidget(self.name, 1); top.addWidget(self.util)
        self.spark = Sparkline()
        self.stats = QLabel(); self.stats.setObjectName("GpuStats")
        gv.addLayout(top); gv.addWidget(self.spark)
        bot = QHBoxLayout(); bot.setSpacing(6); bot.addWidget(self.stats, 1); bot.addWidget(self.badge)
        gv.addLayout(bot)
        self.gpu_box.hide()

        self.cpu, self.ram = _Row("CPU"), _Row("RAM")
        v.addWidget(self.gpu_box); v.addWidget(self.cpu); v.addWidget(self.ram)
        if psutil is None:
            self.cpu.hide(); self.ram.hide()
        if psutil is None:
            self.hide()

        self.spin = QVariantAnimation(self)
        self.spin.setStartValue(0.0); self.spin.setEndValue(1.0)
        self.spin.setDuration(2400); self.spin.setLoopCount(-1)
        self.spin.valueChanged.connect(self._tick)

        self.timer = QTimer(self); self.timer.timeout.connect(self._poll); self.timer.start(2000)
        self.act = QTimer(self); self.act.timeout.connect(self._check_active); self.act.start(500)
        QTimer.singleShot(500, self._poll)

    # ---------- activity ----------
    def _check_active(self) -> None:
        on = bool(self.qm.is_running or getattr(self.qm, "preview_running", False))
        if on == self._active:
            return
        self._active = on
        self.badge.setVisible(on)
        self.setToolTip("Ralenti is processing on this GPU." if on else
                        "GPU load over the last minute, memory and temperature, plus CPU and RAM use.")
        if on:
            self.spin.start()
        else:
            self.spin.stop()
        self.update()

    def _tick(self, v) -> None:
        self._phase = float(v)
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._active:
            return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        c = QColor(theme.ACCENT)
        clear = QColor(c); clear.setAlpha(0)
        half = QColor(c); half.setAlpha(120)
        g = QConicalGradient(r.center(), -self._phase * 360)
        g.setColorAt(0.0, c); g.setColorAt(0.2, clear); g.setColorAt(0.5, half)
        g.setColorAt(0.7, clear); g.setColorAt(1.0, c)
        p.setPen(QPen(g, 1.5)); p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 11.5, 11.5)

    # ---------- polling ----------
    def _poll(self) -> None:
        self.timer.setInterval(1500 if self._active else 4000)
        win = self.window()
        if self._busy or (win and win.isMinimized()):
            return
        self._busy = True
        run_async(lambda: (gpu.stats(), gpu.nvidia_gpus(), _system()), self._got, self._err)

    def _err(self, _msg) -> None:
        self._busy = False

    def _got(self, result) -> None:
        self._busy = False
        stats, gpus, sysinfo = result
        if sysinfo:
            cpu, used, total = sysinfo
            self.cpu.bar.set(cpu / 100, cpu >= 95)
            self.cpu.val.setText(f"{cpu:.0f}%")
            frac = used / total if total else 0
            self.ram.bar.set(frac, frac >= 0.9)
            self.ram.val.setText(f"{used / 2**30:.1f} / {total / 2**30:.0f} GB")
        if stats:
            self._gpu_misses = 0
            try:
                idx = max(0, int(str(self.qm.settings.gpu_device).split(".")[0]))
            except ValueError:
                idx = 0
            s = next((x for x in stats if x.index == idx), stats[0])
            name = next((g.short for g in gpus if g.index == s.index), f"GPU {s.index}")
            self.name.setText(name if len(name) <= 24 else name[:23] + "…")
            self.util.setText(f"{s.util}%")
            self.spark.push(s.util)
            temp_color = theme.WARN if s.temp >= 83 else theme.MUTED
            power = f" · {s.power:.0f} W" if s.power else ""
            self.stats.setText(f"{s.mem_used / 1024:.1f} / {s.mem_total / 1024:.0f} GB · "
                               f"<span style='color:{temp_color}'>{s.temp}°C</span>{power}")
            self.gpu_box.show()
        else:
            self._gpu_misses += 1
            if self._gpu_misses >= 2:
                self.gpu_box.hide()
        self.setVisible(bool(stats) or bool(sysinfo))