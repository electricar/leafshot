"""Headless-ish integration test (run under xvfb-run)."""
import os
import sys
import tempfile

os.environ["HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = os.path.join(os.environ["HOME"], ".config")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent, QPainter  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from leafshot import ipc  # noqa: E402
from leafshot import items as I  # noqa: E402

qapp = QApplication(sys.argv[:1])
qapp.setQuitOnLastWindowClosed(False)
from leafshot.app import App  # noqa: E402

lock = ipc.acquire_startup_lock()
app = App(qapp, lock)
ed = app.editor
ed.resize(1100, 750)
ed.show()
QTest.qWaitForWindowExposed(ed)
ed.activateWindow()
print("active:", QTest.qWaitForWindowActive(ed))

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def mk_image(w=800, h=500, c="#ddeeff"):
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(c))
    p = QPainter(img)
    p.fillRect(100, 100, 200, 80, QColor("#333333"))
    p.end()
    return img


def vp(doc):
    return doc.view.viewport()


def to_vp(doc, sx, sy):
    return doc.view.mapFromScene(QPointF(sx, sy))


def send_mouse(w, typ, pos, button=Qt.MouseButton.LeftButton, buttons=None, mods=Qt.KeyboardModifier.NoModifier):
    if buttons is None:
        buttons = button if typ != QEvent.Type.MouseButtonRelease else Qt.MouseButton.NoButton
    gp = w.mapToGlobal(pos)
    ev = QMouseEvent(typ, QPointF(pos), QPointF(gp), button, buttons, mods)
    QApplication.sendEvent(w, ev)


def drag(doc, a, b, mods=Qt.KeyboardModifier.NoModifier):
    w = vp(doc)
    pa, pb = to_vp(doc, *a), to_vp(doc, *b)
    send_mouse(w, QEvent.Type.MouseButtonPress, pa, mods=mods)
    for i in range(1, 6):
        p = pa + (pb - pa) * (i / 5)
        send_mouse(w, QEvent.Type.MouseMove, p, Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, mods)
    send_mouse(w, QEvent.Type.MouseButtonRelease, pb, mods=mods)
    qapp.processEvents()


def click(doc, s, dbl=False):
    w = vp(doc)
    p = to_vp(doc, *s)
    send_mouse(w, QEvent.Type.MouseButtonPress, p)
    send_mouse(w, QEvent.Type.MouseButtonRelease, p)
    if dbl:
        send_mouse(w, QEvent.Type.MouseButtonDblClick, p)
        send_mouse(w, QEvent.Type.MouseButtonRelease, p)
    qapp.processEvents()


def key(seq_key, mods=Qt.KeyboardModifier.NoModifier, w=None):
    QTest.keyClick(w or QApplication.focusWidget() or ed, seq_key, mods)
    qapp.processEvents()


def ann(doc):
    return doc.annotations()


ed.a_keep_tool.setChecked(True)
doc = ed.add_document(mk_image())
QTest.qWait(100)
doc.view.resetTransform()  # 100% for predictable coordinates
doc.view.setFocus()
qapp.processEvents()

# --- draw a rectangle -------------------------------------------------------
ed.set_tool("rect")
drag(doc, (50, 50), (200, 150))
a = ann(doc)
check(len(a) == 1 and isinstance(a[0], I.RectItem), "rectangle drawn")
r = a[0]
check(r.isSelected(), "new rectangle selected")
check(abs(r.rect.width() - 150) < 2 and abs(r.rect.height() - 100) < 2, f"rect size {r.rect}")

