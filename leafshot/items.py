"""Annotation objects. Every annotation stays an independent, editable object:
selectable, movable, resizable (handles), restylable, copy/paste-able.

State dicts (get_state / set_state) drive undo/redo and the clipboard.
"""
import base64
import math

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QLineF, QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QCursor, QFont, QImage, QPainter, QPainterPath,
                         QPainterPathStroker, QPen, QPolygonF, QTextCursor)
from PyQt6.QtWidgets import (QGraphicsDropShadowEffect, QGraphicsItem, QGraphicsTextItem,
                             QStyle)

HANDLE_PX = 9  # handle size in screen pixels
GI = QGraphicsItem.GraphicsItemFlag


def color_to_str(c):
    return c.name(QColor.NameFormat.HexArgb) if c is not None and c.isValid() else ""


def str_to_color(s):
    return QColor(s) if s else QColor()


def _view_scale(item):
    sc = item.scene()
    if sc and sc.views():
        return max(0.05, sc.views()[0].transform().m11())
    return 1.0


def _snap45(dx, dy):
    """Constrain a vector to multiples of 45 degrees (Shift)."""
    ang = math.atan2(dy, dx)
    length = math.hypot(dx, dy)
    ang = round(ang / (math.pi / 4)) * (math.pi / 4)
    return QPointF(length * math.cos(ang), length * math.sin(ang))


def _record(item, before):
    sc = item.scene()
    if sc is not None and hasattr(sc, "record_change"):
        sc.record_change(item, before)


class AnnotationMixin:
    """Shared style + state handling."""
    TYPE = "base"
    STYLE_KEYS = ()  # which property-bar settings apply
    SHADOW_CAPABLE = True

    def init_annotation(self):
        self.stroke = QColor("#e53935")
        self.fill = QColor()
        self.width = 3.0
        self.shadow = False
        self.setFlags(GI.ItemIsSelectable | GI.ItemIsMovable | GI.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)

    # -- style -------------------------------------------------------------
    def apply_style(self, key, value):
        if key == "stroke":
            self.stroke = QColor(value)
        elif key == "fill":
            self.fill = QColor(value) if value is not None else QColor()
        elif key == "width":
            self.width = float(value)
        elif key == "shadow":
            self.shadow = bool(value)
            self._update_shadow()
        self._style_changed()

    def style_value(self, key):
        return {"stroke": self.stroke, "fill": self.fill, "width": self.width,
                "shadow": self.shadow}.get(key)

    def _style_changed(self):
        self.prepareGeometryChange()
        self.update()

    def _update_shadow(self):
        if self.shadow and self.SHADOW_CAPABLE:
            eff = QGraphicsDropShadowEffect()
            eff.setBlurRadius(8)
            eff.setOffset(2.5, 2.5)
            eff.setColor(QColor(0, 0, 0, 140))
            self.setGraphicsEffect(eff)
        else:
            self.setGraphicsEffect(None)

    # -- state -------------------------------------------------------------
    def base_state(self):
        return {"type": self.TYPE, "x": self.pos().x(), "y": self.pos().y(),
                "z": self.zValue(), "stroke": color_to_str(self.stroke),
                "fill": color_to_str(self.fill), "width": self.width, "shadow": self.shadow}

    def set_base_state(self, s):
        self.prepareGeometryChange()
        self.setPos(s["x"], s["y"])
        self.setZValue(s.get("z", 0))
        self.stroke = str_to_color(s.get("stroke")) if s.get("stroke") else QColor("#e53935")
        self.fill = str_to_color(s.get("fill"))
        self.width = float(s.get("width", 3))
        self.shadow = bool(s.get("shadow", False))
        self._update_shadow()

    def get_state(self):
        return self.base_state()

    def set_state(self, s):
        self.set_base_state(s)
        self.update()

    # -- creation protocol (used by the view while drawing) ------------------
    def begin(self, scene_pos):
        self.setPos(scene_pos)

    def drag_to(self, scene_pos, shift=False):
        pass

    def finish(self):
        """Return False if the drawn item is degenerate and should be dropped."""
        return True

    def handle_at(self, scene_pos):
        return None

    def pen(self):
        p = QPen(self.stroke, self.width)
        p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setCapStyle(Qt.PenCapStyle.RoundCap)
        return p

    def _handle_size(self):
        return HANDLE_PX / _view_scale(self)

    @staticmethod
    def _paint_handle(painter, center, hs):
        r = QRectF(center.x() - hs / 2, center.y() - hs / 2, hs, hs)
        painter.setPen(QPen(QColor(30, 30, 30), hs / HANDLE_PX))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRect(r)


