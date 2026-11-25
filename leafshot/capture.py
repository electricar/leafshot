"""Screen grabbing and the region-selection overlay."""
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QImage, QPainter, QPen,
                         QPixmap)
from PyQt6.QtWidgets import QWidget

from . import x11


class Grab:
    """A frozen copy of the whole virtual desktop."""

    def __init__(self):
        screens = QGuiApplication.screens()
        self.virtual = QRect()
        for s in screens:
            self.virtual = self.virtual.united(s.geometry())
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
        self.screen_rects = [s.geometry() for s in screens]

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


class Overlay(QWidget):
    """Fullscreen frozen screenshot: drag = region, click = window, Enter = monitor."""
    done = pyqtSignal(object)  # QRect (logical, global) or None

    DIM = QColor(0, 0, 0, 115)
    ACCENT = QColor(90, 160, 44)

    def __init__(self, grab, window_rects):
        super().__init__(None, Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.X11BypassWindowManagerHint |
                         Qt.WindowType.Tool)
        self.shot = grab
        self.pm = QPixmap.fromImage(grab.image)
        self.pm.setDevicePixelRatio(grab.scale)
        self.windows = [r for r in window_rects if r is not None]
        self.setGeometry(grab.virtual)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.press = None
        self.cur = QCursor.pos()
        self.dragging = False
        self._finished = False

    # geometry helpers (global logical coordinates)
    def _local(self, r):
        return r.translated(-self.shot.virtual.topLeft())

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
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabKeyboard()
        self.setFocus()

    def finish(self, rect):
        if self._finished:
            return
        self._finished = True
        self.releaseKeyboard()
        self.hide()
        self.done.emit(rect)
        self.close()

    # events
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton:
            self.finish(None)
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self.press = e.globalPosition().toPoint()
            self.cur = self.press
            self.dragging = False
            self.update()

    def mouseMoveEvent(self, e):
        self.cur = e.globalPosition().toPoint()
        if self.press is not None and not self.dragging:
            if (self.cur - self.press).manhattanLength() > 3:
                self.dragging = True
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self.press is None:
            return
        self.cur = e.globalPosition().toPoint()
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
            QCursor.setPos(QCursor.pos() + d)
            self.cur = QCursor.pos()
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pm)
        sel = self.selection()
        target = sel if sel is not None else (self.hovered() if self.press is None else None)
        p.fillRect(self.rect(), self.DIM)
        if target is not None:
            lt = self._local(target)
            p.drawPixmap(QRectF(lt), self.pm,
                         QRectF(QRectF(lt).topLeft() * self.shot.scale,
                                QRectF(lt).size() * self.shot.scale))
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
        c = self.cur - self.shot.virtual.topLeft()
        s = self.shot.scale
        src = QRectF((c.x() - n // 2) * s, (c.y() - n // 2) * s, n * s, n * s)
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
        scr = self._local(self.shot.screen_at(self.cur))
        text = "Drag: region  ·  Click: window  ·  Enter: whole monitor  ·  Arrows: fine-move  ·  Esc: cancel"
        f = QFont("Sans")
        f.setPixelSize(13)
        p.setFont(f)
        fm = p.fontMetrics()
        w, h = fm.horizontalAdvance(text) + 24, fm.height() + 12
        r = QRectF(scr.x() + (scr.width() - w) / 2, scr.y() + 12, w, h)
        if r.contains(QPointF(self.cur - self.shot.virtual.topLeft())):
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
