"""Screen grabbing and the region-selection overlay."""
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen,
                         QPixmap)
from PyQt6.QtWidgets import QWidget

from . import x11


class Grab:
    """A frozen copy of the whole virtual desktop."""

    def __init__(self, image=None):
        """image=None: read the screen directly (X11). Otherwise: a full-desktop
        screenshot delivered by the desktop (Wayland portal)."""
        screens = QGuiApplication.screens()
        self.virtual = QRect()
        for s in screens:
            self.virtual = self.virtual.united(s.geometry())
        self.screen_rects = [s.geometry() for s in screens]
        if image is not None:
            self._from_image(image)
            return
        self.scale = max([s.devicePixelRatio() for s in screens] or [1.0])
        sz = self.virtual.size() * self.scale
        self.image = QImage(sz, QImage.Format.Format_RGB32)
        self.image.fill(QColor(0, 0, 0))
        p = QPainter(self.image)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for s in screens:
            g = s.geometry()
            pm = s.grabWindow(0)
            target = QRectF((g.x() - self.virtual.x()) * self.scale,
                            (g.y() - self.virtual.y()) * self.scale,
                            g.width() * self.scale, g.height() * self.scale)
            p.drawPixmap(target, pm, QRectF(pm.rect()))
        p.end()

    def _from_image(self, image):
        vw, vh = max(1, self.virtual.width()), max(1, self.virtual.height())
        self.scale = image.width() / vw if image.width() else 1.0
        expected_h = round(vh * self.scale)
        img = image.convertToFormat(QImage.Format.Format_RGB32)
        if abs(img.height() - expected_h) > 2:
            # layout doesn't match exactly (unusual monitor setups): stretch to fit
            img = img.scaled(round(vw * self.scale), expected_h,
                             Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        self.image = img

    def crop(self, rect):
        """rect: logical global coordinates."""
        r = rect.intersected(self.virtual)
        if r.isEmpty():
            return QImage()
        src = QRect(round((r.x() - self.virtual.x()) * self.scale),
                    round((r.y() - self.virtual.y()) * self.scale),
                    round(r.width() * self.scale), round(r.height() * self.scale))
        return self.image.copy(src)

    def screen_at(self, pt):
        for r in self.screen_rects:
            if r.contains(pt):
                return r
        return self.screen_rects[0] if self.screen_rects else self.virtual

    def to_logical(self, xr):
        """X11 device-pixel rect tuple -> logical QRect."""
        if xr is None:
            return None
        x, y, w, h = xr
        s = self.scale
        return QRect(round(x / s), round(y / s), round(w / s), round(h / s))


class _Selection:
    """State shared by all overlay windows of one capture (one per monitor on Wayland)."""

    def __init__(self, start_pos):
        self.press = None
        self.cur = start_pos
        self.dragging = False
        self.finished = False
        self.panels = []

    def update_all(self):
        for p in self.panels:
            p.update()


class Overlay(QWidget):
    """Fullscreen frozen screenshot: drag = region, click = window/monitor, Enter = monitor.

    On X11 a single window spans all monitors. On Wayland, where windows can't be
    placed freely, there is one fullscreen window per monitor sharing one selection.
    """
    done = pyqtSignal(object)  # QRect (logical, global) or None

    DIM = QColor(0, 0, 0, 115)
    ACCENT = QColor(90, 160, 44)

    def __init__(self, grab, window_rects, area=None, sel=None, wayland=False):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if not wayland:
            flags |= Qt.WindowType.X11BypassWindowManagerHint | Qt.WindowType.Tool
        super().__init__(None, flags)
        self.shot = grab
        self.wayland = wayland
        self.area = QRect(area) if area is not None else QRect(grab.virtual)
        self.pm = QPixmap.fromImage(grab.image)
        self.pm.setDevicePixelRatio(grab.scale)
        self.windows = [r for r in window_rects if r is not None]
        self.setGeometry(self.area)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if sel is None:
            start = QCursor.pos()
            if not self.area.contains(start):
                start = self.area.center()
            sel = _Selection(start)
        self.g = sel
        self.g.panels.append(self)

    # shared state
    press = property(lambda self: self.g.press, lambda self, v: setattr(self.g, "press", v))
    cur = property(lambda self: self.g.cur, lambda self, v: setattr(self.g, "cur", v))
    dragging = property(lambda self: self.g.dragging,
                        lambda self, v: setattr(self.g, "dragging", v))

    # geometry helpers (global logical coordinates)
    def _local(self, r):
        return r.translated(-self.area.topLeft())

    def _src(self, global_rect):
        """Source rect in the screenshot image for a global logical rect."""
        s = self.shot.scale
        r = QRectF(global_rect).translated(-QPointF(self.shot.virtual.topLeft()))
        return QRectF(r.x() * s, r.y() * s, r.width() * s, r.height() * s)

    def _global(self, e):
        return self.area.topLeft() + e.position().toPoint()

    def hovered(self):
        for r in self.windows:
            if r.contains(self.cur):
                return r.intersected(self.shot.virtual)
        return self.shot.screen_at(self.cur)

    def selection(self):
        if self.press is not None and self.dragging:
            x1, x2 = sorted((self.press.x(), self.cur.x()))
            y1, y2 = sorted((self.press.y(), self.cur.y()))
            return QRect(x1, y1, x2 - x1, y2 - y1)
        return None

    def start(self):
        if self.wayland:
            for scr in QGuiApplication.screens():
                if scr.geometry() == self.area:
                    self.setScreen(scr)
            self.showFullScreen()
            self.raise_()
            self.activateWindow()
            self.setFocus()
            return
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabKeyboard()
        self.setFocus()

    def finish(self, rect):
        if self.g.finished:
            return
        self.g.finished = True
        panels = list(self.g.panels)
        for p in panels:
            if not p.wayland:
                p.releaseKeyboard()
            p.hide()
        panels[0].done.emit(rect)
        for p in panels:
            p.close()

    # events
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.finish(None)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.press = self._global(e)
            self.cur = self.press
            self.dragging = False
            self.g.update_all()

    def mouseMoveEvent(self, e):
        self.cur = self._global(e)
        if self.press is not None and not self.dragging:
            if (self.cur - self.press).manhattanLength() > 3:
                self.dragging = True
        self.g.update_all()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.press is None:
            return
        self.cur = self._global(e)
        sel = self.selection()
        if sel is not None and sel.width() > 2 and sel.height() > 2:
            self.finish(sel)
        else:
            self.finish(self.hovered())

    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.finish(None)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            sel = self.selection()
            self.finish(sel if sel else self.shot.screen_at(self.cur))
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            d = {Qt.Key.Key_Left: QPoint(-step, 0), Qt.Key.Key_Right: QPoint(step, 0),
                 Qt.Key.Key_Up: QPoint(0, -step), Qt.Key.Key_Down: QPoint(0, step)}[k]
            if self.wayland:
                self.cur = self.cur + d  # Wayland doesn't let apps move the mouse pointer
            else:
                QCursor.setPos(QCursor.pos() + d)
                self.cur = QCursor.pos()
            self.g.update_all()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(QRectF(0, 0, self.area.width(), self.area.height()), self.pm,
                     self._src(self.area))
        sel = self.selection()
        target = sel if sel is not None else (self.hovered() if self.press is None else None)
        p.fillRect(self.rect(), self.DIM)
        if target is not None:
            lt = self._local(target)
            p.drawPixmap(QRectF(lt), self.pm, self._src(target))
            pen = QPen(self.ACCENT, 2 if sel is not None else 3)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(lt.adjusted(0, 0, -1, -1))
            self._label(p, lt, f"{target.width()} × {target.height()}")
        self._crosshair(p)
        self._magnifier(p)
        self._help(p)
        p.end()

    def _label(self, p, lt, text):
        f = QFont("Sans")
        f.setPixelSize(13)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 14, fm.height() + 8
        x = lt.x()
        y = lt.y() - h - 4
        if y < 0:
            y = lt.y() + 4
            x += 4
        r = QRectF(x, y, w, h)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 20, 210))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(QColor(255, 255, 255))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)

    def _crosshair(self, p):
        c = self._local(QRect(self.cur, self.cur)).topLeft()
        p.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DashLine))
        p.drawLine(0, c.y(), self.width(), c.y())
        p.drawLine(c.x(), 0, c.x(), self.height())

    def _magnifier(self, p):
        n, z = 15, 8  # source pixels, zoom
        size = n * z
        if not self.area.contains(self.cur):
            return  # the magnifier lives on the monitor under the cursor
        src = self._src(QRect(self.cur.x() - n // 2, self.cur.y() - n // 2, n, n))
        c = self.cur - self.area.topLeft()
        x, y = c.x() + 24, c.y() + 24
        if x + size > self.width():
            x = c.x() - 24 - size
        if y + size + 22 > self.height():
            y = c.y() - 24 - size - 22
        box = QRectF(x, y, size, size)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.fillRect(box, QColor(0, 0, 0))
        p.drawPixmap(box, self.pm, src)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        for i in range(1, n):
            p.drawLine(QPointF(x + i * z, y), QPointF(x + i * z, y + size))
            p.drawLine(QPointF(x, y + i * z), QPointF(x + size, y + i * z))
        p.setPen(QPen(QColor(255, 60, 60), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(x + (n // 2) * z, y + (n // 2) * z, z, z))
        p.setPen(QPen(self.ACCENT, 2))
        p.drawRect(box)
        f = QFont("Sans")
        f.setPixelSize(11)
        p.setFont(f)
        lab = QRectF(x, y + size + 2, size, 18)
        p.fillRect(lab, QColor(20, 20, 20, 210))
        p.setPen(QColor(255, 255, 255))
        p.drawText(lab, Qt.AlignmentFlag.AlignCenter, f"{self.cur.x()}, {self.cur.y()}")

    def _help(self, p):
        scr = self.shot.screen_at(self.cur).intersected(self.area)
        if scr.isEmpty():
            scr = self.area
        scr = self._local(scr)
        click = "window" if self.windows else "whole monitor"
        text = (f"Drag: region  ·  Click: {click}  ·  Enter: whole monitor  ·  "
                "Arrows: fine-move  ·  Esc: cancel")
        f = QFont("Sans")
        f.setPixelSize(13)
        p.setFont(f)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 24, fm.height() + 12
        r = QRectF(scr.x() + (scr.width() - w) / 2, scr.y() + 12, w, h)
        if r.contains(QPointF(self.cur - self.area.topLeft())):
            r.moveTop(scr.bottom() - h - 12)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(20, 20, 20, 200))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor(235, 235, 235))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)


def window_rects_logical(grab):
    return [grab.to_logical(r) for r in x11.window_rects()]


def active_window_logical(grab):
    return grab.to_logical(x11.active_window_rect())


def open_overlay(grab, window_rects, wayland=False):
    """Show the selection overlay; returns the window whose `done` signal reports the result."""
    if not wayland:
        ov = Overlay(grab, window_rects)
        ov.start()
        return ov
    panels = []
    sel = None
    for area in grab.screen_rects:
        ov = Overlay(grab, [], area=area, sel=sel, wayland=True)
        sel = ov.g
        panels.append(ov)
    for ov in panels:
        ov.start()
    return panels[0]