# ---------------------------------------------------------------------------
# Rect-based shapes
# ---------------------------------------------------------------------------
_CURSORS = [Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor,
            Qt.CursorShape.SizeBDiagCursor, Qt.CursorShape.SizeHorCursor,
            Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor,
            Qt.CursorShape.SizeBDiagCursor, Qt.CursorShape.SizeHorCursor]


class HandleMixin:
    """Mouse handling: drag a handle = resize, drag elsewhere = move (Qt default)."""

    def _move_cursor(self):
        sc = self.scene()
        if getattr(sc, "current_tool", "select") == "select":
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        else:
            self.unsetCursor()

    def hoverLeaveEvent(self, e):
        self.unsetCursor()
        super().hoverLeaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            h = self.handle_at(e.scenePos())
            if h is not None:
                self.start_resize(h)
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resize_handle is not None:
            self.resize_to(e.scenePos(), bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._resize_handle is not None:
            self.end_resize()
            e.accept()
            return
        super().mouseReleaseEvent(e)


class RectBasedItem(HandleMixin, AnnotationMixin, QGraphicsItem):
    KEEP_ASPECT = False

    def __init__(self):
        super().__init__()
        self.init_annotation()
        self.rect = QRectF()
        self._origin = QPointF()
        self._resize_handle = None
        self._before = None

    # geometry
    def _norm(self):
        return self.rect.normalized()

    def _handle_points(self):
        r = self._norm()
        c = r.center()
        return [r.topLeft(), QPointF(c.x(), r.top()), r.topRight(), QPointF(r.right(), c.y()),
                r.bottomRight(), QPointF(c.x(), r.bottom()), r.bottomLeft(),
                QPointF(r.left(), c.y())]

    def boundingRect(self):
        m = self.width / 2 + HANDLE_PX / _view_scale(self) + 2
        return self._norm().adjusted(-m, -m, m, m)

    def _outline_path(self):
        p = QPainterPath()
        p.addRect(self._norm())
        return p

    def shape(self):
        path = self._outline_path()
        if self.fill.isValid():
            out = QPainterPath(path)
        else:
            st = QPainterPathStroker()
            st.setWidth(max(12.0 / _view_scale(self), self.width + 6))
            out = st.createStroke(path)
        if self.isSelected():
            hs = self._handle_size()
            for pt in self._handle_points():
                out.addRect(QRectF(pt.x() - hs, pt.y() - hs, 2 * hs, 2 * hs))
        return out

    def paint_selection(self, painter):
        if not self.isSelected():
            return
        hs = self._handle_size()
        pen = QPen(QColor(40, 120, 255), 1 / _view_scale(self), Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._norm())
        for pt in self._handle_points():
            self._paint_handle(painter, pt, hs)

    # creation
    def begin(self, scene_pos):
        self.setPos(scene_pos)
        self.rect = QRectF(0, 0, 0, 0)

    def drag_to(self, scene_pos, shift=False):
        self.prepareGeometryChange()
        d = scene_pos - self.pos()
        if shift:
            s = max(abs(d.x()), abs(d.y()))
            d = QPointF(math.copysign(s, d.x() or 1), math.copysign(s, d.y() or 1))
        self.rect = QRectF(0, 0, d.x(), d.y())
        self.update()

    def finish(self):
        self.prepareGeometryChange()
        self.rect = self.rect.normalized()
        return self.rect.width() >= 3 and self.rect.height() >= 3

    # state
    def get_state(self):
        s = self.base_state()
        r = self._norm()
        s["rect"] = [r.x(), r.y(), r.width(), r.height()]
        return s

    def set_state(self, s):
        self.set_base_state(s)
        self.prepareGeometryChange()
        self.rect = QRectF(*s["rect"])
        self.update()

    # handles / resizing
    def handle_at(self, scene_pos):
        if not self.isSelected():
            return None
        p = self.mapFromScene(scene_pos)
        hs = self._handle_size()
        for i, pt in enumerate(self._handle_points()):
            if abs(p.x() - pt.x()) <= hs and abs(p.y() - pt.y()) <= hs:
                return i
        return None

    def hoverMoveEvent(self, e):
        h = self.handle_at(e.scenePos())
        if h is not None:
            self.setCursor(QCursor(_CURSORS[h]))
        else:
            self._move_cursor()
        super().hoverMoveEvent(e)

    def start_resize(self, handle):
        self._resize_handle = handle
        self._before = self.get_state()
        self._aspect = (self._norm().width() / self._norm().height()
                        if self._norm().height() else 1.0)

    def resize_to(self, scene_pos, shift=False):
        p = self.mapFromScene(scene_pos)
        r = QRectF(self._norm())
        h = self._resize_handle
        if h in (0, 6, 7):
            r.setLeft(p.x())
        if h in (2, 3, 4):
            r.setRight(p.x())
        if h in (0, 1, 2):
            r.setTop(p.y())
        if h in (4, 5, 6):
            r.setBottom(p.y())
        keep = self.KEEP_ASPECT != shift  # Shift toggles aspect lock
        if keep and h in (0, 2, 4, 6) and self._aspect > 0:
            w = abs(r.width())
            new_h = w / self._aspect
            if h in (0, 2):
                r.setTop(r.bottom() - math.copysign(new_h, r.height() or 1))
            else:
                r.setBottom(r.top() + math.copysign(new_h, r.height() or 1))
        self.prepareGeometryChange()
        self.rect = r
        self.update()

    def end_resize(self):
        self.prepareGeometryChange()
        self.rect = self.rect.normalized()
        self._resize_handle = None
        if self._before is not None:
            _record(self, self._before)
        self._before = None


class RectItem(RectBasedItem):
    TYPE = "rect"
    STYLE_KEYS = ("stroke", "fill", "width", "shadow")

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self.pen() if self.width > 0 else Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.fill) if self.fill.isValid() else Qt.BrushStyle.NoBrush)
        painter.drawRect(self._norm())
        self.paint_selection(painter)


