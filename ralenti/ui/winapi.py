"""Windows integration: dark title bars, taskbar progress, sleep/shutdown. No-ops elsewhere."""
import ctypes
import os
import subprocess
import sys
import uuid
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor

from ..core.tools import NO_WINDOW
from . import theme

WIN = sys.platform == "win32"


def _colorref(hex_: str) -> ctypes.c_uint:
    c = QColor(hex_)
    return ctypes.c_uint(c.red() | (c.green() << 8) | (c.blue() << 16))


def style_title_bar(widget) -> None:
    if not WIN:
        return
    try:
        hwnd = wintypes.HWND(int(widget.winId()))
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (newer / older Windows 10 builds)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
        for attr, color in ((35, theme.SURFACE), (36, theme.TEXT), (34, theme.BORDER)):  # Win11 only
            ref = _colorref(color)
            dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ref), ctypes.sizeof(ref))
    except Exception:
        pass


class TitleBarStyler(QObject):
    """App-wide filter: every window and dialog gets the dark title bar once."""

    def eventFilter(self, obj, ev):
        if (os.environ.get("RALENTI_DEBUG_WINDOWS") and ev.type() == QEvent.Show and obj.isWidgetType()
                and obj.isWindow()):
            print(f"[ralenti] window shown: {type(obj).__name__} {obj.objectName()!r} "
                  f"{obj.width()}x{obj.height()} {obj.windowType()}", file=sys.stderr)
        if (ev.type() == QEvent.Show and obj.isWidgetType() and obj.isWindow()
                and obj.windowType() in (Qt.Window, Qt.Dialog) and not obj.property("_rl_styled")):
            obj.setProperty("_rl_styled", True)
            style_title_bar(obj)
        return False


class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort),
                ("d4", ctypes.c_ubyte * 8)]


def _guid(s: str) -> _GUID:
    return _GUID.from_buffer_copy(uuid.UUID(s).bytes_le)


class TaskbarProgress:
    """ITaskbarList3 via raw COM: the green fill on the taskbar button."""
    NONE, INDETERMINATE, NORMAL, ERROR, PAUSED = 0, 1, 2, 4, 8

    def __init__(self, widget):
        self._w = widget
        self._ptr = None
        self._ok = WIN
        self._last = None

    def _init(self) -> bool:
        if self._ptr or not self._ok:
            return bool(self._ptr)
        try:
            ole32 = ctypes.windll.ole32
            ole32.CoInitialize(None)
            ptr = ctypes.c_void_p()
            clsid = _guid("56FDF344-FD6D-11d0-958A-006097C9A090")   # CLSID_TaskbarList
            iid = _guid("EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF")     # IID_ITaskbarList3
            if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid),
                                      ctypes.byref(ptr)) != 0 or not ptr.value:
                raise OSError("CoCreateInstance failed")
            vt = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            proto = ctypes.WINFUNCTYPE
            ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(vt[3])(ptr)        # HrInit
            self._set_value = proto(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND,
                                    ctypes.c_ulonglong, ctypes.c_ulonglong)(vt[9])
            self._set_state = proto(ctypes.HRESULT, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)(vt[10])
            self._ptr = ptr
        except Exception:
            self._ok = False
        return bool(self._ptr)

    def _hwnd(self):
        return wintypes.HWND(int(self._w.winId()))

    def set(self, frac: float) -> None:
        if not self._init():
            return
        try:
            if self._last != "normal":
                self._set_state(self._ptr, self._hwnd(), self.NORMAL)
                self._last = "normal"
            self._set_value(self._ptr, self._hwnd(), int(max(0.0, min(1.0, frac)) * 1000), 1000)
        except Exception:
            pass

    def _state(self, name: str, flag: int) -> None:
        if self._last == name or not self._init():
            return
        try:
            self._set_state(self._ptr, self._hwnd(), flag)
            if flag == self.ERROR:
                self._set_value(self._ptr, self._hwnd(), 1000, 1000)
            self._last = name
        except Exception:
            pass

    def clear(self) -> None:
        self._state("none", self.NONE)

    def error(self) -> None:
        self._state("error", self.ERROR)


def sleep_now() -> None:
    if WIN:
        ctypes.windll.powrprof.SetSuspendState(False, False, False)


def shutdown_now(delay: int = 30) -> None:
    if WIN:
        subprocess.Popen(["shutdown", "/s", "/t", str(delay), "/c", "Ralenti finished the queue."],
                         creationflags=NO_WINDOW)


# ---------- v0.8.1: safety net for stray windows (a parentless widget shown by mistake) ----------
from PySide6.QtCore import QTimer  # noqa: E402

_tb_filter_v80 = TitleBarStyler.eventFilter


def _tb_filter_v81(self, obj, ev):
    if (ev.type() == QEvent.Show and obj.isWidgetType() and obj.isWindow() and obj.windowType() == Qt.Window
            and obj.parent() is None and not obj.inherits("QMainWindow") and not obj.inherits("QDialog")):
        print(f"[ralenti] hid a stray window: {type(obj).__name__} {obj.objectName()!r}", file=sys.stderr)
        QTimer.singleShot(0, obj.hide)
        return False
    return _tb_filter_v80(self, obj, ev)


TitleBarStyler.eventFilter = _tb_filter_v81
