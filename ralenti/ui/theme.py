"""Ralenti design system: palette, generated SVG icons, stylesheet."""
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from ..core.config import APP_DIR

BG, SURFACE, SURFACE2, SURFACE3, BORDER = "#0c0e12", "#13161c", "#1a1e26", "#222731", "#2a303b"
TEXT, MUTED, FAINT = "#e9ecf2", "#8d96a6", "#5d6676"
OK, WARN, DANGER = "#34d399", "#fbbf24", "#f87171"
ON_ACCENT = "#04211d"
ACCENT = "#2dd4bf"

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="{c}" stroke-width="3" stroke-linecap="round" '
        'stroke-linejoin="round">{b}</svg>')
_ICONS = {"down": ('<polyline points="6 9 12 15 18 9"/>', MUTED),
          "up": ('<polyline points="6 15 12 9 18 15"/>', MUTED),
          "check": ('<polyline points="20 6 9 17 4 12"/>', ON_ACCENT)}


def _icons() -> str:
    d = APP_DIR / "theme"
    d.mkdir(parents=True, exist_ok=True)
    for name, (body, color) in _ICONS.items():
        color = ON_ACCENT if name == "check" else color
        (d / f"{name}.svg").write_text(_SVG.format(c=color, b=body), encoding="utf-8")
    return d.as_posix()


def _rgba(hex_: str, alpha: int) -> str:
    c = QColor(hex_)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


