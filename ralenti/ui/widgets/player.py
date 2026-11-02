"""Video player with built-in comparison: [Left ▾] 🔊 VS 🔊 [Right ▾] plus view chips on the
transport row. The view follows the playhead; previews loop at their end. Takes play in dedicated
players bound to the canvas; audio sidecars crossfade (speaker buttons)."""
import time

from PySide6.QtCore import QEvent, QRectF, QSize, QSizeF, Qt, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut, QTransform
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGraphicsItem, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsView, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from ...core.keyframes import key_after, key_before, keyframes, prev_key
from ..icons import icon, pixmap
from ..util import fmt_ts, mono_font
from ..workers import run_async
from .choice import RichCombo
from .compare import CompareCanvas
from .crop_overlay import CropOverlay
from .timeline import Timeline
from .volume import VolumeControl

SPEEDS = (1.0, 0.5, 0.25)
PLAYING = QMediaPlayer.PlayingState
READY = (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia, QMediaPlayer.BufferingMedia)
VIEWS = [("before", "view-left", "Left side only (1)"), ("split", "view-split", "Split view: drag the divider (2)"),
         ("side", "view-side", "Side by side (3)"), ("after", "view-right", "Right side only (4)")]


def _clock(t: float) -> str:
    m, s = divmod(int(max(0.0, t)), 60)
    return f"{m}:{s:02d}"


class IconSeg(QFrame):
    """Compact icon-only chip selector."""
    changed = Signal(str)

    def __init__(self, items):
        super().__init__()
        self.setObjectName("Seg")
        h = QHBoxLayout(self); h.setContentsMargins(3, 3, 3, 3); h.setSpacing(2)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        self._values = []
        for i, (value, ic, tip) in enumerate(items):
            b = QPushButton(); b.setObjectName("SegBtn"); b.setCheckable(True)
            b.setIcon(icon(ic, "muted", active="text", checked="on_accent")); b.setIconSize(QSize(18, 18))
            b.setFixedSize(34, 26); b.setToolTip(tip); b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b, i); h.addWidget(b)
            self._values.append(value)
        self.group.idClicked.connect(lambda i: self.changed.emit(self._values[i]))

    def set_value(self, v) -> None:
        if v in self._values:
            self.group.button(self._values.index(v)).setChecked(True)


from PySide6.QtCore import QPointF, QTimer  # noqa: E402
from PySide6.QtGui import QPainterPath  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsPathItem  # noqa: E402

from .. import theme as _theme  # noqa: E402


