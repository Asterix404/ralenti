"""Dropdowns: rounded, floating popup that opens below the box with a short fade and slide,
scrolls after 9 items. RichCombo items show a title, a description and a tag pill."""
from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QObject, QParallelAnimationGroup,
                            QPoint, QPropertyAnimation, QRect, QSize, Qt)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QFrame, QStyle, QStyledItemDelegate

from .. import theme

BLURB = Qt.UserRole + 1
TAG = Qt.UserRole + 2
HEADER = Qt.UserRole + 3


def polish_popup(combo: QComboBox) -> None:
    """Frameless, translucent popup container so the stylesheet's rounded corners show."""
    if combo.property("_rl_popup"):
        return
    combo.setProperty("_rl_popup", True)
    view = combo.view()
    c = view.parentWidget()
    if c is None:
        return
    c.setObjectName("ComboPopup")
    c.setWindowFlags(c.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    c.setAttribute(Qt.WA_TranslucentBackground)
    if isinstance(c, QFrame):
        c.setFrameShape(QFrame.NoFrame)
    if c.layout():
        c.layout().setContentsMargins(0, 0, 0, 0)
    view.viewport().setAutoFillBackground(False)
    view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)


class ComboPolisher(QObject):
    """App-wide: gives every QComboBox the rounded popup, even plain ones."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Polish and isinstance(obj, QComboBox):
            polish_popup(obj)
        return False


class NiceCombo(QComboBox):
    def __init__(self, popup_width: int = 0):
        super().__init__()
        self._pw = popup_width
        self.setMaxVisibleItems(9)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(12)
        polish_popup(self)

    def showPopup(self):
        super().showPopup()
        c = self.view().window()
        scr = (self.screen() or c.screen()).availableGeometry()
        w = min(max(self.width(), self._pw), scr.width() - 16)
        h = c.height()
        below = self.mapToGlobal(QPoint(0, self.height() + 6))
        down = below.y() + h <= scr.bottom()
        y = below.y() if down else self.mapToGlobal(QPoint(0, -h - 6)).y()
        x = min(max(scr.left() + 8, below.x()), scr.right() - w - 8)
        end = QRect(x, y, w, h)
        c.setGeometry(end.translated(0, -8 if down else 8))
        c.setWindowOpacity(0.0)
        g = QParallelAnimationGroup(c)
        a = QPropertyAnimation(c, b"geometry"); a.setDuration(160); a.setEndValue(end)
        a.setEasingCurve(QEasingCurve.OutCubic)
        b = QPropertyAnimation(c, b"windowOpacity"); b.setDuration(130)
        b.setStartValue(0.0); b.setEndValue(1.0)
        g.addAnimation(a); g.addAnimation(b)
        g.start(QAbstractAnimation.DeleteWhenStopped)

    def select(self, data) -> bool:
        i = self.findData(data)
        if i >= 0:
            self.setCurrentIndex(i)
        return i >= 0


class _Delegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        if index.data(HEADER):
            return QSize(option.rect.width(), 30)
        return QSize(option.rect.width(), 54 if index.data(BLURB) else 36)

    def paint(self, p, option, index):
        p.save()
        if index.data(HEADER):
            f = QFont(option.font); f.setPointSizeF(max(7.0, option.font.pointSizeF() * 0.78))
            f.setWeight(QFont.Bold); p.setFont(f); p.setPen(QColor(theme.FAINT))
            p.drawText(option.rect.adjusted(14, 6, -10, 0), Qt.AlignLeft | Qt.AlignVCenter,
                       index.data(Qt.DisplayRole) or "")
            p.restore(); return
        p.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(2, 2, -2, -2)
        st = option.state
        selected = bool(st & QStyle.StateFlag.State_Selected)
        if selected or st & QStyle.StateFlag.State_MouseOver:
            p.setPen(Qt.NoPen); p.setBrush(QColor(theme.SURFACE3)); p.drawRoundedRect(r, 8, 8)
        if selected:
            p.setBrush(QColor(theme.ACCENT))
            p.drawRoundedRect(r.left() + 3, r.center().y() - 8, 3, 16, 1.5, 1.5)

        title = index.data(Qt.DisplayRole) or ""
        blurb = index.data(BLURB) or ""
        tag = index.data(TAG) or ""
        base = max(7.0, option.font.pointSizeF())
        tf = QFont(option.font); tf.setWeight(QFont.DemiBold)
        p.setFont(tf); fm = p.fontMetrics()
        line = QRect(r.left() + 14, r.top() + (8 if blurb else 0), r.width() - 26,
                     fm.height() if blurb else r.height())
        p.setPen(QColor(theme.TEXT))
        p.drawText(line, Qt.AlignLeft | Qt.AlignVCenter, title)
        x = line.left() + fm.horizontalAdvance(title) + 10
        if tag:
            gf = QFont(option.font); gf.setPointSizeF(base * 0.78); gf.setWeight(QFont.Bold)
            p.setFont(gf); gm = p.fontMetrics()
            w, h = gm.horizontalAdvance(tag) + 14, gm.height() + 2
            pill = QRect(x, line.center().y() - h // 2, w, h)
            bg = QColor(theme.ACCENT); bg.setAlpha(36)
            p.setPen(Qt.NoPen); p.setBrush(bg); p.drawRoundedRect(pill, h / 2, h / 2)
            p.setPen(QColor(theme.ACCENT)); p.drawText(pill, Qt.AlignCenter, tag)
        if blurb:
            bf = QFont(option.font); bf.setPointSizeF(base * 0.88)
            p.setFont(bf); bm = p.fontMetrics()
            br = QRect(r.left() + 14, line.bottom() + 4, r.width() - 26, bm.height())
            p.setPen(QColor(theme.MUTED))
            p.drawText(br, Qt.AlignLeft | Qt.AlignVCenter, bm.elidedText(blurb, Qt.ElideRight, br.width()))
        p.restore()


class RichCombo(NiceCombo):
    def __init__(self, popup_width: int = 460):
        super().__init__(popup_width)
        self.setItemDelegate(_Delegate(self))

    def add(self, title: str, data=None, blurb: str = "", tag: str = "") -> None:
        self.addItem(title, data)
        i = self.count() - 1
        self.setItemData(i, blurb, BLURB)
        self.setItemData(i, tag, TAG)
        self.setItemData(i, blurb or title, Qt.ToolTipRole)

    def add_header(self, title: str) -> None:
        self.addItem(title.upper(), None)
        i = self.count() - 1
        self.setItemData(i, True, HEADER)
        item = self.model().item(i) if hasattr(self.model(), 'item') else None
        if item is not None:
            item.setFlags(Qt.NoItemFlags)

    def blurb(self) -> str:
        return self.currentData(BLURB) or ""


# ===================== v0.8.2: the mouse wheel scrolls the page, never changes a dropdown =====================
from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QAbstractScrollArea  # noqa: E402

_cp_filter_v81 = ComboPolisher.eventFilter


def _cp_filter_v82(self, obj, ev):
    if ev.type() == QEvent.Wheel and isinstance(obj, QComboBox) and not obj.view().isVisible():
        w = obj.parentWidget()
        while w is not None and not isinstance(w, QAbstractScrollArea):
            w = w.parentWidget()
        if w is not None:
            QCoreApplication.sendEvent(w.verticalScrollBar(), ev)
        return True
    return _cp_filter_v81(self, obj, ev)


ComboPolisher.eventFilter = _cp_filter_v82
