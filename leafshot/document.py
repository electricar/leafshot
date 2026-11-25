"""One screenshot = one Document: background image, scene with annotation
objects, its own undo stack and view."""
import time
import uuid

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QUndoCommand, QUndoStack
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from . import items as I

BG_Z = -1_000_000


def is_annot(it):
    return isinstance(it, I.AnnotationMixin) and getattr(it, "IS_ANNOTATION", True)


# ---------------------------------------------------------------------------
# Undo commands
# ---------------------------------------------------------------------------
class StateCommand(QUndoCommand):
    def __init__(self, item, before, after, text="Change"):
        super().__init__(text)
        self.item, self.before, self.after = item, before, after
        self._first = True

    def redo(self):
        if self._first:
            self._first = False
            return
        self.item.set_state(self.after)

    def undo(self):
        self.item.set_state(self.before)


class AddCommand(QUndoCommand):
    def __init__(self, scene, items, already_added=True, text="Add"):
        super().__init__(text)
        self.scene, self.items = scene, list(items)
        self._skip = already_added

    def redo(self):
        if self._skip:
            self._skip = False
            return
        for it in self.items:
            if it.scene() is None:
                self.scene.addItem(it)

    def undo(self):
        for it in self.items:
            if it.scene() is not None:
                it.setSelected(False)
                self.scene.removeItem(it)


class RemoveCommand(QUndoCommand):
    def __init__(self, scene, items, text="Delete"):
        super().__init__(text)
        self.scene, self.items = scene, list(items)

    def redo(self):
        for it in self.items:
            if it.scene() is not None:
                it.setSelected(False)
                self.scene.removeItem(it)

    def undo(self):
        for it in self.items:
            if it.scene() is None:
                self.scene.addItem(it)


class CropCommand(QUndoCommand):
    def __init__(self, doc, rect):
        super().__init__("Crop")
        self.doc = doc
        self.rect = rect.toAlignedRect().intersected(doc.background.rect())
        self.old_bg = doc.background

    def redo(self):
        self.doc.set_background(self.old_bg.copy(self.rect))
        d = QPointF(self.rect.topLeft())
        for it in self.doc.annotations():
            it.setPos(it.pos() - d)

    def undo(self):
        self.doc.set_background(self.old_bg)
        d = QPointF(self.rect.topLeft())
        for it in self.doc.annotations():
            it.setPos(it.pos() + d)