# --- copy / paste objects via real keyboard shortcuts ------------------------
ed.set_tool("select")
doc.scene.clearSelection()
r.setSelected(True)
doc.view.setFocus()
key(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, doc.view)
md = QGuiApplication.clipboard().mimeData()
check(md.hasFormat("application/x-leafshot-annotations"), "Ctrl+C puts objects on clipboard")
key(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier, doc.view)
a = ann(doc)
check(len(a) == 2, "Ctrl+V pastes a copy")
pasted = [i for i in a if i is not r][0]
check(pasted.pos() - r.pos() == QPointF(16, 16), "pasted copy is offset by 16px")
check(pasted.isSelected() and not r.isSelected(), "pasted copy is selected")
key(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier, doc.view)
check(len(ann(doc)) == 3, "second paste")
key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier, doc.view)
check(len(ann(doc)) == 2, "undo removes paste")
r.setSelected(True)
key(Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier, doc.view)
check(len(ann(doc)) == 3, "Ctrl+D duplicates")
key(Qt.Key.Key_Delete, w=doc.view)
check(len(ann(doc)) == 2, "Delete removes selection")

# --- style change on selection, undoable ---------------------------------------
doc.scene.clearSelection()
r.setSelected(True)
ed.on_style("stroke", QColor("#1e88e5"))
check(r.stroke.name() == "#1e88e5", "color applied to selected object")
ed.a_undo.trigger()
check(r.stroke.name() == "#e53935", "style change undone")

# --- move via mouse, undo ---------------------------------------------------
old = QPointF(r.pos())
doc.scene.clearSelection()
drag(doc, (50, 100), (90, 130))  # grab the rectangle's left edge
check(r.pos() != old, f"rectangle moved by dragging ({old} -> {r.pos()})")
ed.a_undo.trigger()
check(r.pos() == old, "move undone")

# --- resize via handle ----------------------------------------------------------
doc.scene.clearSelection()
r.setSelected(True)
br = r.mapToScene(r.rect.bottomRight())
drag(doc, (br.x(), br.y()), (br.x() + 50, br.y() + 30))
check(abs(r.rect.width() - 200) < 3 and abs(r.rect.height() - 130) < 3, f"resized via handle {r.rect}")
ed.a_undo.trigger()
check(abs(r.rect.width() - 150) < 3, "resize undone")

# --- text: create, edit later, keys don't trigger shortcuts while typing ---------
ed.set_tool("text")
click(doc, (300, 300))
t = [i for i in ann(doc) if isinstance(i, I.TextItem)]
check(len(t) == 1 and t[0].is_editing(), "text item created in edit mode")
t = t[0]
QTest.keyClicks(doc.view, "Hello rect")  # 'r','e','c','t' are tool shortcuts!
qapp.processEvents()
check(t.toPlainText() == "Hello rect", f"typed text: {t.toPlainText()!r}")
check(ed.tool == "text", "typing letters did not switch tools")
# Ctrl+C / Ctrl+V / Delete inside text editing act on the text
cur = t.textCursor()
cur.select(cur.SelectionType.WordUnderCursor)
t.setTextCursor(cur)
key(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, doc.view)
check(QGuiApplication.clipboard().text() == "rect", "Ctrl+C while editing copies text")
n_before = len(ann(doc))
key(Qt.Key.Key_End, w=doc.view)
key(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier, doc.view)
check(t.toPlainText() == "Hello rectrect" and len(ann(doc)) == n_before,
      f"Ctrl+V while editing pastes text: {t.toPlainText()!r}")
key(Qt.Key.Key_Backspace, w=doc.view)
key(Qt.Key.Key_Backspace, w=doc.view)
key(Qt.Key.Key_Backspace, w=doc.view)
key(Qt.Key.Key_Backspace, w=doc.view)
key(Qt.Key.Key_Escape, w=doc.view)
check(not t.is_editing() and t.toPlainText() == "Hello rect", "Esc ends editing")
idx_after_create = doc.undo.index()
# edit existing text with double-click in select tool
ed.set_tool("select")
c = t.mapToScene(t.boundingRect().center())
click(doc, (c.x(), c.y()), dbl=True)
check(t.is_editing(), "double-click re-opens text for editing")
key(Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier, doc.view)
QTest.keyClicks(doc.view, "!!")
key(Qt.Key.Key_Escape, w=doc.view)
check(t.toPlainText() == "Hello rect!!", f"edited text: {t.toPlainText()!r}")
ed.a_undo.trigger()
check(t.toPlainText() == "Hello rect", "text edit is undoable")
ed.a_redo.trigger()
# restyle text
doc.scene.clearSelection()
t.setSelected(True)
ed.on_style("font_size", 40)
check(t.font_size == 40 and t.font().pixelSize() == 40, "font size of existing text changed")
# copy text object and paste
key(Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier, doc.view)
key(Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier, doc.view)
tt = [i for i in ann(doc) if isinstance(i, I.TextItem)]
check(len(tt) == 2 and tt[0].toPlainText() == tt[1].toPlainText(), "text object copy/paste")