class EllipseItem(RectBasedItem):
    TYPE = "ellipse"
    STYLE_KEYS = ("stroke", "fill", "width", "shadow")

    def _outline_path(self):
        p = QPainterPath()
        p.addEllipse(self._norm())
        return p

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self.pen() if self.width > 0 else Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(self.fill) if self.fill.isValid() else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(self._norm())
        self.paint_selection(painter)


class PixelateItem(RectBasedItem):
    """Pixelates the screenshot underneath (for hiding sensitive info)."""
    TYPE = "pixelate"
    STYLE_KEYS = ("pixel_size",)
    SHADOW_CAPABLE = False

    def __init__(self):
        super().__init__()
        self.pixel_size = 10
        self._cache_key = None
        self._cache_img = None

    def apply_style(self, key, value):
        if key == "pixel_size":
            self.pixel_size = max(2, int(value))
            self._cache_key = None
            self.update()
        # other keys don't apply

    def style_value(self, key):
        return self.pixel_size if key == "pixel_size" else None

    def _outline_path(self):
        p = QPainterPath()
        p.addRect(self._norm())
        return p

    def shape(self):
        out = self._outline_path()
        if self.isSelected():
            hs = self._handle_size()
            for pt in self._handle_points():
                out.addRect(QRectF(pt.x() - hs, pt.y() - hs, 2 * hs, 2 * hs))
        return out

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._cache_key = None
        return super().itemChange(change, value)

    def get_state(self):
        s = super().get_state()
        s["pixel_size"] = self.pixel_size
        return s

    def set_state(self, s):
        self.pixel_size = int(s.get("pixel_size", 10))
        self._cache_key = None
        super().set_state(s)

    def paint(self, painter, option, widget=None):
        sc = self.scene()
        bg = getattr(getattr(sc, "doc", None), "background", None)
        r = self._norm()
        if bg is not None and not bg.isNull() and r.width() >= 1 and r.height() >= 1:
            src = self.mapRectToScene(r).toAlignedRect().intersected(bg.rect())
            key = (src.x(), src.y(), src.width(), src.height(), self.pixel_size, bg.cacheKey())
            if key != self._cache_key and not src.isEmpty():
                ps = self.pixel_size
                img = bg.copy(src)
                small = img.scaled(max(1, src.width() // ps), max(1, src.height() // ps),
                                   Qt.AspectRatioMode.IgnoreAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
                self._cache_img = small.scaled(src.width(), src.height(),
                                               Qt.AspectRatioMode.IgnoreAspectRatio,
                                               Qt.TransformationMode.FastTransformation)
                self._cache_key = key
            if self._cache_img is not None and not src.isEmpty():
                painter.drawImage(self.mapRectFromScene(QRectF(src)), self._cache_img)
        if self.isSelected():
            self.paint_selection(painter)


class CoverItem(RectBasedItem):
    """Blank something out with the background color found around it.
    Re-samples automatically when moved or resized."""
    TYPE = "cover"
    STYLE_KEYS = ()
    SHADOW_CAPABLE = False

    def __init__(self):
        super().__init__()
        self._key = None
        self._color = QColor(128, 128, 128)

    def _outline_path(self):
        p = QPainterPath()
        p.addRect(self._norm())
        return p

    def shape(self):
        out = self._outline_path()
        if self.isSelected():
            hs = self._handle_size()
            for pt in self._handle_points():
                out.addRect(QRectF(pt.x() - hs, pt.y() - hs, 2 * hs, 2 * hs))
        return out

    def sampled_color(self):
        bg = getattr(getattr(self.scene(), "doc", None), "background", None)
        if bg is None or bg.isNull():
            return self._color
        r = self.mapRectToScene(self._norm()).toAlignedRect()
        key = (r.x(), r.y(), r.width(), r.height(), bg.cacheKey())
        if key == self._key:
            return self._color
        self._key = key
        ring = r.adjusted(-2, -2, 2, 2)
        pts = []
        n = max(8, min(400, (ring.width() + ring.height()) // 2))
        for i in range(n + 1):
            t = i / n
            x = round(ring.left() + t * ring.width())
            y = round(ring.top() + t * ring.height())
            pts += [(x, ring.top()), (x, ring.bottom()), (ring.left(), y), (ring.right(), y)]
        counts = {}
        for x, y in pts:
            if bg.valid(x, y):
                rgb = bg.pixel(x, y) & 0xFFFFFF
                counts[rgb] = counts.get(rgb, 0) + 1
        if counts:
            self._color = QColor(max(counts, key=counts.get))
        return self._color

    def get_state(self):
        s = super().get_state()
        s["cover_color"] = color_to_str(self.sampled_color())
        return s

    def paint(self, painter, option, widget=None):
        painter.fillRect(self._norm(), self.sampled_color())
        if self.isSelected():
            self.paint_selection(painter)


class ImageItem(RectBasedItem):
    """A pasted image (e.g. a crop from another screenshot)."""
    TYPE = "image"
    STYLE_KEYS = ("shadow",)
    KEEP_ASPECT = True

    def __init__(self, image=None):
        super().__init__()
        self.image = image if image is not None else QImage()
        if not self.image.isNull():
            self.rect = QRectF(0, 0, self.image.width(), self.image.height())

    def _outline_path(self):
        p = QPainterPath()
        p.addRect(self._norm())
        return p

    def shape(self):
        out = self._outline_path()
        if self.isSelected():
            hs = self._handle_size()
            for pt in self._handle_points():
                out.addRect(QRectF(pt.x() - hs, pt.y() - hs, 2 * hs, 2 * hs))
        return out

    def get_state(self):
        s = super().get_state()
        s["image"] = self.image  # kept as object; serialized only for the clipboard
        return s

    def set_state(self, s):
        self.image = s["image"]
        super().set_state(s)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(self._norm(), self.image)
        self.paint_selection(painter)


# ---------------------------------------------------------------------------
# Line / arrow
# ---------------------------------------------------------------------------
class LineItem(HandleMixin, AnnotationMixin, QGraphicsItem):
    TYPE = "line"
    STYLE_KEYS = ("stroke", "width", "shadow")

    def __init__(self):
        super().__init__()
        self.init_annotation()
        self.p1 = QPointF()
        self.p2 = QPointF()
        self._resize_handle = None
        self._before = None

    def _path(self):
        p = QPainterPath(self.p1)
        p.lineTo(self.p2)
        return p

    def _extra(self):
        return 0.0

    def boundingRect(self):
        m = self.width / 2 + self._extra() + HANDLE_PX / _view_scale(self) + 2
        return QRectF(self.p1, self.p2).normalized().adjusted(-m, -m, m, m)

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(max(12.0 / _view_scale(self), self.width + 8 + self._extra()))
        out = st.createStroke(self._path())
        if self.isSelected():
            hs = self._handle_size()
            for pt in (self.p1, self.p2):
                out.addRect(QRectF(pt.x() - hs, pt.y() - hs, 2 * hs, 2 * hs))
        return out

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(self.pen())
        painter.drawLine(self.p1, self.p2)
        self.paint_selection(painter)

    def paint_selection(self, painter):
        if self.isSelected():
            hs = self._handle_size()
            for pt in (self.p1, self.p2):
                self._paint_handle(painter, pt, hs)

    def begin(self, scene_pos):
        self.setPos(scene_pos)
        self.p1 = QPointF(0, 0)
        self.p2 = QPointF(0, 0)

    def drag_to(self, scene_pos, shift=False):
        self.prepareGeometryChange()
        d = scene_pos - self.pos()
        self.p2 = _snap45(d.x(), d.y()) if shift else d
        self.update()

    def finish(self):
        return QLineF(self.p1, self.p2).length() >= 4

    def get_state(self):
        s = self.base_state()
        s["p1"] = [self.p1.x(), self.p1.y()]
        s["p2"] = [self.p2.x(), self.p2.y()]
        return s

    def set_state(self, s):
        self.set_base_state(s)
        self.prepareGeometryChange()
        self.p1 = QPointF(*s["p1"])
        self.p2 = QPointF(*s["p2"])
        self.update()

    def handle_at(self, scene_pos):
        if not self.isSelected():
            return None
        p = self.mapFromScene(scene_pos)
        hs = self._handle_size()
        for i, pt in enumerate((self.p1, self.p2)):
            if abs(p.x() - pt.x()) <= hs and abs(p.y() - pt.y()) <= hs:
                return i
        return None

    def hoverMoveEvent(self, e):
        if self.handle_at(e.scenePos()) is not None:
            self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        else:
            self._move_cursor()
        super().hoverMoveEvent(e)

    def start_resize(self, handle):
        self._resize_handle = handle
        self._before = self.get_state()

    def resize_to(self, scene_pos, shift=False):
        p = self.mapFromScene(scene_pos)
        other = self.p2 if self._resize_handle == 0 else self.p1
        if shift:
            p = other + _snap45(p.x() - other.x(), p.y() - other.y())
        self.prepareGeometryChange()
        if self._resize_handle == 0:
            self.p1 = p
        else:
            self.p2 = p
        self.update()

    def end_resize(self):
        self._resize_handle = None
        if self._before is not None:
            _record(self, self._before)
        self._before = None


class ArrowItem(LineItem):
    TYPE = "arrow"

    def _head(self):
        return 3.2 * self.width + 9

    def _extra(self):
        return self._head() / 2

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        line = QLineF(self.p1, self.p2)
        length = line.length()
        if length < 0.5:
            return
        head = min(self._head(), length * 0.9)
        ux, uy = (self.p2.x() - self.p1.x()) / length, (self.p2.y() - self.p1.y()) / length
        base = QPointF(self.p2.x() - ux * head, self.p2.y() - uy * head)
        half = head * 0.45
        left = QPointF(base.x() - uy * half, base.y() + ux * half)
        right = QPointF(base.x() + uy * half, base.y() - ux * half)
        pen = self.pen()
        painter.setPen(pen)
        # stop the shaft inside the head so the tip stays sharp
        shaft_end = QPointF(self.p2.x() - ux * head * 0.6, self.p2.y() - uy * head * 0.6)
        painter.drawLine(self.p1, shaft_end)
        painter.setPen(QPen(self.stroke, 1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.MiterJoin))
        painter.setBrush(self.stroke)
        painter.drawPolygon(QPolygonF([self.p2, left, right]))
        self.paint_selection(painter)


# ---------------------------------------------------------------------------
# Freehand / highlighter
# ---------------------------------------------------------------------------
class FreehandItem(AnnotationMixin, QGraphicsItem):
    TYPE = "freehand"
    STYLE_KEYS = ("stroke", "width", "shadow")
    HIGHLIGHT = False

    def __init__(self):
        super().__init__()
        self.init_annotation()
        self.points = []
        self._path_cache = None

    def eff_width(self):
        return self.width

    def _path(self):
        if self._path_cache is None:
            pts = self.points
            path = QPainterPath()
            if pts:
                path.moveTo(pts[0])
                if len(pts) == 1:
                    path.lineTo(pts[0] + QPointF(0.01, 0))
                elif len(pts) == 2:
                    path.lineTo(pts[1])
                else:
                    for i in range(1, len(pts) - 1):
                        mid = (pts[i] + pts[i + 1]) / 2
                        path.quadTo(pts[i], mid)
                    path.lineTo(pts[-1])
            self._path_cache = path
        return self._path_cache

    def boundingRect(self):
        m = self.eff_width() / 2 + 4
        return self._path().boundingRect().adjusted(-m, -m, m, m)

    def shape(self):
        st = QPainterPathStroker()
        st.setWidth(max(12.0 / _view_scale(self), self.eff_width() + 6))
        st.setCapStyle(Qt.PenCapStyle.RoundCap)
        return st.createStroke(self._path())

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = self.pen()
        pen.setWidthF(self.eff_width())
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(self._path())
        if self.isSelected():
            painter.setPen(QPen(QColor(40, 120, 255), 1 / _view_scale(self), Qt.PenStyle.DashLine))
            painter.drawRect(self._path().boundingRect())

    def begin(self, scene_pos):
        self.setPos(scene_pos)
        self.points = [QPointF(0, 0)]
        self._path_cache = None

    def drag_to(self, scene_pos, shift=False):
        self.prepareGeometryChange()
        p = scene_pos - self.pos()
        if shift:
            d = _snap45(p.x(), p.y())
            self.points = [QPointF(0, 0), d]
        elif not self.points or QLineF(self.points[-1], p).length() >= 1.5:
            self.points.append(p)
        self._path_cache = None
        self.update()

    def finish(self):
        return len(self.points) >= 2

    def get_state(self):
        s = self.base_state()
        s["points"] = [[p.x(), p.y()] for p in self.points]
        return s

    def set_state(self, s):
        self.set_base_state(s)
        self.prepareGeometryChange()
        self.points = [QPointF(x, y) for x, y in s["points"]]
        self._path_cache = None
        self.update()


class HighlighterItem(FreehandItem):
    TYPE = "highlighter"
    STYLE_KEYS = ("stroke", "width")
    SHADOW_CAPABLE = False

    def eff_width(self):
        return max(10.0, self.width * 5)

    def pen(self):
        c = QColor(self.stroke)
        if getattr(self, "_dark_paint", False):
            # a darker tint keeps light text readable on dark backgrounds
            c = QColor(int(c.red() * 0.5), int(c.green() * 0.5), int(c.blue() * 0.5))
        p = QPen(c, self.eff_width())
        p.setCapStyle(Qt.PenCapStyle.SquareCap)
        p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return p

    def __init__(self):
        super().__init__()
        self._lum_key = None
        self._dark = False

    def _on_dark(self):
        """Is the screenshot under the stroke mostly dark? (cached)"""
        bg = getattr(getattr(self.scene(), "doc", None), "background", None)
        if bg is None or bg.isNull():
            return False
        r = self.sceneBoundingRect().toAlignedRect().intersected(bg.rect())
        key = (r.x(), r.y(), r.width(), r.height(), bg.cacheKey())
        if key != self._lum_key:
            self._lum_key = key
            if r.isEmpty():
                self._dark = False
            else:
                small = bg.copy(r).scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
                tot = sum(small.pixelColor(x, y).lightnessF()
                          for x in range(small.width()) for y in range(small.height()))
                self._dark = tot / (small.width() * small.height()) < 0.45
        return self._dark

    def paint(self, painter, option, widget=None):
        # Light background: multiply (like a real marker, text stays crisp).
        # Dark background: screen (lightens, so the color glows instead of vanishing).
        dark = self._on_dark()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen if dark
                                   else QPainter.CompositionMode.CompositionMode_Multiply)
        self._dark_paint = dark
        super().paint(painter, option, widget)
        self._dark_paint = False


# ---------------------------------------------------------------------------
# Numbered step marker
# ---------------------------------------------------------------------------
class StepItem(AnnotationMixin, QGraphicsItem):
    TYPE = "step"
    STYLE_KEYS = ("stroke", "font_size", "shadow")

    def __init__(self):
        super().__init__()
        self.init_annotation()
        self.number = 1
        self.font_size = 18

    def diameter(self):
        return self.font_size * 1.7 + 4

    def apply_style(self, key, value):
        if key == "font_size":
            self.prepareGeometryChange()
            self.font_size = int(value)
            self.update()
        else:
            super().apply_style(key, value)

    def style_value(self, key):
        if key == "font_size":
            return self.font_size
        return super().style_value(key)

    def boundingRect(self):
        d = self.diameter()
        return QRectF(-d / 2 - 3, -d / 2 - 3, d + 6, d + 6)

    def shape(self):
        p = QPainterPath()
        d = self.diameter()
        p.addEllipse(QRectF(-d / 2, -d / 2, d, d))
        return p

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self.diameter()
        r = QRectF(-d / 2, -d / 2, d, d)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(self.stroke)
        painter.drawEllipse(r)
        light = self.stroke.lightnessF() > 0.7
        painter.setPen(QColor(20, 20, 20) if light else QColor(255, 255, 255))
        f = QFont("Sans")
        f.setBold(True)
        f.setPixelSize(max(6, int(self.font_size * (0.95 if self.number < 10 else 0.8))))
        painter.setFont(f)
        painter.drawText(r, Qt.AlignmentFlag.AlignCenter, str(self.number))
        if self.isSelected():
            painter.setPen(QPen(QColor(40, 120, 255), 1.5 / _view_scale(self), Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(r.adjusted(-2, -2, 2, 2))

    def get_state(self):
        s = self.base_state()
        s["number"] = self.number
        s["font_size"] = self.font_size
        return s

    def set_state(self, s):
        self.set_base_state(s)
        self.prepareGeometryChange()
        self.number = int(s.get("number", 1))
        self.font_size = int(s.get("font_size", 18))
        self.update()

    def finish(self):
        return True


# ---------------------------------------------------------------------------
# Text (editable at any time: double-click)
# ---------------------------------------------------------------------------
class TextItem(AnnotationMixin, QGraphicsTextItem):
    TYPE = "text"
    STYLE_KEYS = ("stroke", "fill", "border", "width", "font_family", "font_size", "bold",
                  "italic", "underline", "align", "shadow")
    ALIGN = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
             "right": Qt.AlignmentFlag.AlignRight}

    def __init__(self):
        super().__init__()
        self.init_annotation()
        self.font_size = 18
        self.font_family = "Sans"
        self.border = QColor()  # frame color; invalid = no frame
        self.width = 2
        self.bold = True
        self.italic = False
        self.underline = False
        self.pending_add = False  # new item: undo entry is created when editing ends
        self._edit_before = None
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.align = "left"
        self._fitting = False
        self.document().setDocumentMargin(4)
        self.document().contentsChanged.connect(self._fit_width)
        self._apply_font()

    def _apply_font(self):
        f = QFont(self.font_family or "Sans")
        f.setPixelSize(max(6, int(self.font_size)))
        f.setBold(self.bold)
        f.setItalic(self.italic)
        f.setUnderline(self.underline)
        self.setFont(f)
        self.setDefaultTextColor(self.stroke)
        opt = self.document().defaultTextOption()
        opt.setAlignment(self.ALIGN.get(getattr(self, "align", "left"), Qt.AlignmentFlag.AlignLeft))
        self.document().setDefaultTextOption(opt)
        self._fit_width()

    def _fit_width(self):
        """Lines are aligned within the box; the box always hugs the longest line."""
        if getattr(self, "_fitting", True):
            return
        self._fitting = True
        try:
            self.prepareGeometryChange()
            self.setTextWidth(-1)
            self.setTextWidth(self.document().idealWidth() + 1)
        finally:
            self._fitting = False

    def apply_style(self, key, value):
        if key == "font_size":
            self.font_size = int(value)
        elif key == "font_family":
            self.font_family = str(value) or "Sans"
        elif key == "align":
            self.align = value if value in self.ALIGN else "left"
        elif key == "border":
            self.border = QColor(value) if value is not None else QColor()
        elif key in ("bold", "italic", "underline"):
            setattr(self, key, bool(value))
        else:
            super().apply_style(key, value)
        self._apply_font()
        self.update()

    def style_value(self, key):
        if key == "font_size":
            return self.font_size
        if key in ("bold", "italic", "underline", "font_family", "align", "border"):
            return getattr(self, key)
        return super().style_value(key)

    def _style_changed(self):
        self._apply_font()
        self.update()

    def _frame_w(self):
        return self.width if self.border.isValid() and self.width > 0 else 0

    def boundingRect(self):
        m = self._frame_w() + 1 if self._frame_w() else 0
        return super().boundingRect().adjusted(-m, -m, m, m)

    def paint(self, painter, option, widget=None):
        box = super().boundingRect()
        fw = self._frame_w()
        if self.fill.isValid() or fw:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if self.fill.isValid():
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.fill)
                painter.drawRoundedRect(box, 4, 4)
            if fw:
                pen = QPen(self.border, fw)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                # the frame sits just outside the text box, so it never touches the text
                painter.drawRoundedRect(box.adjusted(-fw / 2, -fw / 2, fw / 2, fw / 2),
                                        4 + fw / 2, 4 + fw / 2)
        option.state &= ~QStyle.StateFlag.State_HasFocus
        sel = bool(option.state & QStyle.StateFlag.State_Selected)
        option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, widget)
        if sel or self.is_editing():
            pen = QPen(QColor(40, 120, 255), 1 / _view_scale(self), Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self.boundingRect())

    # editing
    def is_editing(self):
        return bool(self.textInteractionFlags() & Qt.TextInteractionFlag.TextEditable)

    def start_edit(self, select_all=False):
        if self.is_editing():
            return
        self._edit_before = None if self.pending_add else self.get_state()
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextEditorInteraction)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        cur = self.textCursor()
        if select_all:
            cur.select(QTextCursor.SelectionType.Document)
        else:
            cur.movePosition(QTextCursor.MoveOperation.End)
        self.setTextCursor(cur)
        self.update()

    def end_edit(self):
        if not self.is_editing():
            return
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        cur = self.textCursor()
        cur.clearSelection()
        self.setTextCursor(cur)
        text = self.toPlainText()
        if text:
            # strip any rich formatting that came in via paste
            self.setPlainText(text)
            self._apply_font()
        sc = self.scene()
        if sc is not None and hasattr(sc, "text_edit_finished"):
            sc.text_edit_finished(self, self._edit_before)
        self._edit_before = None
        self.update()

    def mouseDoubleClickEvent(self, e):
        if not self.is_editing():
            self.start_edit()
            # place the caret where the user double-clicked
            super().mousePressEvent(e)
            return
        super().mouseDoubleClickEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if e.reason() != Qt.FocusReason.PopupFocusReason:
            self.end_edit()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape or (
                e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.clearFocus()
            return
        super().keyPressEvent(e)

    def get_state(self):
        s = self.base_state()
        s["text"] = self.toPlainText()
        s["font_size"] = self.font_size
        s["font_family"] = self.font_family
        s["border"] = color_to_str(self.border)
        s["align"] = self.align
        s["bold"] = self.bold
        s["italic"] = self.italic
        s["underline"] = self.underline
        return s

    def set_state(self, s):
        self.set_base_state(s)
        self.font_size = int(s.get("font_size", 18))
        self.font_family = s.get("font_family") or "Sans"
        self.border = str_to_color(s.get("border"))
        self.align = s.get("align") if s.get("align") in self.ALIGN else "left"
        self.bold = bool(s.get("bold", True))
        self.italic = bool(s.get("italic", False))
        self.underline = bool(s.get("underline", False))
        self.setPlainText(s.get("text", ""))
        self._apply_font()
        self.update()


TYPES = {cls.TYPE: cls for cls in (RectItem, EllipseItem, PixelateItem, CoverItem, ImageItem, LineItem,
                                   ArrowItem, FreehandItem, HighlighterItem, StepItem,
                                   TextItem)}


def make_item(state):
    item = TYPES[state["type"]]()
    item.set_state(state)
    return item


# -- clipboard serialization -------------------------------------------------
def _img_to_b64(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return base64.b64encode(bytes(ba)).decode()


def _b64_to_img(s):
    img = QImage()
    img.loadFromData(base64.b64decode(s), "PNG")
    return img


def states_to_json_ready(states):
    out = []
    for s in states:
        s = dict(s)
        if isinstance(s.get("image"), QImage):
            s["image"] = _img_to_b64(s["image"])
        out.append(s)
    return out


def states_from_json(states):
    out = []
    for s in states:
        s = dict(s)
        if s.get("type") not in TYPES:
            continue
        if s.get("type") == "image" and isinstance(s.get("image"), str):
            s["image"] = _b64_to_img(s["image"])
        out.append(s)
    return out
