"""Small vector icons drawn in code (no icon-theme or SVG-plugin dependency)."""
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

FG = QColor(60, 60, 64)


def _canvas(size=48):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 24, size / 24)
    return pm, p


def _icon(draw, fg=None):
    pm, p = _canvas()
    draw(p, fg or FG)
    p.end()
    return QIcon(pm)


def is_dark(color):
    return color.lightnessF() > 0.5  # light text color => dark theme


def _pen(c, w=1.8):
    pen = QPen(c, w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def tool_icon(tool, fg=None, bg=None):
    bg = bg or QColor(255, 255, 255)

    def draw(p, c):
        p.setPen(_pen(c))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if tool == "select":
            p.setBrush(c)
            p.drawPolygon(QPolygonF([QPointF(6, 3), QPointF(6, 19), QPointF(10, 15),
                                     QPointF(13, 21), QPointF(15.5, 20), QPointF(12.5, 14),
                                     QPointF(18, 14)]))
        elif tool == "rect":
            p.drawRect(QRectF(4, 6, 16, 12))
        elif tool == "ellipse":
            p.drawEllipse(QRectF(3.5, 6, 17, 12))
        elif tool == "line":
            p.drawLine(QPointF(4, 20), QPointF(20, 4))
        elif tool == "arrow":
            p.drawLine(QPointF(4, 20), QPointF(17, 7))
            p.setBrush(c)
            p.drawPolygon(QPolygonF([QPointF(20.5, 3.5), QPointF(12, 6.5), QPointF(17.5, 12)]))
        elif tool == "freehand":
            path = QPainterPath(QPointF(3, 17))
            path.cubicTo(QPointF(7, 5), QPointF(10, 22), QPointF(14, 11))
            path.cubicTo(QPointF(16, 6), QPointF(19, 8), QPointF(21, 6))
            p.drawPath(path)
        elif tool == "highlighter":
            p.setPen(_pen(QColor(255, 214, 0, 220), 6))
            p.drawLine(QPointF(5, 16), QPointF(19, 16))
            p.setPen(_pen(c, 1.6))
            p.drawLine(QPointF(5, 9), QPointF(19, 9))
        elif tool == "text":
            f = QFont("Sans")
            f.setBold(True)
            f.setPixelSize(17)
            p.setFont(f)
            p.drawText(QRectF(0, 0, 24, 24), Qt.AlignmentFlag.AlignCenter, "T")
        elif tool == "step":
            p.setBrush(c)
            p.drawEllipse(QRectF(3.5, 3.5, 17, 17))
            p.setPen(bg)
            f = QFont("Sans")
            f.setBold(True)
            f.setPixelSize(12)
            p.setFont(f)
            p.drawText(QRectF(3.5, 3.5, 17, 17), Qt.AlignmentFlag.AlignCenter, "1")
        elif tool == "pixelate":
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(4):
                for j in range(4):
                    shade = 90 + ((i * 7 + j * 13) % 5) * 30
                    p.setBrush(QColor(shade, shade, shade + 10))
                    p.drawRect(QRectF(4 + i * 4, 4 + j * 4, 4, 4))
        elif tool == "cover":
            p.drawLine(QPointF(3, 8), QPointF(21, 8))
            p.drawLine(QPointF(3, 14), QPointF(10, 14))
            p.drawLine(QPointF(3, 20), QPointF(21, 20))
            p.setPen(_pen(c, 1.4))
            p.setBrush(bg)
            p.drawRect(QRectF(11.5, 11, 10, 6))
        elif tool == "crop":
            p.drawPolyline(QPolygonF([QPointF(7, 2), QPointF(7, 17), QPointF(22, 17)]))
            p.drawPolyline(QPolygonF([QPointF(2, 7), QPointF(17, 7), QPointF(17, 22)]))
    return _icon(draw, fg)


def action_icon(name, fg=None):
    """Toolbar icons drawn in code, so they match light and dark themes alike."""
    def draw(p, c):
        p.setPen(_pen(c, 1.7))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if name == "save":
            p.drawRoundedRect(QRectF(4, 4, 16, 16), 2, 2)
            p.drawRect(QRectF(8, 4, 8, 5))
            p.drawRect(QRectF(7, 13, 10, 7))
        elif name == "copy":
            p.drawRoundedRect(QRectF(8, 8, 12, 13), 2, 2)
            p.drawPolyline(QPolygonF([QPointF(5, 16), QPointF(4, 16), QPointF(4, 3),
                                      QPointF(15, 3), QPointF(15, 5)]))
        elif name in ("undo", "redo"):
            if name == "redo":
                p.translate(24, 0)
                p.scale(-1, 1)
            path = QPainterPath(QPointF(6, 10))
            path.lineTo(QPointF(15, 10))
            path.cubicTo(QPointF(22, 10), QPointF(22, 20), QPointF(15, 20))
            path.lineTo(QPointF(10, 20))
            p.drawPath(path)
            p.drawPolyline(QPolygonF([QPointF(10, 5.5), QPointF(5.5, 10), QPointF(10, 14.5)]))
        elif name == "fit":
            for (x, y, dx, dy) in ((4, 4, 1, 1), (20, 4, -1, 1), (4, 20, 1, -1), (20, 20, -1, -1)):
                p.drawLine(QPointF(x, y), QPointF(x + 5 * dx, y))
                p.drawLine(QPointF(x, y), QPointF(x, y + 5 * dy))
            p.drawRect(QRectF(9, 9, 6, 6))
        elif name == "100":
            f = QFont("Sans")
            f.setBold(True)
            f.setPixelSize(10)
            p.setFont(f)
            p.drawRoundedRect(QRectF(2.5, 5, 19, 14), 2, 2)
            p.drawText(QRectF(2.5, 5, 19, 14), Qt.AlignmentFlag.AlignCenter, "1:1")
        elif name.startswith("align-"):
            how = name[6:]
            for y, w in ((6, 16), (10.5, 10), (15, 16), (19.5, 10)):
                x = {"left": 4, "center": 12 - w / 2, "right": 20 - w}[how]
                p.drawLine(QPointF(x, y), QPointF(x + w, y))
        elif name == "clock":
            p.drawEllipse(QPointF(12, 13), 8, 8)
            p.drawPolyline(QPolygonF([QPointF(12, 8.5), QPointF(12, 13), QPointF(15, 15)]))
            p.drawLine(QPointF(9.5, 2.5), QPointF(14.5, 2.5))
        elif name == "trash":
            p.drawLine(QPointF(4.5, 7), QPointF(19.5, 7))
            p.drawPolyline(QPolygonF([QPointF(9.5, 7), QPointF(9.5, 4), QPointF(14.5, 4),
                                      QPointF(14.5, 7)]))
            p.drawPolyline(QPolygonF([QPointF(6.5, 7), QPointF(7.5, 20), QPointF(16.5, 20),
                                      QPointF(17.5, 7)]))
            p.drawLine(QPointF(10.2, 10.5), QPointF(10.5, 16.5))
            p.drawLine(QPointF(13.8, 10.5), QPointF(13.5, 16.5))
        elif name in ("lock", "unlock"):
            p.drawRoundedRect(QRectF(5, 11, 14, 10), 2, 2)
            path = QPainterPath(QPointF(8, 11))
            path.lineTo(QPointF(8, 8))
            path.cubicTo(QPointF(8, 3), QPointF(16, 3), QPointF(16, 8))
            if name == "lock":
                path.lineTo(QPointF(16, 11))
            p.drawPath(path)
            p.setBrush(c)
            p.drawEllipse(QPointF(12, 16), 1.3, 1.3)
    return _icon(draw, fg)


def color_icon(color, size=48):
    pm, p = _canvas(size)
    p.setPen(QPen(QColor(90, 90, 90), 1))
    if color is not None and color.isValid():
        # checkerboard under translucent colors
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 3, 3)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 3, 3)
    else:
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 3, 3)
        p.setPen(QPen(QColor(220, 40, 40), 2))
        p.drawLine(QPointF(5, 19), QPointF(19, 5))
    p.end()
    return QIcon(pm)