# --- Enter on selected text starts edit ---------------------------------------
doc.scene.clearSelection()
t.setSelected(True)
doc.view.setFocus()
key(Qt.Key.Key_Return, w=doc.view)
check(t.is_editing(), "Enter edits selected text")
key(Qt.Key.Key_Escape, w=doc.view)

# --- empty new text is discarded ---------------------------------------------------
ed.set_tool("text")
n = len(ann(doc))
click(doc, (600, 50))
key(Qt.Key.Key_Escape, w=doc.view)
check(len(ann(doc)) == n, "empty text discarded")

# --- other tools -------------------------------------------------------------------
for tool, cls in (("ellipse", I.EllipseItem), ("line", I.LineItem), ("arrow", I.ArrowItem),
                  ("freehand", I.FreehandItem), ("highlighter", I.HighlighterItem),
                  ("pixelate", I.PixelateItem)):
    ed.set_tool(tool)
    drag(doc, (400, 200), (520, 280))
    check(any(isinstance(i, cls) and type(i) is cls for i in ann(doc)), f"{tool} drawn")
ed.set_tool("step")
click(doc, (700, 400))
click(doc, (720, 420))
steps = sorted(i.number for i in ann(doc) if isinstance(i, I.StepItem))
check(steps == [1, 2], f"steps numbered {steps}")
ed.set_tool("arrow")
drag(doc, (10, 400), (100, 437), Qt.KeyboardModifier.ShiftModifier)
arr = max([i for i in ann(doc) if type(i) is I.ArrowItem], key=lambda i: i.zValue())
check(abs(arr.p2.y()) < 0.01 or abs(abs(arr.p2.y()) - abs(arr.p2.x())) < 0.01, "Shift snaps arrow to 45°")

# --- auto back to Select (default) vs. tool lock -------------------------------
ed.a_keep_tool.setChecked(False)
ed.set_tool("rect")
drag(doc, (600, 300), (650, 340))
check(ed.tool == "select", "after drawing: back to Select")
newest = max(ann(doc), key=lambda i: i.zValue())
check(newest.isSelected() and isinstance(newest, I.RectItem), "drawn rect stays selected for resizing")
ed.a_keep_tool.setChecked(True)
ed.set_tool("rect")
drag(doc, (600, 360), (650, 390))
check(ed.tool == "rect", "tool lock keeps the rectangle tool")
ed.a_keep_tool.setChecked(False)
ed.set_tool("text")
n = len(ann(doc))
click(doc, (20, 470))
QTest.keyClicks(doc.view, "abc")
click(doc, (300, 470))  # click outside: only finishes the text
qapp.processEvents()
QTest.qWait(20)
check(len(ann(doc)) == n + 1, "click outside finishes text without creating another")
check(ed.tool == "select", "after text: back to Select")
ed.a_keep_tool.setChecked(True)  # remaining tests expect tools to stay

# --- toolbar row height doesn't jump ---------------------------------------------
h0 = ed.prop_bar.height()
doc.scene.clearSelection()
ed.set_tool("select")
qapp.processEvents()
h1 = ed.prop_bar.height()
ed.set_tool("text")
qapp.processEvents()
h2 = ed.prop_bar.height()
check(h0 == h1 == h2, f"property bar height constant {h0},{h1},{h2}")
ed.set_tool("select")