# ---------------------------------------------------------------------------
# Crop frame (temporary helper, not an annotation)
# ---------------------------------------------------------------------------
class CropFrame(I.RectBasedItem):
    TYPE = "_crop"
    IS_ANNOTATION = False

    def __init__(self):
        super().__init__()
        self.width = 1
        self.setZValue(10_000_000)
        self.setFlag(I.GI.ItemIsMovable, True)

    def boundingRect(self):
        sc = self.scene()
        r = sc.sceneRect() if sc else QRectF()
        return self.mapRectFromScene(r).united(super().boundingRect())

    def shape(self):
        from PyQt6.QtGui import QPainterPath
        p = QPainterPath()
        p.addRect(self._norm())
        if self.isSelected():
            hs = self._handle_size()
            for pt in self._handle_points():
                p.addRect(QRectF(pt.x() - hs, pt.y() - hs, 2 * hs, 2 * hs))
        return p

    def paint(self, painter, option, widget=None):
        sc = self.scene()
        if sc is None:
            return
        full = self.mapRectFromScene(sc.sceneRect())
        r = self._norm()
        dim = QColor(0, 0, 0, 120)
        painter.fillRect(QRectF(full.left(), full.top(), full.width(), r.top() - full.top()), dim)
        painter.fillRect(QRectF(full.left(), r.bottom(), full.width(), full.bottom() - r.bottom()), dim)
        painter.fillRect(QRectF(full.left(), r.top(), r.left() - full.left(), r.height()), dim)
        painter.fillRect(QRectF(r.right(), r.top(), full.right() - r.right(), r.height()), dim)
        painter.setPen(QPen(QColor(255, 255, 255), 1 / I._view_scale(self), Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r)
        hs = self._handle_size()
        for pt in self._handle_points():
            self._paint_handle(painter, pt, hs)

    def end_resize(self):
        self.prepareGeometryChange()
        self.rect = self.rect.normalized()
        self._resize_handle = None
        self._before = None


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------
class AnnotScene(QGraphicsScene):
    def __init__(self, doc):
        super().__init__()
        self.doc = doc
        self.current_tool = "select"
        self._press_states = None
        self.setBackgroundBrush(QColor(58, 58, 62))

    def record_change(self, item, before, text="Change"):
        after = item.get_state()
        if before != after:
            self.doc.undo.push(StateCommand(item, before, after, text))

    def text_edit_finished(self, item, before):
        empty = not item.toPlainText().strip()
        if item.pending_add:
            item.pending_add = False
            if empty:
                self.removeItem(item)
            else:
                self.doc.undo.push(AddCommand(self, [item], True, "Add text"))
        elif empty:
            self.doc.undo.beginMacro("Delete text")
            self.doc.undo.push(StateCommand(item, before, item.get_state()))
            self.doc.undo.push(RemoveCommand(self, [item]))
            self.doc.undo.endMacro()
        elif before is not None:
            self.record_change(item, before, "Edit text")
        if item.scene() is self and not empty:
            QTimer.singleShot(0, lambda: self.doc.view.editor.after_draw("text"))

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        self._press_states = {it: it.get_state() for it in self.selectedItems()
                              if is_annot(it)}

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        states, self._press_states = self._press_states, None
        if not states:
            return
        moved = [(it, b) for it, b in states.items()
                 if it.scene() is self and (it.pos().x(), it.pos().y()) != (b["x"], b["y"])]
        if moved:
            self.doc.undo.beginMacro("Move")
            for it, b in moved:
                self.doc.undo.push(StateCommand(it, b, it.get_state(), "Move"))
            self.doc.undo.endMacro()


# ---------------------------------------------------------------------------
# View: drawing tools, zoom, keyboard
# ---------------------------------------------------------------------------
class AnnotView(QGraphicsView):
    zoomChanged = pyqtSignal(float)

    def __init__(self, doc, editor):
        super().__init__(doc.scene)
        self.doc, self.editor = doc, editor
        self.setRenderHints(QPainter.RenderHint.Antialiasing |
                            QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drawing = None
        self._pan = None
        self.crop = None
        self._fitted_once = False
        self._pick_cb = None
        self._pick_img = None

    # -- tools --------------------------------------------------------------
    def set_tool(self, tool):
        if self._drawing is not None:
            self._cancel_drawing()
        if tool != "crop":
            self.cancel_crop()
        self.doc.scene.current_tool = tool
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if tool == "select"
                         else QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor if tool == "select"
                                  else Qt.CursorShape.CrossCursor)

    @property
    def tool(self):
        return self.doc.scene.current_tool

    def _handle_hit(self, scene_pos):
        for it in self.doc.scene.selectedItems():
            if hasattr(it, "handle_at") and it.handle_at(scene_pos) is not None:
                return it
        if self.crop is not None and self.crop.handle_at(scene_pos) is not None:
            return self.crop
        return None

    # -- eyedropper ---------------------------------------------------------------
    def start_pick(self, callback):
        self._pick_cb = callback
        self._pick_img = self.doc.render_image()
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()

    def _pick_color_at(self, pos):
        sp = self.mapToScene(pos.toPoint())
        x, y = int(sp.x()), int(sp.y())
        if self._pick_img is not None and self._pick_img.valid(x, y):
            c = self._pick_img.pixelColor(x, y)
            c.setAlpha(255)
            return c
        return None

    def _end_pick(self, color):
        cb, self._pick_cb, self._pick_img = self._pick_cb, None, None
        self.set_tool(self.tool)
        if cb:
            cb(color)

    def mousePressEvent(self, e):
        if self._pick_cb is not None:
            if e.button() == Qt.MouseButton.LeftButton:
                c = self._pick_color_at(e.position())
                if c is not None:
                    self._end_pick(c)
            else:
                self._end_pick(None)
            return
        if e.button() == Qt.MouseButton.MiddleButton:
            self._pan = e.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        tool = self.tool
        if tool == "select" or e.button() != Qt.MouseButton.LeftButton:
            if e.button() == Qt.MouseButton.RightButton and tool != "select":
                self.editor.set_tool("select")
                return
            super().mousePressEvent(e)
            return
        sp = self.mapToScene(e.position().toPoint())
        if self._handle_hit(sp) is not None:
            super().mousePressEvent(e)  # resize an existing object even in a draw tool
            return
        if tool == "crop":
            if self.crop is not None and self.crop.contains(self.crop.mapFromScene(sp)) \
                    and self.crop._norm().contains(self.crop.mapFromScene(sp)):
                super().mousePressEvent(e)  # move the crop frame
                return
            self.cancel_crop()
            self.crop = CropFrame()
            self.doc.scene.addItem(self.crop)
            self.crop.begin(sp)
            self._drawing = self.crop
            return
        if tool == "text":
            hit = [it for it in self.doc.scene.items(sp) if isinstance(it, I.TextItem)]
            editing = self.editing_text()
            if hit and hit[0].is_editing():
                super().mousePressEvent(e)  # place the caret inside the text
                return
            if editing:
                # first click outside only finishes the current text
                self.doc.scene.focusItem().clearFocus()
                return
            if hit:
                self.doc.scene.clearSelection()
                hit[0].setSelected(True)
                hit[0].start_edit()
                return
            self.doc.scene.clearSelection()
            item = self.editor.new_item("text")
            item.setPos(sp)
            item.pending_add = True
            item.setZValue(self.doc.next_z())
            self.doc.scene.addItem(item)
            item.setSelected(True)
            item.start_edit()
            return
        self.doc.scene.clearSelection()
        item = self.editor.new_item(tool)
        if item is None:
            return
        item.setZValue(self.doc.next_z())
        if tool == "step":
            item.number = self.doc.next_step_number()
        self.doc.scene.addItem(item)
        item.begin(sp)
        if tool == "step":
            self._commit(item)
            return
        self._drawing = item

    def mouseMoveEvent(self, e):
        if self._pick_cb is not None:
            c = self._pick_color_at(e.position())
            if c is not None:
                self.editor.status(f"Color under cursor: {c.name()}  "
                                   f"(rgb {c.red()}, {c.green()}, {c.blue()}) · click to pick")
            return
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - d.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - d.y()))
            return
        if self._drawing is not None:
            sp = self.mapToScene(e.position().toPoint())
            self._drawing.drag_to(sp, bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.MiddleButton and self._pan is not None:
            self._pan = None
            self.set_tool(self.tool)
            return
        if self._drawing is not None and e.button() == Qt.MouseButton.LeftButton:
            item, self._drawing = self._drawing, None
            if item is self.crop:
                if not item.finish():
                    self.cancel_crop()
                else:
                    item.setSelected(True)
                    self.editor.status("Crop: adjust the frame, then Enter (or double-click) "
                                       "to apply · Esc to cancel")
                return
            if item.finish():
                self._commit(item)
            else:
                self.doc.scene.removeItem(item)
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self.tool == "crop" and self.crop is not None:
            self.apply_crop()
            return
        super().mouseDoubleClickEvent(e)

    def _commit(self, item):
        self.doc.undo.push(AddCommand(self.doc.scene, [item], True, "Add"))
        self.doc.scene.clearSelection()
        item.setSelected(True)
        self.editor.after_draw(self.tool)

    def _cancel_drawing(self):
        item, self._drawing = self._drawing, None
        if item is not None and item is not self.crop and item.scene() is not None:
            self.doc.scene.removeItem(item)
        if item is self.crop:
            self.cancel_crop()

    def cancel_crop(self):
        if self.crop is not None:
            if self.crop.scene() is not None:
                self.doc.scene.removeItem(self.crop)
            self.crop = None

    def apply_crop(self):
        if self.crop is None:
            return
        r = self.crop.mapRectToScene(self.crop._norm())
        self.cancel_crop()
        if r.width() >= 2 and r.height() >= 2:
            self.doc.undo.push(CropCommand(self.doc, r))
        self.editor.set_tool("select")
        self.fit()

    # -- keyboard ---------------------------------------------------------------
    def editing_text(self):
        f = self.doc.scene.focusItem()
        return isinstance(f, I.TextItem) and f.is_editing()

    def keyPressEvent(self, e):
        if self.editing_text():
            super().keyPressEvent(e)
            return
        k = e.key()
        if k == Qt.Key.Key_Escape and self._pick_cb is not None:
            self._end_pick(None)
            return
        if k == Qt.Key.Key_Escape:
            if self._drawing is not None:
                self._cancel_drawing()
            elif self.crop is not None:
                self.cancel_crop()
                self.editor.set_tool("select")
            elif self.doc.scene.selectedItems():
                self.doc.scene.clearSelection()
            else:
                self.editor.set_tool("select")
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.crop is not None:
                self.apply_crop()
                return
            sel = self.doc.scene.selectedItems()
            if len(sel) == 1 and isinstance(sel[0], I.TextItem):
                sel[0].start_edit()
                return
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                  Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if k in arrows:
            sel = [it for it in self.doc.scene.selectedItems() if is_annot(it)]
            if sel:
                step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
                dx, dy = arrows[k]
                self.doc.undo.beginMacro("Nudge")
                for it in sel:
                    b = it.get_state()
                    it.moveBy(dx * step, dy * step)
                    self.doc.undo.push(StateCommand(it, b, it.get_state(), "Nudge"))
                self.doc.undo.endMacro()
                return
        super().keyPressEvent(e)

    # -- zoom ---------------------------------------------------------------
    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.set_zoom(self.zoom() * f)
            return
        super().wheelEvent(e)

    def zoom(self):
        return self.transform().m11()

    def set_zoom(self, z):
        z = max(0.05, min(16.0, z))
        f = z / self.zoom()
        self.scale(f, f)
        self.zoomChanged.emit(self.zoom())
        self.doc.scene.update()

    def fit(self):
        r = self.doc.scene.sceneRect()
        vp = self.viewport().rect().adjusted(10, 10, -10, -10)
        if r.isEmpty() or vp.width() <= 0:
            return
        z = min(1.0, vp.width() / r.width(), vp.height() / r.height())
        self.resetTransform()
        self.scale(z, z)
        self.zoomChanged.emit(self.zoom())

    def showEvent(self, e):
        super().showEvent(e)
        if not self._fitted_once:
            self._fitted_once = True
            QTimer.singleShot(0, self.fit)


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
class Document:
    _counter = 0

    def __init__(self, image, editor, title=None, path=None):
        Document._counter += 1
        self.created = time.time()
        self.title = title or time.strftime("%H:%M:%S")
        self.path = path
        self.uid = uuid.uuid4().hex
        self.restored_dirty = False  # restored from session with unsaved work
        self.project_path = None
        self.background = QImage(image).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        self.scene = AnnotScene(self)
        self.undo = QUndoStack()
        self.bg_item = QGraphicsPixmapItem()
        self.bg_item.setZValue(BG_Z)
        self.bg_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene.addItem(self.bg_item)
        self.set_background(self.background)
        self.view = AnnotView(self, editor)
        self.paste_offset = 0

    def set_background(self, img):
        self.background = img
        self.bg_item.setPixmap(QPixmap.fromImage(img))
        self.scene.setSceneRect(QRectF(img.rect()))
        self.scene.update()

    def annotations(self):
        return [it for it in self.scene.items() if is_annot(it) and it.parentItem() is None]

    def next_z(self):
        zs = [it.zValue() for it in self.annotations()]
        return (max(zs) + 1) if zs else 1

    def next_step_number(self):
        nums = [it.number for it in self.annotations() if isinstance(it, I.StepItem)]
        return (max(nums) + 1) if nums else 1

    def is_modified(self):
        return self.restored_dirty or not self.undo.isClean()

    def mark_saved(self):
        self.restored_dirty = False
        self.undo.setClean()

    def load_states(self, states):
        """Add annotation objects (no undo entry) - used for projects/session/duplicates."""
        for st in states:
            self.scene.addItem(I.make_item(st))

    def render_image(self):
        """Flatten screenshot + annotations into a QImage (no selection handles)."""
        sc = self.scene
        selected = sc.selectedItems()
        focus = sc.focusItem()
        for it in selected:
            it.setSelected(False)
        crop_vis = None
        if self.view.crop is not None:
            crop_vis = self.view.crop.isVisible()
            self.view.crop.setVisible(False)
        if focus is not None and isinstance(focus, I.TextItem) and focus.is_editing():
            focus.clearFocus()
        bg = self.background
        img = QImage(bg.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHints(QPainter.RenderHint.Antialiasing |
                         QPainter.RenderHint.SmoothPixmapTransform |
                         QPainter.RenderHint.TextAntialiasing)
        old_brush = sc.backgroundBrush()
        sc.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
        sc.render(p, QRectF(img.rect()), QRectF(bg.rect()))
        sc.setBackgroundBrush(old_brush)
        p.end()
        for it in selected:
            if it.scene() is sc:
                it.setSelected(True)
        if crop_vis is not None and self.view.crop is not None:
            self.view.crop.setVisible(crop_vis)
        return img