class VideoView(QGraphicsView):
    """The picture. Scroll to zoom around the cursor, drag to move when zoomed (middle button while drawing a crop),
    double-click to fit. The area around the picture is patterned and the picture framed, so black bars in the video
    stay visible. A red tint can mark the parts auto crop removes."""
    clicked = Signal()
    zoom_changed = Signal(float)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._fw, self._fh, self._tf = 1920, 1080, (0, False, False, None)
        self._zoom, self._center, self._press, self._panned = 1.0, None, None, False
        self.holder = QGraphicsRectItem()          # clips the picture to the crop; carries rotate/flip
        self.holder.setPen(QPen(Qt.NoPen))
        self.holder.setFlag(QGraphicsItem.ItemClipsChildrenToShape, True)
        self.scene().addItem(self.holder)
        self.video = QGraphicsVideoItem(self.holder)
        self.video.setAspectRatioMode(Qt.IgnoreAspectRatio)
        self.tint = QGraphicsPathItem(self.holder)
        self.tint.setPen(QPen(Qt.NoPen)); self.tint.setBrush(QColor(248, 113, 113, 85))
        self.tint.setZValue(2); self.tint.hide()
        self.crop = CropOverlay()
        self.scene().addItem(self.crop)
        self.setBackgroundBrush(_theme.backdrop())
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setAcceptDrops(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(320, 180)
        self.badge = QLabel(self.viewport()); self.badge.setObjectName("ZoomBadge"); self.badge.hide()
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._click = QTimer(self); self._click.setSingleShot(True)
        self._click.setInterval(QApplication.doubleClickInterval())
        self._click.timeout.connect(self.clicked.emit)
        self.set_frame(1920, 1080)

    def set_frame(self, w: int, h: int) -> None:
        self._fw, self._fh = w, h
        self._zoom, self._center = 1.0, None
        self.video.setSize(QSizeF(w, h))
        self.crop.set_frame(w, h)
        self._apply()

    def set_transform(self, rotate: int = 0, hflip: bool = False, vflip: bool = False, rect=None) -> None:
        """Shows the picture rotated (clockwise, like FFmpeg's transpose), flipped and cropped to rect
        (w, h, x, y in source pixels). The crop overlay follows the same transform."""
        tf = (int(rotate or 0) % 360, bool(hflip), bool(vflip), tuple(rect) if rect else None)
        if tf != self._tf:
            self._tf = tf
            self._center = None
            self._apply()

    def set_tint(self, rect) -> None:
        """Tints everything outside rect (w, h, x, y in source pixels), or nothing."""
        if not rect:
            self.tint.hide()
            return
        w, h, x, y = rect
        outer = QPainterPath(); outer.addRect(QRectF(0, 0, self._fw, self._fh))
        inner = QPainterPath(); inner.addRect(QRectF(x, y, w, h))
        self.tint.setPath(outer.subtracted(inner))
        self.tint.show()

    def _apply(self) -> None:
        rot, hf, vf, rect = self._tf
        w, h = self._fw, self._fh
        W, H = (h, w) if rot in (90, 270) else (w, h)
        t = (QTransform.fromTranslate(-w / 2, -h / 2) * QTransform().rotate(rot)
             * QTransform.fromScale(-1 if hf else 1, -1 if vf else 1) * QTransform.fromTranslate(W / 2, H / 2))
        r = QRectF(rect[2], rect[3], rect[0], rect[1]) if rect else QRectF(0, 0, w, h)
        self.holder.setRect(r)
        self.holder.setTransform(t)
        self.crop.setTransform(t)
        self.scene().setSceneRect(t.mapRect(r))
        self._fit()

    # ---------- zoom ----------
    def _base(self) -> float:
        r, vp = self.scene().sceneRect(), self.viewport()
        return min((vp.width() - 2) / r.width(), (vp.height() - 2) / r.height())

    def _clamp(self, c) -> QPointF:
        r = self.scene().sceneRect()
        return QPointF(min(max(c.x(), r.left()), r.right()), min(max(c.y(), r.top()), r.bottom()))

    def _fit(self):
        r = self.scene().sceneRect()
        if r.isEmpty() or self.viewport().width() < 4 or self.viewport().height() < 4:
            return
        s = self._base() * self._zoom
        self.resetTransform()
        self.scale(s, s)
        zoomed = self._zoom > 1.0001
        self.centerOn(self._clamp(self._center) if zoomed and self._center is not None else r.center())
        self._badge()

    def _badge(self) -> None:
        z = self._zoom > 1.0001
        self.badge.setVisible(z)
        if z:
            self.badge.setText(f"{self.transform().m11() * 100:.0f}%")
            self.badge.adjustSize()
            vp = self.viewport()
            self.badge.move(vp.width() - self.badge.width() - 10, vp.height() - self.badge.height() - 10)

    def reset_zoom(self) -> None:
        if self._zoom != 1.0:
            self._zoom, self._center = 1.0, None
            self._fit()
            self.zoom_changed.emit(1.0)

    def wheelEvent(self, e):
        r = self.scene().sceneRect()
        if r.isEmpty():
            return
        pos = e.position()
        pt = self.mapToScene(pos.toPoint())
        self._zoom = max(1.0, min(32.0, self._zoom * 1.25 ** (e.angleDelta().y() / 120)))
        if self._zoom <= 1.0001:
            self._zoom, self._center = 1.0, None
        else:
            s = self._base() * self._zoom
            vp = self.viewport()
            self._center = QPointF(pt.x() - (pos.x() - vp.width() / 2) / s, pt.y() - (pos.y() - vp.height() / 2) / s)
        self._fit()
        self.zoom_changed.emit(self._zoom)
        e.accept()

    # ---------- frame around the picture ----------
    def drawForeground(self, painter, rect):
        r = self.scene().sceneRect()
        if r.isEmpty():
            return
        painter.save()
        pen = QPen(QColor(255, 255, 255, 50), 1); pen.setCosmetic(True)
        painter.setPen(pen); painter.setBrush(Qt.NoBrush)
        painter.drawRect(r)
        painter.restore()

    def resizeEvent(self, e):
        super().resizeEvent(e); self._fit()

    def showEvent(self, e):
        super().showEvent(e); self._fit()

    # ---------- mouse: click plays, drag pans when zoomed, double-click fits ----------
    def mousePressEvent(self, e):
        self.setFocus()
        b = e.button()
        if b == Qt.MiddleButton or (b == Qt.LeftButton and not self.crop.isVisible()):
            self._press = (e.position(), self.mapToScene(self.viewport().rect().center()), b)
            self._panned = False
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is not None:
            p0, c0, _b = self._press
            d = e.position() - p0
            if self._zoom > 1.0001 and (self._panned or abs(d.x()) + abs(d.y()) > 4):
                self._panned = True
                s = self.transform().m11()
                self._center = QPointF(c0.x() - d.x() / s, c0.y() - d.y() / s)
                self._fit()
                self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._press is not None:
            b, panned = self._press[2], self._panned
            self._press, self._panned = None, False
            self.viewport().setCursor(Qt.OpenHandCursor if self._zoom > 1.0001 else Qt.ArrowCursor)
            if b == Qt.LeftButton and not panned:
                if self._zoom > 1.0001:
                    self._click.start()        # wait: a double-click fits instead of playing
                else:
                    self.clicked.emit()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.crop.isVisible() and e.button() == Qt.LeftButton:
            super().mouseDoubleClickEvent(e)
            return
        self._click.stop()
        self.reset_zoom()


class CtaOverlay(QFrame):
    primary = Signal()
    secondary = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("Cta")
        h = QHBoxLayout(self); h.setContentsMargins(16, 12, 10, 12); h.setSpacing(12)
        ic = QLabel(); ic.setPixmap(pixmap("sparkle", 22, "accent"))
        texts = QVBoxLayout(); texts.setSpacing(2)
        self.title = QLabel(); self.title.setObjectName("CtaTitle")
        self.sub = QLabel(); self.sub.setObjectName("Hint"); self.sub.setWordWrap(True)
        texts.addWidget(self.title); texts.addWidget(self.sub)
        self.b2 = QPushButton(); self.b2.setProperty("ghost", True); self.b2.setCursor(Qt.PointingHandCursor)
        self.b1 = QPushButton(); self.b1.setProperty("accent", True); self.b1.setCursor(Qt.PointingHandCursor)
        self.b1.setIcon(icon("play-circle", "on_accent", checked=None)); self.b1.setIconSize(QSize(16, 16))
        close = QToolButton(); close.setIcon(icon("x", "muted", active="text", checked=None))
        close.setCursor(Qt.PointingHandCursor); close.clicked.connect(self.hide)
        self.b1.clicked.connect(lambda: (self.hide(), self.primary.emit()))
        self.b2.clicked.connect(lambda: (self.hide(), self.secondary.emit()))
        h.addWidget(ic, 0, Qt.AlignTop); h.addLayout(texts, 1)
        h.addWidget(self.b2); h.addWidget(self.b1); h.addWidget(close, 0, Qt.AlignTop)
        self.hide()

    def set(self, title: str, sub: str, primary: str, secondary: str | None) -> None:
        self.title.setText(title); self.sub.setText(sub)
        self.b1.setText(primary)
        self.b2.setText(secondary or ""); self.b2.setVisible(bool(secondary))
        self.adjustSize()


class PlayerPanel(QWidget):
    range_changed = Signal(object, object)
    maximize_toggled = Signal(bool)
    cta_primary = Signal()
    cta_secondary = Signal()
    open_requested = Signal()
    compare_requested = Signal(object, object)   # left: None | take id, right: "off" | take id
    render_here = Signal()
    shown_changed = Signal()
    mode_requested = Signal(str)
    seek_outside = Signal(float)
    take_selected = Signal(str)
    against_selected = Signal(object)

    def __init__(self, qm, show_range: bool = True, takes: bool = False, maximizable: bool = False):
        super().__init__()
        self.qm = qm
        self.path = self.info = None
        self.fps, self.duration = 30.0, 0.0
        self.keys: list[float] = []
        self.in_s = self.out_s = None
        self.snap_in = False
        self.autoplay = False
        self.resolve = None
        self.on_uncovered = None
        self.describe_take = None    # callable(take id) -> settings text
        self.render_info = None      # callable() -> current settings text
        self.take = self.take_b = None
        self.kind = None             # "orig" | "ab" | "result"
        self.mode = "source"
        self._sel = (None, "off")
        self._layout = "split"
        self._pending_seek = None
        self._pending_rel = None
        self._started = None
        self._want_play = True
        self._slaves: list = []
        self._avail = [False, False]
        self._audio_side = 1
        self._maxed = False
        self._mix = 1.0
        self._rate = 1.0
        self._last_sync = 0.0
        self._last_route = 0.0
        self.empty_text = "Select a file to preview"
        self.setFocusPolicy(Qt.StrongFocus)

        self.view = VideoView(); self.view.clicked.connect(self.toggle_play)
        self.canvas = CompareCanvas(); self.canvas.clicked.connect(self.toggle_play)
        self.ph_icon = QLabel(); self.ph_icon.setAlignment(Qt.AlignCenter)
        self.placeholder = QLabel(); self.placeholder.setObjectName("Hint")
        self.placeholder.setAlignment(Qt.AlignCenter); self.placeholder.setWordWrap(True)
        self.ph = QWidget(); pv = QVBoxLayout(self.ph); pv.setAlignment(Qt.AlignCenter); pv.setSpacing(10)
        self.ph_btn = QPushButton("Open a video"); self.ph_btn.setProperty("accent", True)
        self.ph_btn.setIcon(icon("folder-open", "on_accent", checked=None)); self.ph_btn.setIconSize(QSize(16, 16))
        self.ph_btn.setCursor(Qt.PointingHandCursor); self.ph_btn.clicked.connect(self.open_requested.emit)
        pv.addWidget(self.ph_icon); pv.addWidget(self.placeholder); pv.addSpacing(4)
        pv.addWidget(self.ph_btn, 0, Qt.AlignHCenter)
        self.ph_btn.hide()
        self.stack = QStackedWidget()
        for w in (self.ph, self.view, self.canvas):
            self.stack.addWidget(w)
        self.cta = CtaOverlay(self.stack)
        self.cta.primary.connect(self.cta_primary.emit)
        self.cta.secondary.connect(self.cta_secondary.emit)
        self.stack.installEventFilter(self)
        self._set_placeholder("film-strip", self.empty_text)
        frame = QFrame(); frame.setObjectName("Card")
        fl = QVBoxLayout(frame); fl.setContentsMargins(1, 1, 1, 1); fl.addWidget(self.stack)
        self.timeline = Timeline(); self.timeline.seek.connect(self._timeline_seek)
        self.cmp_label = QLabel()

        def tb(name, tip, fn, size=18):
            b = QToolButton(); b.setIcon(icon(name, "muted", active="text", checked=None))
            b.setIconSize(QSize(size, size)); b.setToolTip(tip); b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor); b.clicked.connect(lambda: fn())
            return b

        self.play_btn = tb("play-fill", "Play / pause (Space)", self.toggle_play, 22)
        self.play_btn.setIcon(icon("play-fill", "text", checked=None))
        self.mute_btn = tb("speaker-high", "Mute (M)", self.toggle_mute)
        self.time = QLabel(); self.time.setObjectName("Muted"); self.time.setFont(mono_font(9))
        self.key_btns = [tb("skip-back", "Previous keyframe (Shift+←)", lambda: self.jump_key(-1)),
                         tb("skip-forward", "Next keyframe (Shift+→)", lambda: self.jump_key(1))]
        row = QHBoxLayout(); row.setSpacing(4)
        for b in (self.key_btns[0], tb("caret-left", "Previous frame (←)", lambda: self.step(-1)),
                  self.play_btn, tb("caret-right", "Next frame (→)", lambda: self.step(1)), self.key_btns[1]):
            row.addWidget(b)
        from .timeline import zoom_controls
        row.addSpacing(10); row.addWidget(self.time); row.addStretch(1)
        self.range_btns = []
        if show_range:
            self.range_btns = [tb("mark-in", "Set in point (I)", self.set_in),
                               tb("mark-out", "Set out point (O)", self.set_out),
                               tb("eraser", "Clear in and out points", self.clear_range)]
            for b in self.range_btns:
                row.addWidget(b)

        self.cmp = QFrame(); self.cmp.setObjectName("CompareBox")
        ch = QHBoxLayout(self.cmp); ch.setContentsMargins(4, 3, 4, 3); ch.setSpacing(2)
        self.left_box = RichCombo(420); self.left_box.setMinimumContentsLength(9)
        self.left_box.setToolTip("Left side: the original video, or a preview to compare with another preview")
        self.right_box = RichCombo(440); self.right_box.setMinimumContentsLength(13)
        self.right_box.setToolTip("Right side: a preview to compare with, or none")
        self.l_audio, self.r_audio = self._spk(0), self._spk(1)
        vs = QLabel("VS"); vs.setObjectName("VsLabel"); vs.setAlignment(Qt.AlignCenter)
        for w in (self.left_box, self.l_audio, vs, self.r_audio, self.right_box):
            ch.addWidget(w)
        self.left_box.activated.connect(self._combo)
        self.right_box.activated.connect(self._combo)
        self.layout_seg = IconSeg(VIEWS); self.layout_seg.changed.connect(self._set_layout)
        self.speed_btn = QToolButton(); self.speed_btn.setText("1×"); self.speed_btn.setObjectName("SpeedBtn")
        self.speed_btn.setToolTip("Playback speed of the comparison: click to cycle 1× · ½× · ¼×")
        self.speed_btn.setCursor(Qt.PointingHandCursor); self.speed_btn.setFocusPolicy(Qt.NoFocus)
        self.speed_btn.clicked.connect(self._cycle_speed)
        self.max_btn = tb("arrows-out", "Maximize the player (F)", self.toggle_max)
        self.volume = VolumeControl(self.mute_btn)
        for w in (self.cmp, self.layout_seg, self.speed_btn, self.max_btn, self.volume):
            row.addWidget(w)

        self.extra = QVBoxLayout(); self.extra.setSpacing(8)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(6)
        lay.addWidget(frame, 1); lay.addWidget(self.timeline); lay.addLayout(row); lay.addLayout(self.extra)
        if not takes:          # hide only once parented (a parentless widget shown = a stray window)
            self.cmp.hide(); self.layout_seg.hide()
        if not maximizable:
            self.max_btn.hide()

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.volume.bind(self.audio)
        self.player.setVideoOutput(self.view.video)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._icon_state)
        self.player.mediaStatusChanged.connect(self._on_status)
        self.player.errorOccurred.connect(self._on_error)
        self.sink_a, self.sink_b = QVideoSink(self), QVideoSink(self)
        self.sink_a.videoFrameChanged.connect(self.canvas.set_frame)
        self.sink_b.videoFrameChanged.connect(self.canvas.set_frame_b)
        self.tp = QMediaPlayer(self); self.tp.setVideoOutput(self.sink_a)
        self.tp.positionChanged.connect(self._on_take_pos)
        self.tp.playbackStateChanged.connect(self._take_state)
        self.tp.mediaStatusChanged.connect(self._take_status)
        self.tp.errorOccurred.connect(self._on_error)
        self.tpb = QMediaPlayer(self); self.tpb.setVideoOutput(self.sink_b)
        self.tpb.mediaStatusChanged.connect(lambda *_: self._try_start())
        self.ap = []
        for _ in range(2):
            pl = QMediaPlayer(self); out = QAudioOutput(self)
            pl.setAudioOutput(out)
            pl.mediaStatusChanged.connect(lambda *_: self._try_start())
            self.ap.append(pl)
        self.audio.volumeChanged.connect(self._apply_mix)
        self.audio.mutedChanged.connect(self._apply_mix)
        self.fade = QVariantAnimation(self); self.fade.setDuration(140)
        self.fade.valueChanged.connect(self._set_mix)

        keys = [("Space", self.toggle_play), ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
                ("Shift+Left", lambda: self.jump_key(-1)), ("Shift+Right", lambda: self.jump_key(1)),
                ("K", self.pause), ("L", self.faster), ("J", lambda: self.seek(self.position() - 5)),
                ("M", self.toggle_mute), ("Home", lambda: self.seek(0)), ("Escape", self._escape),
                ("A", self.toggle_audio)] + [(str(i + 1), lambda v=v[0]: self._set_layout(v))
                                             for i, v in enumerate(VIEWS)]
        if maximizable:
            keys.append(("F", self.toggle_max))
        if show_range:
            keys += [("I", self.set_in), ("O", self.set_out)]
        for k, fn in keys:
            sc = QShortcut(QKeySequence(k), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self.set_compare_choices([], None, "off")
        self._set_layout("split")
        self._take_ui()
        self._update_time(0)

    def _spk(self, i: int) -> QToolButton:
        b = QToolButton(); b.setCheckable(True); b.setFocusPolicy(Qt.NoFocus); b.setCursor(Qt.PointingHandCursor)
        b.setIcon(icon("speaker-high", "faint", active="text", checked="accent")); b.setIconSize(QSize(17, 17))
        b.setFixedSize(30, 26)
        b.clicked.connect(lambda: self._set_audio_side(i))
        return b

    def add_row(self, widget: QWidget) -> None:
        self.extra.addWidget(widget)

    def _set_placeholder(self, icon_name: str, text: str) -> None:
        self.ph_icon.setPixmap(pixmap(icon_name, 40, "faint"))
        self.placeholder.setText(text)

    def active(self) -> QMediaPlayer:
        return self.tp if self.take else self.player

    # ---------- source ----------
    def load(self, path: str, info) -> None:
        self._leave_take()
        self._pending_seek = None
        self.path, self.info = path, info
        self.fps = info.fps_float or 30.0
        self.duration = info.duration
        self.keys, self.in_s, self.out_s = [], None, None
        self.view.set_frame(info.width or 1920, info.height or 1080)
        self.timeline.set_duration(info.duration)
        self.timeline.set_keyframes([]); self.timeline.set_range(None, None)
        self.timeline.set_takes([]); self.timeline.set_position(0)
        self.player.setPlaybackRate(1.0)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.stack.setCurrentWidget(self.view)
        ffprobe = self.qm.tc.ffprobe
        run_async(lambda: keyframes(ffprobe, path), lambda ks, p=path: self._set_keys(p, ks), lambda _m: None)

    def _set_keys(self, path, ks):
        if path == self.path:
            self.keys = ks
            if not self.take:
                self.timeline.set_keyframes(ks)

    def unload(self) -> None:
        self._leave_take()
        self.player.stop()
        self.player.setSource(QUrl())
        self.path = self.info = None
        self.duration = 0.0
        self.view.crop.setVisible(False)
        self._set_placeholder("film-strip", self.empty_text)
        self.stack.setCurrentWidget(self.ph)
        self.timeline.set_duration(0); self.timeline.set_takes([])
        self.set_compare_choices([], None, "off")
        self._update_time(0)

    def set_takes(self, spans) -> None:
        self.timeline.set_takes(spans)

    # ---------- comparison choices ----------
    def set_compare_choices(self, items, left=None, right="off") -> None:
        lb, rb = self.left_box, self.right_box
        desc = self.describe_take or (lambda _j: "")
        for b in (lb, rb):
            b.blockSignals(True); b.clear()
        lb.add("Original", None, "The source video, untouched")
        for it in items:
            lb.add(it[1], it[0], it[2] if len(it) > 2 else desc(it[0]))
        rb.add("No comparison", "off", "Just play the original video")
        others = [it for it in items if it[0] != left]
        if others:
            rb.add_header("Previews")
            for it in others:
                rb.add(it[1], it[0], it[2] if len(it) > 2 else desc(it[0]))
        rb.add_header("New")
        info = (self.render_info() if self.render_info else "") or "Uses your current settings"
        rb.add("Render a preview here", "render", info, "Current settings")
        if not lb.select(left):
            lb.setCurrentIndex(0); left = None
        if right in (None, "auto", "render") or not rb.select(right):
            rb.select("off"); right = "off"
        lb.setEnabled(bool(items))
        for b in (lb, rb):
            b.blockSignals(False)
        self._sel = (left, right)

    def set_take_choices(self, items, current=None, against=None) -> None:
        self.set_compare_choices(items, None, current or "off")

    def _combo(self, *_):
        left, right = self.left_box.currentData(), self.right_box.currentData()
        if right == "render":
            self.right_box.select(self._sel[1])
            self.render_here.emit()
            return
        if right is None:
            self.right_box.select(self._sel[1]); return
        self._sel = (left, right)
        self.compare_requested.emit(left, right)

    # ---------- routing ----------
    def route_to(self, t: float, play=None) -> None:
        if not self.path:
            return
        playing = self.active().playbackState() == PLAYING if play is None else play
        r = self.resolve(t) if self.resolve else None
        if r is None:
            self.show_source(at=t, play=playing)
            if not playing and self.on_uncovered:
                self.on_uncovered(t)
            return
        a, b, kind = r
        self.show_take(a, kind, at=t, against=b, play=playing)

    # ---------- takes ----------
    def _src_time(self, t: float) -> float:
        return self.take["start"] + t / self.take["slowmo"]

    def source_position(self) -> float:
        return self._src_time(self.tp.position() / 1000) if self.take else self.player.position() / 1000

    def _map(self, ms: int, tk: dict) -> int:
        if tk is self.take:
            return ms
        rel = (self._src_time(ms / 1000) - tk["start"]) * tk["slowmo"]
        dur = (tk["end"] - tk["start"]) * tk["slowmo"]
        return int(max(0.0, min(rel, dur)) * 1000)

    def show_take(self, take: dict, mode: str = "orig", at=None, against=None, play=None) -> None:
        if not self.path:
            return
        self.hide_cta()
        if mode == "compare":
            mode = "ab" if against else "orig"
        b = against if against and against["id"] != take["id"] else None
        if mode == "ab" and b is None:
            mode = "orig"
        if play is None:
            play = self.active().playbackState() == PLAYING
        if self.take is None:
            self.view.crop.setVisible(False)
            self.player.pause()
        new_a = self.take is None or self.take["id"] != take["id"]
        new_b = (b or {}).get("id") != (self.take_b or {}).get("id")
        new_kind = mode != self.kind
        self.take, self.take_b, self.kind, self.mode = take, b, mode, "compare"
        self.fps = take["info"].fps_float or 30.0
        rel = 0.0 if at is None else max(0.0, (at - take["start"]) * take["slowmo"])
        self._want_play = play
        if new_a:
            self.tp.stop(); self.canvas.reset()
            self._started, self._pending_rel = None, rel
            self.tp.setPlaybackRate(self._rate)
            self.tp.setSource(QUrl.fromLocalFile(take["path"]))
        else:
            self._seek_take(rel)
            if play and self.tp.playbackState() != PLAYING:
                self.tp.play()
            elif not play and self.tp.playbackState() == PLAYING:
                self.tp.pause()
        if new_a or new_b or new_kind:
            self._setup_slaves()
        self._labels()
        self.stack.setCurrentWidget(self.canvas)
        self._take_ui()
        self.timeline.set_keyframes([]); self.timeline.set_range(take["start"], take["end"])
        if new_a or new_b:
            self.shown_changed.emit()

    def _setup_slaves(self) -> None:
        A, B = self.take, self.take_b
        for pl in [*self.ap, self.tpb]:
            pl.stop()
        aa = A.get("audio") or (None, None)
        if self.kind == "ab":
            ba = B.get("audio") or (None, None)
            paths = [(aa[1], A), (ba[1], B)]
            tips = ("Listen to the left preview", "Listen to the right preview")
            self.tpb.setSource(QUrl.fromLocalFile(B["path"])); self.tpb.setPlaybackRate(self._rate)
        else:
            self.tpb.setSource(QUrl())
            paths = [(None if self.kind == "result" else aa[0], A), (aa[1], A)]
            label = (A.get("audio_labels") or ("Original", "Processed"))[1]
            tips = ("Listen to the original audio, stretched to match the video",
                    f"Listen to the processed audio ({label})")
        self._slaves = []
        for pl, (path, tk) in zip(self.ap, paths):
            pl.setSource(QUrl.fromLocalFile(path) if path else QUrl())
            pl.setPlaybackRate(self._rate)
            if path:
                self._slaves.append((pl, tk))
        if self.kind == "ab":
            self._slaves.append((self.tpb, B))
        self._avail = [bool(paths[0][0]), bool(paths[1][0])]
        note = A.get("audio_note") or "No audio for this side"
        for i, btn in enumerate((self.l_audio, self.r_audio)):
            btn.setToolTip(tips[i] + " (A)" if self._avail[i] else note)
        side = self._audio_side if self._avail[self._audio_side] else (
            1 if self._avail[1] else (0 if self._avail[0] else None))
        self._audio_now(side, fade=False)
        if self._started == A["id"]:
            self._started = None
            self._try_start()

    def _try_start(self) -> None:
        if not self.take or self.tp.mediaStatus() not in READY:
            return
        if any(pl.mediaStatus() not in READY for pl, _ in self._slaves):
            return
        if self._started == self.take["id"]:
            return
        self._started = self.take["id"]
        rel = self._pending_rel if self._pending_rel is not None else self.tp.position() / 1000
        self._pending_rel = None
        ms = int(rel * 1000)
        self.tp.setPosition(ms)
        for pl, tk in self._slaves:
            pl.setPosition(self._map(ms, tk))
        if self._want_play:
            if self.tp.playbackState() == PLAYING:
                self._take_state(PLAYING)
            else:
                self.tp.play()
        else:
            self.tp.pause()

    def _take_status(self, status) -> None:
        self._try_start()
        if status == QMediaPlayer.EndOfMedia and self.take:   # loop the preview
            self.tp.setPosition(0)
            self.tp.play()

    def _labels(self) -> None:
        A, B, c = self.take, self.take_b, self.canvas

        def short(t):
            return f"{t['after'].replace('AI · ', '')} · {_clock(t['start'])}"

        if self.kind == "ab":
            c.against = "take"
            c.before, c.after = short(A), short(B)
            c.details_left, c.details = A["details"], B["details"]
            mode = self._layout
        elif self.kind == "result":
            c.against, c.after, c.details = "original", A["after"], A["details"]
            mode = "after"
        else:
            c.against = "original"
            c.before, c.after = A["before"], A["after"]
            c.details_left = "The source video, its frames repeated to match the slow motion"
            c.details = A["details"]
            mode = self._layout
        c.set_mode(mode)

    def show_source(self, at=None, play=None) -> None:
        self.hide_cta()
        playing = self.active().playbackState() == PLAYING if play is None else play
        if self.take is None:
            if at is not None:
                self.seek(at)
            if playing and self.player.playbackState() != PLAYING:
                self.player.play()
            return
        pos = self.source_position() if at is None else at
        self._leave_take()
        self.fps = self.info.fps_float or 30.0
        self.timeline.set_keyframes(self.keys); self.timeline.set_range(self.in_s, self.out_s)
        self.stack.setCurrentWidget(self.view)
        self.player.setPosition(int(min(pos, self.duration) * 1000))
        if playing:
            self.player.play()
        else:
            self.player.pause()
        self._icon_state()
        self.shown_changed.emit()

    def _leave_take(self) -> None:
        if self.take is None:
            return
        self.take = self.take_b = self.kind = None
        self.mode, self._started, self._pending_rel = "source", None, None
        for pl in [self.tp, self.tpb, *self.ap]:
            pl.stop(); pl.setSource(QUrl())
        self._slaves = []
        self._avail = [False, False]
        self.canvas.reset(); self.canvas.set_audio(None)
        self._take_ui()

    def _take_ui(self) -> None:
        on = self.take is not None
        for b in self.range_btns + self.key_btns:
            b.setVisible(not on)
        self.layout_seg.setEnabled(on and self.kind != "result")
        self.speed_btn.setVisible(on)
        self.l_audio.setEnabled(on and self.kind != "result" and self._avail[0])
        self.r_audio.setEnabled(on and self._avail[1])
        if not on:
            self.l_audio.setChecked(False); self.r_audio.setChecked(False)

    def _set_layout(self, m: str) -> None:
        self._layout = m
        self.layout_seg.set_value(m)
        if self.take and self.kind != "result":
            self._labels()

    def _cycle_speed(self) -> None:
        i = (SPEEDS.index(self._rate) + 1) % len(SPEEDS) if self._rate in SPEEDS else 0
        self._rate = SPEEDS[i]
        self.speed_btn.setText({1.0: "1×", 0.5: "½×", 0.25: "¼×"}[self._rate])
        for pl in [self.tp, self.tpb, *self.ap]:
            pl.setPlaybackRate(self._rate)

    def _timeline_seek(self, t: float) -> None:
        self.hide_cta()
        if self.resolve:
            self.route_to(t)
        else:
            self.seek(t)

    def _seek_take(self, rel: float) -> None:
        if self._started != (self.take or {}).get("id"):
            self._pending_rel = rel
            return
        ms = int(rel * 1000)
        self.tp.setPosition(ms)
        for pl, tk in self._slaves:
            pl.setPosition(self._map(ms, tk))

    def _take_state(self, state) -> None:
        self._icon_state()
        playing = state == PLAYING
        pos = self.tp.position()
        for pl, tk in self._slaves:
            if playing:
                pl.setPosition(self._map(pos, tk)); pl.play()
            else:
                pl.pause()

    def _on_take_pos(self, ms: int) -> None:
        if not self.take:
            return
        t = ms / 1000
        self.timeline.set_position(self._src_time(t))
        self._update_time(t)
        now = time.monotonic()
        if now - self._last_sync < 1.5 or self.tp.playbackState() != PLAYING:
            return
        self._last_sync = now
        for pl, tk in self._slaves:
            m = self._map(ms, tk)
            if pl.playbackState() != PLAYING and pl.mediaStatus() != QMediaPlayer.EndOfMedia:
                pl.setPosition(m); pl.play()
            elif abs(pl.position() - m) > 250:
                pl.setPosition(m)

    # ---------- audio side ----------
    def toggle_audio(self) -> None:
        if self.take:
            other = 1 - (self._audio_side or 0)
            if self._avail[other]:
                self._set_audio_side(other)

    def _set_audio_side(self, i: int) -> None:
        if not self.take or not self._avail[i]:
            self._audio_now(self._audio_side if self.take and self._avail[self._audio_side] else None, False)
            return
        self._audio_now(i, fade=True)

    def _audio_now(self, side, fade: bool) -> None:
        if side is None:
            self.l_audio.setChecked(False); self.r_audio.setChecked(False)
            self.canvas.set_audio(None)
            return
        self._audio_side = side
        if fade:
            self.fade.stop(); self.fade.setStartValue(self._mix); self.fade.setEndValue(float(side)); self.fade.start()
        else:
            self._mix = float(side); self._apply_mix()
        self.l_audio.setChecked(side == 0); self.r_audio.setChecked(side == 1)
        self.canvas.set_audio(side)

    def _set_mix(self, v) -> None:
        self._mix = float(v)
        self._apply_mix()

    def _apply_mix(self, *_) -> None:
        master = 0.0 if self.audio.isMuted() else self.audio.volume()
        self.ap[0].audioOutput().setVolume(master * (1 - self._mix))
        self.ap[1].audioOutput().setVolume(master * self._mix)

    # ---------- call to action ----------
    def show_cta(self, title: str, sub: str, primary: str, secondary: str | None = None) -> None:
        self.cta.set(title, sub, primary, secondary)
        self.cta.show(); self.cta.raise_()
        self._place_cta()

    def hide_cta(self) -> None:
        self.cta.hide()

    def _place_cta(self) -> None:
        r = self.stack.rect()
        hint = self.cta.sizeHint()
        w = min(max(hint.width(), 460), r.width() - 24)
        self.cta.setGeometry((r.width() - w) // 2, r.height() - hint.height() - 20, w, hint.height())

    def eventFilter(self, obj, ev):
        if obj is self.stack and ev.type() == QEvent.Resize and self.cta.isVisible():
            self._place_cta()
        return False

    # ---------- transport ----------
    def position(self) -> float:
        return self.active().position() / 1000.0

    def seek(self, t: float) -> None:
        if self.take:
            self._seek_take(max(0.0, t)); return
        if self.path:
            self.player.setPosition(int(round(max(0.0, min(t, self.duration)) * 1000)))

    def pause(self) -> None:
        self.active().pause()
        if not self.take:
            self.player.setPlaybackRate(1.0)

    def toggle_play(self) -> None:
        if not self.path:
            return
        self.hide_cta()
        pl = self.active()
        if pl.playbackState() == PLAYING:
            pl.pause()
        else:
            if not self.take and self.position() >= self.duration - 0.05:
                self.route_to(0.0, play=True)
                return
            pl.play()

    def faster(self) -> None:
        if self.take:
            return
        if self.player.playbackState() != PLAYING:
            self.player.setPlaybackRate(1.0); self.player.play()
        else:
            self.player.setPlaybackRate(min(4.0, self.player.playbackRate() * 2))

    def step(self, n: int) -> None:
        if not self.path:
            return
        self.active().pause()
        fd = 1.0 / self.fps
        cur = round(self.position() / fd)
        self.seek((cur + n + 0.25) * fd)

    def jump_key(self, d: int) -> None:
        if self.take or not self.keys:
            return
        t = self.position()
        k = key_before(self.keys, t) if d < 0 else key_after(self.keys, t)
        if k is not None:
            self.player.pause()
            self.seek(k + 0.002)

    def toggle_mute(self) -> None:
        self.audio.setMuted(not self.audio.isMuted())

    def toggle_max(self) -> None:
        self._maxed = not self._maxed
        self.max_btn.setIcon(icon("arrows-in" if self._maxed else "arrows-out", "muted", active="text", checked=None))
        self.max_btn.setToolTip("Restore the layout (F / Esc)" if self._maxed else "Maximize the player (F)")
        self.maximize_toggled.emit(self._maxed)

    def _escape(self) -> None:
        if self.cta.isVisible():
            self.hide_cta()
        elif self._sel != (None, "off") and self.cmp.isVisible():
            self.right_box.select("off"); self.left_box.setCurrentIndex(0)
            self._combo()
        elif self._maxed:
            self.toggle_max()

    # ---------- range ----------
    def set_in(self) -> None:
        if not self.path or self.take:
            return
        t = self.position()
        if self.snap_in and self.keys:
            t = prev_key(self.keys, t)
        self.in_s = t if t > 0.001 else None
        if self.out_s is not None and self.out_s <= t:
            self.out_s = None
        self._range_updated(True)

    def set_out(self) -> None:
        if not self.path or self.take:
            return
        t = min(self.duration, self.position() + 1.0 / self.fps)
        if self.in_s is not None and t <= self.in_s:
            return
        self.out_s = t if t < self.duration - 0.001 else None
        self._range_updated(True)

    def clear_range(self) -> None:
        if self.take:
            return
        self.in_s = self.out_s = None
        self._range_updated(True)

    def set_range(self, in_s, out_s, emit: bool = False) -> None:
        self.in_s, self.out_s = in_s, out_s
        self._range_updated(emit)

    def _range_updated(self, emit: bool) -> None:
        if not self.take:
            self.timeline.set_range(self.in_s, self.out_s)
        if emit:
            self.range_changed.emit(self.in_s, self.out_s)

    # ---------- player events ----------
    def _icon_state(self, *_):
        playing = self.active().playbackState() == PLAYING
        self.play_btn.setIcon(icon("pause-fill" if playing else "play-fill", "text", checked=None))

    def _on_pos(self, ms):
        if self.take:
            return
        t = ms / 1000.0
        self.timeline.set_position(t)
        self._update_time(t)
        if self.resolve and self.player.playbackState() == PLAYING:
            now = time.monotonic()
            if now - self._last_route > 0.08:
                self._last_route = now
                if self.resolve(t):
                    self.route_to(t, play=True)

    def _on_duration(self, ms):
        if ms > 0 and not self.duration:
            self.duration = ms / 1000.0
            self.timeline.set_duration(self.duration)

    def _on_status(self, status):
        if status == QMediaPlayer.LoadedMedia:
            if self._pending_seek is not None:
                self.player.setPosition(int(self._pending_seek * 1000))
                self._pending_seek = None
            if not self.take:
                self.player.play() if self.autoplay else self.player.pause()
        elif status == QMediaPlayer.InvalidMedia:
            self._show_error("This file can't be previewed. Processing still works.")

    def _on_error(self, err, msg):
        if err != QMediaPlayer.NoError:
            self._show_error(f"Playback problem: {msg}\nProcessing still works.")

    def _show_error(self, text):
        self._set_placeholder("warning-circle", text)
        self.stack.setCurrentWidget(self.ph)

    def _update_time(self, t):
        if self.take:
            self.time.setText(f"{fmt_ts(self._src_time(t))} / {fmt_ts(self.duration)}   take {fmt_ts(t)[3:]}")
        else:
            self.time.setText(f"{fmt_ts(t)} / {fmt_ts(self.duration)}   f{int(t * self.fps + 1e-6)}")

    def mousePressEvent(self, e):
        self.setFocus()
        super().mousePressEvent(e)