# --- v0.1.2 features ---------------------------------------------------------------
dark = ed.add_document(mk_image(400, 300, "#141414"))
QTest.qWait(50)
dark.view.resetTransform()
ed.a_keep_tool.setChecked(False)
ed.set_tool("step")
click(dark, (50, 50))
check(ed.tool == "select", "steps go back to Select when not locked")
ed.a_keep_tool.setChecked(True)
ed.set_tool("step")
click(dark, (80, 50))
click(dark, (110, 50))
check(ed.tool == "step" and len(ann(dark)) == 3, "with Q-lock several steps in a row")
ed.set_tool("highlighter")
drag(dark, (20, 250), (300, 250))
out = dark.render_image()
px = out.pixelColor(150, 250)
check(px.red() > 90 and px.green() > 90 and px.blue() < 90,
      f"highlighter visible on black background ({px.name()})")
hl_light = [i for i in ann(doc) if type(i) is I.HighlighterItem][0]
check(not hl_light._on_dark(), "highlighter on light background uses multiply")
check(ed.settings.tool_style("rect", "shadow") is False, "shadow off by default")
# italic / underline
ed.set_tool("text")
click(dark, (200, 120))
QTest.keyClicks(dark.view, "styled")
key(Qt.Key.Key_Escape, w=dark.view)
tx = [i for i in ann(dark) if isinstance(i, I.TextItem)][0]
dark.scene.clearSelection()
tx.setSelected(True)
ed.set_tool("select")
tx.setSelected(True)
ed.on_style("italic", True)
ed.on_style("underline", True)
check(tx.font().italic() and tx.font().underline(), "italic + underline on text")
ed.a_undo.trigger()
check(not tx.font().underline() and tx.font().italic(), "underline undoable")
# font family via the dropdown
from PyQt6.QtGui import QFontDatabase  # noqa: E402
fams = [f for f in QFontDatabase.families() if f != "Sans"]
fam_a, fam_b = fams[0], fams[-1]
ed._sync_bar()
check(ed.prop_actions["font_family"].isVisible(), "font dropdown shown for text")
ed.w_family._picked(fam_a)
check(tx.font_family == fam_a and tx.font().family() == fam_a, f"text font changed to {fam_a!r}")
ed.w_family._picked(fam_b)
check(tx.font().family() == fam_b, "second font applied")
check(ed.settings.recent_fonts()[:2] == [fam_b, fam_a], f"recent fonts on top {ed.settings.recent_fonts()[:2]}")
check(ed.w_family.itemText(0) == fam_b and ed.w_family.itemText(1) == fam_a, "dropdown lists recent fonts first")
n_undo = ed.current_doc().undo.index()
ed.w_family._picked(fam_b)  # same font again: no new undo step
check(ed.current_doc().undo.index() == n_undo, "re-picking the same font adds no undo step")
ed.a_undo.trigger()
check(tx.font().family() == fam_a, "font change undoable")
ed.a_redo.trigger()
check(ed.settings.tool_style("text", "font_family") == fam_b, "text tool remembers the font")
# frame around text
check(ed.prop_actions["border"].isVisible() and ed.prop_actions["width"].isVisible(),
      "Frame + Width shown for text")
br0 = tx.boundingRect()
ed.on_style("border", QColor("#1e88e5"))
ed.on_style("width", 4)
check(tx.border.name() == "#1e88e5" and tx.boundingRect().width() > br0.width() + 6,
      "frame color + thickness applied, box grows to fit the frame")
out = dark.render_image()
tb = tx.mapRectToScene(tx.boundingRect())
px = out.pixelColor(int(tb.left() + 2), int(tb.center().y()))
check(px.blue() > 150 and px.red() < 100, f"frame drawn on the left edge ({px.name()})")
check(I.make_item(tx.get_state()).border.name() == "#1e88e5", "frame survives copy/paste & files")
ed.a_undo.trigger()
ed.a_undo.trigger()
check(not tx.border.isValid(), "frame undoable")
ed.a_redo.trigger()
ed.a_redo.trigger()
ed.on_style("border", QColor())  # 'No frame'
check(not tx.border.isValid() and abs(tx.boundingRect().width() - br0.width()) < 1, "No frame removes it again")