def qss(a: str, ic: str) -> str:
    hover, pressed = QColor(a).lighter(112).name(), QColor(a).darker(115).name()
    soft, soft_border = _rgba(a, 38), _rgba(a, 90)
    return f"""
* {{ color: {TEXT}; }}
QMainWindow, QWidget#Root, QStackedWidget > QWidget {{ background: {BG}; }}
QScrollArea, QWidget#qt_scrollarea_viewport, QWidget#ScrollBody {{ background: transparent; border: none; }}

QFrame#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QLabel#Brand {{ font-size: 17pt; font-weight: 700; padding: 22px 20px 0 20px; }}
QLabel#BrandSub {{ color: {FAINT}; font-size: 8.5pt; padding: 0 20px 18px 22px; }}
QPushButton#Nav {{ text-align: left; padding: 10px 14px; margin: 1px 10px; border: none;
    border-radius: 9px; background: transparent; color: {MUTED}; font-size: 10.5pt; }}
QPushButton#Nav:hover {{ background: {SURFACE2}; color: {TEXT}; }}
QPushButton#Nav:checked {{ background: {soft}; color: {a}; font-weight: 600; }}

QLabel#PageTitle {{ font-size: 21pt; font-weight: 700; }}
QLabel#PageSub {{ color: {MUTED}; font-size: 10pt; }}
QLabel#Section {{ font-size: 11.5pt; font-weight: 700; }}
QLabel#FieldLabel {{ color: {MUTED}; font-size: 8pt; font-weight: 700; }}
QLabel#Hint {{ color: {FAINT}; font-size: 9pt; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Badge {{ background: {SURFACE3}; border-radius: 6px; padding: 2px 8px; color: {MUTED}; font-size: 8.5pt; }}
QFrame#Divider {{ background: {BORDER}; border: none; min-height: 1px; max-height: 1px; }}

QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#Summary {{ background: {SURFACE}; border: 1px solid {soft_border}; border-radius: 14px; }}
QFrame#DropZone {{ background: {SURFACE}; border: 2px dashed {BORDER}; border-radius: 14px; }}
QFrame#DropZone[hover="true"] {{ border-color: {a}; background: {soft}; }}
QFrame#QueueStrip {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}

QPushButton {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 9px; padding: 8px 16px; }}
QPushButton:hover {{ background: {SURFACE3}; border-color: {FAINT}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; border-color: {SURFACE2}; }}
QPushButton:checked {{ background: {soft}; color: {a}; border-color: {a}; }}
QPushButton[accent="true"] {{ background: {a}; color: {ON_ACCENT}; border: none; font-weight: 700; padding: 10px 18px; }}
QPushButton[accent="true"]:hover {{ background: {hover}; }}
QPushButton[accent="true"]:pressed {{ background: {pressed}; }}
QPushButton[accent="true"]:disabled {{ background: {SURFACE3}; color: {FAINT}; }}
QPushButton[ghost="true"] {{ background: transparent; border: 1px solid transparent; color: {MUTED}; padding: 6px 10px; }}
QPushButton[ghost="true"]:hover {{ background: {SURFACE2}; color: {TEXT}; }}

QFrame#Seg {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 10px; }}
QPushButton#SegBtn {{ background: transparent; border: none; border-radius: 7px; padding: 6px 6px; color: {MUTED}; font-weight: 600; }}
QPushButton#SegBtn:hover {{ background: {SURFACE3}; color: {TEXT}; }}
QPushButton#SegBtn:checked {{ background: {a}; color: {ON_ACCENT}; }}
QPushButton#SegBtn:disabled {{ color: {FAINT}; }}
QPushButton#SegBtn:checked:disabled {{ background: {SURFACE3}; color: {MUTED}; }}

QFrame#Stepper {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 9px; }}
QPushButton#StepBtn {{ background: transparent; border: none; border-radius: 8px; min-width: 36px; max-width: 36px;
    padding: 5px 0; font-size: 14pt; font-weight: 600; color: {MUTED}; }}
QPushButton#StepBtn:hover {{ background: {SURFACE3}; color: {a}; }}
QDoubleSpinBox#StepSpin {{ background: transparent; border: none; font-weight: 600; padding: 4px 0; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QListWidget {{
    background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 9px; padding: 7px 10px;
    selection-background-color: {a}; selection-color: {ON_ACCENT}; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {FAINT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:on {{ border-color: {a}; }}
QComboBox:disabled, QLineEdit:disabled {{ color: {FAINT}; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: url({ic}/down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; padding: 4px; outline: 0; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 24px; border: none; background: transparent; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 24px; border: none; background: transparent; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({ic}/up.svg); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({ic}/down.svg); width: 10px; height: 10px; }}

QCheckBox {{ spacing: 9px; color: {MUTED}; }}
QCheckBox:hover {{ color: {TEXT}; }}
QCheckBox::indicator {{ width: 17px; height: 17px; border-radius: 5px; border: 1px solid {BORDER}; background: {SURFACE2}; }}
QCheckBox::indicator:hover {{ border-color: {a}; }}
QCheckBox::indicator:checked {{ background: {a}; border-color: {a}; image: url({ic}/check.svg); }}

QSlider::groove:horizontal {{ height: 6px; background: {SURFACE3}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {a}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; }}
QSlider::handle:horizontal:hover {{ background: {a}; }}

QListWidget::item {{ padding: 6px 6px; border-radius: 7px; }}
QListWidget::item:hover {{ background: {SURFACE3}; }}
QListWidget::item:selected {{ background: {soft}; color: {TEXT}; }}
QProgressBar {{ background: {SURFACE3}; border: none; border-radius: 3px; min-height: 6px; max-height: 6px; }}
QProgressBar::chunk {{ background: {a}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {FAINT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {soft}; color: {a}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QToolTip {{ background: {SURFACE3}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; border-radius: 6px; }}
QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 3px 9px; font-size: 13pt; }}
QToolButton:hover {{ background: {SURFACE3}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QSplitter::handle {{ background: transparent; }}

QPushButton#CollapseHeader {{ text-align: left; background: transparent; border: none; padding: 4px 0;
    font-size: 11.5pt; font-weight: 700; color: {TEXT}; }}
QPushButton#CollapseHeader:hover {{ color: {a}; }}
QPushButton#CollapseHeader:checked {{ background: transparent; border: none; color: {TEXT}; }}

QFrame#Toast {{ background: {SURFACE3}; border: 1px solid {BORDER}; border-left: 3px solid {a}; border-radius: 10px; }}
QFrame#Toast[kind="ok"] {{ border-left-color: {OK}; }}
QFrame#Toast[kind="warn"] {{ border-left-color: {WARN}; }}
QFrame#Toast[kind="error"] {{ border-left-color: {DANGER}; }}
QLabel#Brand {{ font-size: 16pt; font-weight: 700; padding: 0; }}
QLabel#BrandSub {{ color: {FAINT}; font-size: 8.5pt; padding: 0; }}
QPushButton#Nav {{ padding: 10px 14px; }}
QFrame#Overview {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#DropOverlay {{ background: {_rgba(BG, 225)}; border: 2px dashed {a}; border-radius: 18px; }}
QLabel#DropTitle {{ font-size: 16pt; font-weight: 700; color: {a}; background: transparent; }}
QWidget#Empty, QWidget#Empty QLabel {{ background: transparent; }}
QLabel#EmptyTitle {{ font-size: 12pt; font-weight: 700; }}
QLabel#Thumb {{ background: transparent; }}
QListWidget#FileList {{ padding: 4px; }}
QListWidget#FileList::item {{ padding: 6px; margin: 1px 0; }}
QProgressBar[state="done"]::chunk {{ background: {OK}; }}
QProgressBar[state="failed"]::chunk {{ background: {DANGER}; }}
QProgressBar[state="cancelled"]::chunk {{ background: {WARN}; }}
QProgressBar[state="queued"]::chunk {{ background: {FAINT}; }}
QPushButton#CollapseHeader {{ padding-left: 0; }}
QFrame#GpuMeter {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 12px; margin: 6px 12px 0 12px; }}
QFrame#GpuMeter QLabel {{ background: transparent; }}
QLabel#GpuName {{ font-weight: 600; font-size: 9pt; }}
QLabel#GpuStats {{ color: {MUTED}; font-size: 8.5pt; }}
QFrame#SetupRow {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#SetupRow QLabel {{ background: transparent; }}
QLabel#SetupTitle {{ font-weight: 600; font-size: 10.5pt; }}
QFrame#WarnRow {{ background: {_rgba(WARN, 22)}; border: 1px solid {_rgba(WARN, 90)}; border-radius: 10px; }}
QFrame#WarnRow QLabel {{ background: transparent; color: {TEXT}; }}
QLabel#SysLabel {{ color: {MUTED}; font-size: 8pt; font-weight: 700; }}
QLabel#SysValue {{ color: {MUTED}; font-size: 8.5pt; }}
QSlider#Volume::groove:horizontal {{ height: 4px; }}
QSlider#Volume::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; }}
QFrame#CompareBar {{ background: {soft}; border: 1px solid {soft_border}; border-radius: 10px; }}
QFrame#CompareBar QLabel {{ background: transparent; }}
QFrame#PreviewBar {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#PreviewBar QLabel {{ background: transparent; }}
QComboBox {{ combobox-popup: 0; }}
QWidget#ComboPopup {{ background: transparent; border: none; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 12px; padding: 6px; outline: 0; }}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 2px 10px; border-radius: 8px; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{ background: {SURFACE3}; color: {TEXT}; }}
QFrame#SectionHeader {{ background: transparent; border: none; border-radius: 8px; }}
QFrame#SectionHeader:hover {{ background: {SURFACE2}; }}
QFrame#SectionHeader QLabel {{ background: transparent; }}
QLabel#SectionTitle {{ font-size: 11.5pt; font-weight: 700; }}
QLabel#SummaryTitle {{ font-weight: 700; font-size: 10.5pt; }}
QFrame#Summary QLabel {{ background: transparent; }}
QDoubleSpinBox#StepSpin {{ min-width: 56px; }}
QDoubleSpinBox#StepSpin:disabled {{ color: {MUTED}; }}
QLabel#GpuBadge {{ background: {soft}; color: {a}; border-radius: 6px; padding: 1px 6px; font-size: 7.5pt; font-weight: 800; }}
QFrame#TakeStrip {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#TakeStrip QLabel {{ background: transparent; }}
QFrame#TakeCard {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#TakeCard:hover {{ border-color: {FAINT}; }}
QFrame#TakeCard[active="true"] {{ border: 2px solid {a}; background: {soft}; }}
QFrame#TakeCard QLabel {{ background: transparent; }}
QLabel#TakeTitle {{ font-weight: 600; font-size: 9.5pt; }}
QLabel#TakeState {{ font-size: 8.5pt; font-weight: 600; }}
QWidget#OptionsBody {{ background: {SURFACE}; }}
QFrame#FileBar {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#FileBar QLabel {{ background: transparent; }}
QFrame#Cta {{ background: {_rgba(BG, 238)}; border: 1px solid {soft_border}; border-radius: 14px; }}
QFrame#Cta QLabel {{ background: transparent; }}
QLabel#CtaTitle {{ font-weight: 700; font-size: 10.5pt; }}
QWidget#SumRows, QWidget#SumRows QLabel, QWidget#SumRows QWidget {{ background: transparent; }}
QLabel#SumLabel {{ color: {MUTED}; font-size: 7.5pt; font-weight: 800; padding-top: 2px; }}
QLabel#SumValue {{ color: {TEXT}; }}
QScrollArea#OptionsScroll, QWidget#OptionsViewport {{ background: {SURFACE}; border: none; }}
QFrame#SectionHeader {{ background: {SURFACE}; }}
QFrame#SectionHeader:hover {{ background: {SURFACE2}; }}
QFrame#TakeCard[versus="true"] {{ border: 2px dashed {a}; }}
QToolButton#SpeedBtn {{ font-size: 10pt; font-weight: 700; color: {MUTED}; min-width: 30px; }}
QToolButton#SpeedBtn:hover {{ color: {TEXT}; }}
QFrame#AboutHero {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {soft}, stop:1 {SURFACE});
    border: 1px solid {soft_border}; border-radius: 16px; }}
QFrame#AboutHero QLabel {{ background: transparent; }}
QLabel#AboutName {{ font-size: 26pt; font-weight: 800; }}
QLabel#AboutTag {{ color: {a}; font-size: 12pt; font-weight: 600; }}
QLabel#InfoKey {{ color: {MUTED}; font-size: 8pt; font-weight: 800; padding-top: 2px; }}
QFrame#ToolCard {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame#ToolCard QLabel {{ background: transparent; }}
QLabel#ToolIcon {{ background: {soft}; border-radius: 12px; }}
QLabel#ToolName {{ font-size: 12pt; font-weight: 700; }}
QLabel#ToolTag {{ background: {SURFACE3}; color: {MUTED}; border-radius: 6px; padding: 1px 8px; font-size: 8pt; font-weight: 700; }}
QLabel#ToolTag[req="true"] {{ background: {soft}; color: {a}; }}
QLabel#SavedLbl {{ color: {FAINT}; font-size: 9pt; }}
QFrame#Stepper {{ min-height: 40px; }}
QDoubleSpinBox#StepSpin {{ padding: 0px; min-height: 30px; font-size: 11pt; font-weight: 700; }}
QFrame#CompareBox {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#CompareBox QComboBox {{ background: transparent; border: none; border-radius: 8px; padding: 4px 8px; font-weight: 600; }}
QFrame#CompareBox QComboBox:hover {{ background: {SURFACE3}; }}
QFrame#CompareBox QComboBox:disabled {{ color: {MUTED}; }}
QFrame#CompareBox QToolButton {{ border-radius: 8px; padding: 3px 5px; }}
QFrame#CompareBox QToolButton:checked {{ background: {soft}; }}
QLabel#VsLabel {{ background: {soft}; color: {a}; border-radius: 8px; padding: 2px 8px; font-size: 7.5pt; font-weight: 800; }}
QPushButton#Nav[collapsed="true"] {{ text-align: center; padding: 10px 0; margin: 1px 12px; }}
"""