def app_icon_pixmap(size=256):
    """Leafshot logo (concept R2): viewfinder + two-tone leaf."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2F8F46"))
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    # viewfinder corners
    p.setPen(_pen(QColor(255, 255, 255), 4))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for pts in (((13, 23), (13, 13), (23, 13)), ((41, 13), (51, 13), (51, 23)),
                ((51, 41), (51, 51), (41, 51)), ((23, 51), (13, 51), (13, 41))):
        p.drawPolyline(QPolygonF([QPointF(*xy) for xy in pts]))
    # stem
    p.setPen(_pen(QColor(255, 255, 255), 3))
    p.drawLine(QPointF(19, 45), QPointF(14.5, 49.5))
    # two-tone leaf (concept R2), split along the midrib
    leaf = QPainterPath(QPointF(18, 46))
    leaf.cubicTo(QPointF(17, 31), QPointF(28, 18), QPointF(46, 18))
    leaf.cubicTo(QPointF(47, 33), QPointF(36, 46), QPointF(18, 46))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255))
    p.drawPath(leaf)
    half = QPainterPath(QPointF(0, 64))
    half.lineTo(QPointF(64, 0))
    half.lineTo(QPointF(64, 64))
    half.closeSubpath()
    p.setBrush(QColor("#BFEFC9"))
    p.drawPath(leaf.intersected(half))
    midrib = QPainterPath(QPointF(19.5, 44.5))
    midrib.cubicTo(QPointF(27, 37), QPointF(34, 30), QPointF(43, 21))
    p.setPen(_pen(QColor("#2F8F46"), 1.6))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(midrib)
    p.end()
    return pm


def countdown_icon(n, size=64):
    """Tray icon while a delayed capture is counting down."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2F8F46"))
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    p.setPen(QColor(255, 255, 255))
    f = QFont("Sans")
    f.setBold(True)
    f.setPixelSize(40 if n < 10 else 30)
    p.setFont(f)
    p.drawText(QRectF(4, 2, 56, 56), Qt.AlignmentFlag.AlignCenter, str(n))
    p.end()
    return QIcon(pm)


def app_icon():
    ic = QIcon()
    for s in (16, 22, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(app_icon_pixmap(s))
    return ic


def theme(name, fallback=None):
    ic = QIcon.fromTheme(name)
    if ic.isNull() and fallback is not None:
        return fallback
    return ic