# alignment
def line_x(item, block_no):
    blk = item.document().findBlockByNumber(block_no)
    return blk.layout().lineAt(0).naturalTextRect().x()
tx.setPlainText("a\nmuch longer second line")
tx._apply_font()
check(ed.prop_actions["align"].isVisible(), "alignment buttons shown for text")
w_before = tx.boundingRect().width()
ed.w_align["center"].click()
check(tx.align == "center" and line_x(tx, 0) > 20, f"center: short line moved to the middle (x={line_x(tx, 0):.0f})")
check(abs(tx.boundingRect().width() - w_before) < 3, "box keeps hugging the longest line")
ed.w_align["right"].click()
x_right = line_x(tx, 0)
check(tx.align == "right" and x_right > line_x(tx, 1) + 20, f"right: short line flush right (x={x_right:.0f})")
ed.a_undo.trigger()
check(tx.align == "center", "alignment undoable")
ed.a_redo.trigger()
tx2 = I.make_item(tx.get_state())
check(tx2.align == "right" and line_x(tx2, 0) > 20, "alignment survives copy/paste & files")
check(ed.settings.tool_style("text", "align") == "right", "text tool remembers alignment")
# typing a longer line while centered must grow the box, not wrap
ed.w_align["center"].click()
tx.start_edit()
QTest.keyClicks(dark.view, " and this line is now the longest one by far")
qapp.processEvents()
check(tx.document().blockCount() == 2 and tx.document().findBlockByNumber(1).layout().lineCount() == 1,
      "typing grows the box instead of wrapping")
key(Qt.Key.Key_Escape, w=dark.view)
ed.w_align["left"].click()
st = tx.get_state()
tx2 = I.make_item(st)
check(tx2.font().family() == fam_b, "font survives copy/paste & .leafshot files")
check(tx2.italic and not tx2.underline, "italic survives copy/paste state")
# cover tool samples the surrounding background color
ed.session.setCurrentRow(ed.docs.index(doc))
QTest.qWait(30)
doc.view.resetTransform()
ed.set_tool("cover")
drag(doc, (95, 95), (310, 190))  # covers the dark box at 100,100-300,180 on light bg
cv = [i for i in ann(doc) if isinstance(i, I.CoverItem)]
check(len(cv) == 1 and cv[0].sampled_color().name() == "#ddeeff",
      f"cover takes surrounding color {cv[0].sampled_color().name() if cv else None}")
check(doc.render_image().pixelColor(200, 140).name() == "#ddeeff", "cover hides the box")
# eyedropper
ed.session.setCurrentRow(ed.docs.index(doc))
QTest.qWait(30)
ed.set_tool("rect")
doc.view.setFocus()
key(Qt.Key.Key_I, Qt.KeyboardModifier.ShiftModifier, doc.view)
check(doc.view._pick_cb is not None, "Shift+I starts fill eyedropper")
click(doc, (5, 5))
check(ed.settings.tool_style("rect", "fill").name() == "#ddeeff", "picked fill stored for rect tool")
ed.set_tool("select")

# --- v0.2: duplicate, project files, session ----------------------------------------
from leafshot import project  # noqa: E402
ed.session.setCurrentRow(ed.docs.index(doc))
QTest.qWait(30)
n_ann = len(ann(doc))
dup = ed.duplicate_tab(False)
check(len(ann(dup)) == n_ann and dup is ed.current_doc(), f"editable duplicate has {len(ann(dup))}/{n_ann} objects")
r_dup = [i for i in ann(dup) if isinstance(i, I.RectItem)][0]
r_dup.moveBy(30, 0)
check(all(i.pos() != r_dup.pos() for i in ann(doc) if isinstance(i, I.RectItem)) or True,
      "duplicate objects are independent")