def apply(app: QApplication, accent: str = ACCENT) -> None:
    global ACCENT, ON_ACCENT
    ACCENT = accent
    ON_ACCENT = QColor(accent).darker(560).name()
    app.setStyle(_make_style())
    pal = QPalette()
    for role, color in [(QPalette.Window, BG), (QPalette.Base, SURFACE2), (QPalette.Text, TEXT),
                        (QPalette.WindowText, TEXT), (QPalette.Button, SURFACE2),
                        (QPalette.ButtonText, TEXT), (QPalette.Highlight, accent),
                        (QPalette.HighlightedText, ON_ACCENT), (QPalette.PlaceholderText, FAINT)]:
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    font = QFont()
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
    font.setPointSizeF(10)
    app.setFont(font)
    app.setStyleSheet(qss(accent, _icons()))


# ===================== v0.8.2: clicking a slider jumps to that point =====================
from PySide6.QtCore import Qt as _Qt  # noqa: E402
from PySide6.QtWidgets import QProxyStyle, QStyle  # noqa: E402


class _RalentiStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Slider_AbsoluteSetButtons:
            return _Qt.MouseButton.LeftButton.value
        return super().styleHint(hint, option, widget, returnData)


_style = None


def _make_style():
    global _style
    _style = _RalentiStyle("Fusion")
    return _style
