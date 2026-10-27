"""Motion: fading page stack, gliding progress bars, collapsible sections, toasts."""
from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QParallelAnimationGroup, QPoint,
                            QPropertyAnimation, QSize, Qt, QTimer, Signal)
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from ..icons import icon, pixmap

OUT = QEasingCurve.OutCubic


class AnimatedStack(QStackedWidget):
    def fade_to(self, i: int) -> None:
        if i == self.currentIndex():
            return
        self.setCurrentIndex(i)
        w = self.currentWidget()
        eff = QGraphicsOpacityEffect(w); w.setGraphicsEffect(eff)
        a = QPropertyAnimation(eff, b"opacity", w)
        a.setDuration(180); a.setStartValue(0.0); a.setEndValue(1.0); a.setEasingCurve(OUT)
        a.finished.connect(lambda: w.setGraphicsEffect(None))
        a.start(QAbstractAnimation.DeleteWhenStopped)


class SmoothBar(QProgressBar):
    def __init__(self):
        super().__init__()
        self.setRange(0, 1000); self.setTextVisible(False)
        self._a = QPropertyAnimation(self, b"value", self)
        self._a.setDuration(380); self._a.setEasingCurve(OUT)

    def set_smooth(self, v) -> None:
        v = int(v)
        if v < self.value() or abs(v - self.value()) < 3:
            self._a.stop(); self.setValue(v); return
        self._a.stop()
        self._a.setStartValue(self.value()); self._a.setEndValue(v)
        self._a.start()


class Collapsible(QWidget):
    def __init__(self, title: str, content: QWidget, expanded: bool = False):
        super().__init__()
        self.title, self.content = title, content
        self.btn = QPushButton(title); self.btn.setObjectName("CollapseHeader")
        self.btn.setCheckable(True); self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setIconSize(QSize(16, 16)); self.btn.setChecked(expanded)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(10)
        v.addWidget(self.btn); v.addWidget(content)
        self.anim = QPropertyAnimation(content, b"maximumHeight", self)
        self.anim.setDuration(220); self.anim.setEasingCurve(OUT)
        self.anim.finished.connect(self._done)
        content.setMaximumHeight(16777215 if expanded else 0)
        content.setVisible(expanded)
        self.btn.toggled.connect(self._toggle)
        self._icon()

    def _icon(self):
        self.btn.setIcon(icon("caret-down" if self.btn.isChecked() else "caret-right", "text", checked="text"))

    def _toggle(self, on: bool):
        self._icon()
        self.anim.stop()
        if on:
            self.content.setVisible(True)
            self.anim.setStartValue(0)
            self.anim.setEndValue(max(1, self.content.sizeHint().height()))
        else:
            self.anim.setStartValue(self.content.height())
            self.anim.setEndValue(0)
        self.anim.start()

    def _done(self):
        if self.btn.isChecked():
            self.content.setMaximumHeight(16777215)
        else:
            self.content.setVisible(False)


class Toast(QFrame):
    closed = Signal(object)
    ICONS = {"ok": ("check-circle", "ok"), "warn": ("warning", "warn"),
             "error": ("x-circle", "danger"), "info": ("info", "accent")}

    def __init__(self, parent, text: str, kind: str = "info", ms: int = 3800):
        super().__init__(parent)
        self.setObjectName("Toast"); self.setProperty("kind", kind)
        self.setCursor(Qt.PointingHandCursor)
        name, tone = self.ICONS.get(kind, self.ICONS["info"])
        h = QHBoxLayout(self); h.setContentsMargins(14, 11, 16, 11); h.setSpacing(10)
        ic = QLabel(); ic.setPixmap(pixmap(name, 20, tone)); ic.setStyleSheet("background: transparent;")
        label = QLabel(text); label.setWordWrap(True); label.setMaximumWidth(360)
        label.setStyleSheet("background: transparent;")
        h.addWidget(ic, 0, Qt.AlignTop); h.addWidget(label, 1)
        self._eff = QGraphicsOpacityEffect(self); self._eff.setOpacity(0.0)
        self.setGraphicsEffect(self._eff)
        self._closing = False
        self.adjustSize(); self.raise_(); self.show()
        QTimer.singleShot(ms, self.fade_out)

    def pop_in(self) -> None:
        target = self.pos()
        self.move(target + QPoint(0, 18))
        group = QParallelAnimationGroup(self)
        a = QPropertyAnimation(self, b"pos"); a.setEndValue(target); a.setDuration(260); a.setEasingCurve(OUT)
        b = QPropertyAnimation(self._eff, b"opacity"); b.setStartValue(0.0); b.setEndValue(1.0); b.setDuration(200)
        group.addAnimation(a); group.addAnimation(b)
        group.start(QAbstractAnimation.DeleteWhenStopped)

    def fade_out(self) -> None:
        if self._closing:
            return
        self._closing = True
        a = QPropertyAnimation(self._eff, b"opacity", self)
        a.setEndValue(0.0); a.setDuration(260)
        a.finished.connect(self._done)
        a.start(QAbstractAnimation.DeleteWhenStopped)

    def _done(self):
        self.closed.emit(self)
        self.deleteLater()

    def mousePressEvent(self, e):
        self.fade_out()