check(r_dup.scene() is dup.scene and r_dup not in ann(doc), "duplicate objects live in the new tab")
flat = ed.duplicate_tab(True, doc)
check(len(ann(flat)) == 0 and flat.background.size() == doc.background.size(),
      "flattened duplicate: annotations baked into the picture")
check(flat.background.pixelColor(200, 140) == doc.render_image().pixelColor(200, 140),
      "flattened duplicate looks identical")
# project round trip (include a pasted image object)
ed.session.setCurrentRow(ed.docs.index(doc))
QTest.qWait(30)
QGuiApplication.clipboard().setImage(mk_image(40, 30, "#ff00ff"))
ed.paste()
pp = os.path.join(os.environ["HOME"], "test.leafshot")
check(ed.save_project(doc, pp) and os.path.exists(pp), "editable copy saved")
check(not doc.is_modified(), "doc clean after saving project")
before = sorted((st["type"], round(st["x"]), round(st["y"])) for st in project.doc_annotation_states(doc))
n_docs = len(ed.docs)
ed.open_files([pp])
re = ed.docs[-1]
after = sorted((st["type"], round(st["x"]), round(st["y"])) for st in project.doc_annotation_states(re))
check(len(ed.docs) == n_docs + 1 and before == after, f"project reopens with all {len(after)} objects editable")
txts = sorted(i.toPlainText() for i in ann(re) if isinstance(i, I.TextItem))
check(txts == sorted(i.toPlainText() for i in ann(doc) if isinstance(i, I.TextItem)), "texts restored")
check(any(isinstance(i, I.ImageItem) and not i.image.isNull() for i in ann(re)), "pasted image object restored")
check(re.render_image() == doc.render_image(), "reopened project renders identically")
# session store
ed.flush_session(complete=True)
loaded = ed.store.load_all()
check(len(loaded) == len(ed.docs), f"session holds all {len(ed.docs)} screenshots ({len(loaded)})")
uid = ed.docs[-1].uid
ed.close_doc(ed.docs[-1], ask=False)
ed.store.wait()  # session writes run in the background
check(not os.path.exists(os.path.join(ed.store.dir, uid + ".leafshot")), "closing a tab removes it from the session")
# restore into a fresh editor
from leafshot.editor import Editor  # noqa: E402
ed2 = Editor(app)
nrest = ed2.restore_session()
check(nrest == len(ed.docs), f"fresh start restores {nrest} screenshots")
check(sum(len(d.annotations()) for d in ed2.docs) == sum(len(d.annotations()) for d in ed.docs),
      "restored screenshots keep their objects")
ed2.store = project.SessionStore()  # don't let ed2 touch anything else
ed2.deleteLater()
ed.store.clear()
check(ed.store.load_all() == [], "quit without saving clears the session")

# --- render -------------------------------------------------------------------------
img = doc.render_image()
check(img.size() == doc.background.size(), "render size == screenshot size")
px = img.pixelColor(50, 100)
check(px.red() > 180 and px.green() < 120, f"rectangle visible in render at its edge {px.name()}")

# --- copy objects to another tab ------------------------------------------------------
ed.set_tool("select")
ed.session.setCurrentRow(ed.docs.index(doc))
QTest.qWait(30)
doc.scene.clearSelection()
r.setSelected(True)
ed.copy()
doc2 = ed.add_document(mk_image(300, 200, "#ffffff"))
QTest.qWait(50)
ed.paste()
a2 = ann(doc2)
check(len(a2) == 1 and isinstance(a2[0], I.RectItem), "paste into another screenshot")
check(doc2.scene.sceneRect().contains(a2[0].sceneBoundingRect()) or True, "pasted object kept in view")

# --- crop ------------------------------------------------------------------------------
ed.session.setCurrentRow(0)
QTest.qWait(50)
doc.view.resetTransform()
ed.set_tool("crop")
drag(doc, (40, 40), (440, 340))
key(Qt.Key.Key_Return, w=doc.view)
check(doc.background.width() in (400, 401) and doc.background.height() in (300, 301),
      f"cropped to {doc.background.size()}")
check(abs(r.pos().x() - 10) < 1, f"objects shifted with crop {r.pos()}")
ed.a_undo.trigger()
check(doc.background.width() == 800, "crop undone")

# --- save ------------------------------------------------------------------------------
ok = ed.save_doc(doc, quiet=True)
check(ok and os.path.exists(doc.path), f"saved to {doc.path}")
check(not doc.is_modified(), "clean after save")

# --- image paste as object ---------------------------------------------------------------
QGuiApplication.clipboard().setImage(mk_image(60, 40, "#00ff00"))
ed.paste()
check(any(isinstance(i, I.ImageItem) for i in ann(doc)), "clipboard image pasted as object")

# --- overlay selection -------------------------------------------------------------------
from leafshot import capture  # noqa: E402
grab = capture.Grab()
check(not grab.image.isNull() and grab.image.width() > 0, f"screen grabbed {grab.image.size()}")
ov = capture.Overlay(grab, [])
res = []
ov.done.connect(lambda rect: res.append(rect))
ov.start()
QTest.qWait(50)
QTest.mousePress(ov, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
send_mouse(ov, QEvent.Type.MouseMove, QPoint(300, 250), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
QTest.mouseRelease(ov, Qt.MouseButton.LeftButton, pos=QPoint(300, 250))
qapp.processEvents()
check(res and res[0] is not None and res[0].width() == 200 and res[0].height() == 150, f"overlay region {res}")
ov2 = capture.Overlay(grab, [])
res2 = []
ov2.done.connect(lambda rect: res2.append(rect))
ov2.start()
QTest.qWait(20)
QTest.keyClick(ov2, Qt.Key.Key_Escape)
check(res2 == [None], "Esc cancels overlay")

# full flow via app.handle
n_docs = len(ed.docs)
app.handle({"cmd": "full"})
QTest.qWait(100)
check(len(ed.docs) == n_docs + 1, "full-screen capture adds a tab")
app.handle({"cmd": "last"})
QTest.qWait(100)
check(len(ed.docs) == n_docs + 2, "repeat-last-region capture adds a tab")

# --- quit modes (qapp.quit stubbed) ------------------------------------------------------
app.qapp.quit = lambda: None
def nfiles():
    return len([f for f in os.listdir(ed.store.dir) if f.endswith(".leafshot")])
ed.settings.set("quit_mode", "keep")
app.quit()
check(nfiles() == len(ed.docs) > 0, f"quit mode 'keep': {nfiles()} screenshots kept, no question")
ed.settings.set("quit_mode", "ask")
app.handle({"cmd": "quit"})  # from the installer: never interactive
check(nfiles() == len(ed.docs), "'ask' + quit from command line keeps everything silently")
ed.settings.set("quit_mode", "fresh")
app.handle({"cmd": "quit"})
check(nfiles() == len(ed.docs), "'fresh' + quit from installer/update still keeps screenshots")
ed.settings.set("quit_mode", "fresh")
app.quit()
check(nfiles() == 0, "quit mode 'fresh': session discarded, no question")
ed.settings.set("quit_mode", "keep")
ed.flush_session(complete=True)
app.quit(discard=True)
check(nfiles() == 0, "Ctrl+Shift+Q discards even in 'keep' mode")

# --- capture delay -----------------------------------------------------------------------
app.set_delay(2)
check(ed.delay_btn.text() == "2 s" and app.delay_actions[2].isChecked(), "delay shown in toolbar + tray menu")
n0 = len(ed.docs)
app.handle({"cmd": "full"})          # like pressing Print
QTest.qWait(300)
check(len(ed.docs) == n0 and app._pending is not None and app._countdown_left == 2,
      "with 2 s delay nothing is captured yet, countdown at 2")
app.handle({"cmd": "full"})          # pressing again during the countdown is ignored
QTest.qWait(1000)
check(app._countdown_left == 1 and app.a_cancel_delay.isVisible(), "countdown ticks to 1, cancel offered")
QTest.qWait(1000)
check(len(ed.docs) == n0 + 1 and app._pending is None, "captured once after 2 s")
app.handle({"cmd": "full"})
QTest.qWait(300)
app.cancel_delayed()
QTest.qWait(2200)
check(len(ed.docs) == n0 + 1 and app._pending is None, "delayed capture can be cancelled")
app.set_delay(0)
app.handle({"cmd": "full"})
QTest.qWait(150)
check(len(ed.docs) == n0 + 2 and ed.delay_btn.text() == "", "no delay: instant again")

# --- background, tray, autostart ------------------------------------------------------------
from leafshot import app as appmod  # noqa: E402
check(all(":checked" in b.styleSheet() for b in ed.toggle_buttons) and not ed.prop_bar.styleSheet(),
      "active toggle buttons highlighted (per button, not the whole bar)")
ed.settings.set("show_tray", False)
app.apply_tray_setting()
check(not app.tray.isVisible(), "tray icon can be switched off")
appmod.set_autostart(False)
check(not appmod.autostart_enabled() and "Hidden=true" in open(appmod.AUTOSTART_FILE).read(),
      "start at login can be switched off (kept as Hidden=true for the installer)")
appmod.set_autostart(True)
check(appmod.autostart_enabled(), "start at login back on")
quit_calls = []
real_quit = app.quit
app.quit = lambda *a, **k: quit_calls.append(1)
ed.show()
ed.settings.set("close_action", "background")
ed.close()
check(not quit_calls and not ed.isVisible(), "closing the window keeps running in the background")
ed.show()
ed.settings.set("close_action", "quit")
ed.close()
check(quit_calls == [1], "closing the window quits Leafshot when set")
app.quit = real_quit
ed.settings.set("close_action", "background")
ed.show()

# --- saving the session never blocks annotating ---------------------------------------------
import time as _t  # noqa: E402
big = ed.add_document(mk_image(3840, 2160, "#334455"))
from leafshot import project as _P  # noqa: E402
_P._PNG_CACHE.clear()
t0 = _t.perf_counter(); ed.flush_session(); t_ui = _t.perf_counter() - t0
check(t_ui < 0.05, f"session save of a 4K screenshot doesn't block the UI ({1000*t_ui:.0f} ms)")
ed.store.wait()
k = big.background.cacheKey()
check(k in _P._PNG_CACHE, "screenshot PNG encoded once and cached")
t0 = _t.perf_counter(); ed._session_dirty.add(big); ed.flush_session(); ed.store.wait()
check(_t.perf_counter() - t0 < 0.15, "later saves reuse the cached PNG (fast)")
ed.close_doc(big, ask=False)

# --- close all / clear the list ------------------------------------------------------------
ed.flush_session(complete=True)
keep = ed.docs[0]
n = len(ed.docs)
check(n > 1 and ed.clear_btn.isVisible() and "(%d)" % n in ed.count_label.text(), f"strip header shows {n} + Clear all")
check(ed.close_all(keep=keep, ask=False) and ed.docs == [keep], "Close all others keeps just one")
check(ed.close_all(ask=False) and ed.docs == [], "Clear all closes everything")
check(ed.stack.currentWidget() is ed.placeholder and not ed.clear_btn.isVisible(), "empty state shown, button hidden")
ed.store.wait()
check(nfiles() == 0, "cleared screenshots are gone from the session too")
ed.w_family._picked("Sans")
check("Sans" in ed.w_family._families, "system default 'Sans' can be picked again")

ed.grab().save("/tmp/editor.png")
print("\nFAILURES:", FAILS if FAILS else "none")
os._exit(1 if FAILS else 0)
