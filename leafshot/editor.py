"""Editor window: session strip (one entry per screenshot), tools, property bar."""
import json
import os
import tempfile
import time

from PyQt6.QtCore import QByteArray, QMimeData, QPointF, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import (QAction, QActionGroup, QColor, QDrag, QFont, QFontDatabase,
                         QGuiApplication, QIcon, QImage,
                         QImageReader, QKeySequence, QUndoGroup)
from PyQt6.QtWidgets import (QComboBox, QCompleter, QColorDialog, QHBoxLayout, QVBoxLayout, QFileDialog, QGridLayout, QLabel,
                             QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                             QSpinBox, QSplitter, QStackedWidget, QToolBar, QToolButton,
                             QWidget, QWidgetAction)

from . import icons
from . import items as I
from . import project
from .document import AddCommand, Document, RemoveCommand, StateCommand, is_annot
from .ipc import APP_TITLE

MIME_ITEMS = "application/x-leafshot-annotations"

TOOLS = [
    # id, label, shortcut
    ("select", "Select / move", "V"),
    ("rect", "Rectangle", "R"),
    ("ellipse", "Ellipse", "E"),
    ("line", "Line", "L"),
    ("arrow", "Arrow", "A"),
    ("freehand", "Freehand", "F"),
    ("highlighter", "Highlighter", "H"),
    ("text", "Text", "T"),
    ("step", "Numbered step", "N"),
    ("pixelate", "Pixelate (hide)", "P"),
    ("cover", "Cover with background color (hide)", "B"),
    ("crop", "Crop", "C"),
]
TOOL_CLASSES = {
    "rect": I.RectItem, "ellipse": I.EllipseItem, "line": I.LineItem, "arrow": I.ArrowItem,
    "freehand": I.FreehandItem, "highlighter": I.HighlighterItem, "text": I.TextItem,
    "step": I.StepItem, "pixelate": I.PixelateItem, "cover": I.CoverItem,
}
ALL_KEYS = ("stroke", "fill", "border", "width", "font_family", "font_size", "bold", "italic", "underline",
            "align", "shadow",
            "pixel_size")
SWATCHES = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa",
            "#000000", "#616161", "#ffffff", "#fff176", "#80deea", "#f48fb1"]


# ---------------------------------------------------------------------------
class ColorButton(QToolButton):
    def __init__(self, label, allow_none, on_pick, parent=None, on_eyedropper=None,
                 eyedropper_key=None, none_label="No fill"):
        super().__init__(parent)
        self._color = QColor()
        self._on_pick = on_pick
        self.setToolTip(label)
        self.setText(label)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self)
        grid_w = QWidget()
        grid = QGridLayout(grid_w)
        grid.setContentsMargins(6, 6, 6, 6)
        grid.setSpacing(4)
        for i, hexc in enumerate(SWATCHES):
            b = QToolButton()
            b.setIcon(icons.color_icon(QColor(hexc)))
            b.setIconSize(QSize(22, 22))
            b.setAutoRaise(True)
            b.clicked.connect(lambda _=False, c=hexc: self._pick(QColor(c), menu))
            grid.addWidget(b, i // 6, i % 6)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(grid_w)
        menu.addAction(wa)
        if allow_none:
            menu.addAction(icons.color_icon(None), none_label,
                           lambda: self._pick(QColor(), menu))
        if on_eyedropper is not None:
            menu.addAction("Pick from screenshot…" + (f"  ({eyedropper_key})" if eyedropper_key
                                                      else ""), on_eyedropper)
        menu.addAction("Custom… (with transparency)", self._custom)
        self.setMenu(menu)
        self.set_color(QColor())

    def _pick(self, c, menu=None):
        if menu:
            menu.close()
        self.set_color(c)
        self._on_pick(c)

    def _custom(self):
        start = self._color if self._color.isValid() else QColor("#e53935")
        c = QColorDialog.getColor(start, self.window(), "Pick color",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self._pick(c)

    def set_color(self, c):
        self._color = QColor(c) if c is not None else QColor()
        self.setIcon(icons.color_icon(self._color))


class FontBox(QComboBox):
    """All installed fonts, your 5 most recent on top; type to search."""

    def __init__(self, settings, on_pick):
        super().__init__()
        self.settings, self._on_pick = settings, on_pick
        self._value = None
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(18)
        self.setFixedWidth(190)
        self.setToolTip("Font (type to search; recently used fonts are at the top)")
        self._families = sorted(set(QFontDatabase.families()) | {"Sans"}, key=str.lower)
        comp = QCompleter(self._families, self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(comp)
        self._fill()
        self.activated.connect(self._activated)
        comp.activated.connect(self._picked)
        self.lineEdit().editingFinished.connect(lambda: self._picked(self.currentText()))

    def _fill(self, current=None):
        self.blockSignals(True)
        self.clear()
        recent = [f for f in self.settings.recent_fonts() if f in self._families]
        for fam in recent:
            self.addItem(fam)
        if recent:
            self.insertSeparator(len(recent))
        for fam in self._families:
            self.addItem(fam)
        # preview every name in its own font
        for i in range(self.count()):
            t = self.itemText(i)
            if t:
                self.setItemData(i, QFont(t, 11), Qt.ItemDataRole.FontRole)
        if current:
            self.setCurrentText(current)
        self.blockSignals(False)

    def set_family(self, fam):
        self._value = fam
        self.blockSignals(True)
        i = self.findText(fam)
        if i >= 0:
            self.setCurrentIndex(i)
        else:
            self.setCurrentText(fam)
        self.blockSignals(False)

    def _activated(self, i):
        self._picked(self.itemText(i))

    def _picked(self, fam):
        fam = (fam or "").strip()
        if fam not in self._families or fam == self._value:
            return
        self._value = fam
        self.settings.add_recent_font(fam)
        self._fill(fam)
        self._on_pick(fam)


class SessionList(QListWidget):
    """Thumbnails of all screenshots in this session; drag one out to another app."""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.TopToBottom)
        self.setWrapping(False)
        self.setMovement(QListWidget.Movement.Static)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setIconSize(QSize(168, 104))
        self.setGridSize(QSize(184, 150))
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragOnly)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.setMinimumWidth(200)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setToolTip("Drag a screenshot into another app · right-click for more")

    def startDrag(self, actions):
        doc = self.editor.current_doc()
        if doc is None:
            return
        img = doc.render_image()
        path = self.editor.temp_png(doc, img)
        md = QMimeData()
        md.setImageData(img)
        md.setUrls([QUrl.fromLocalFile(path)])
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.setPixmap(self.currentItem().icon().pixmap(120, 80))
        drag.exec(Qt.DropAction.CopyAction)


# ---------------------------------------------------------------------------
class Editor(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = app.settings
        self.docs = []
        self.tool = "select"
        self._updating_bar = False
        self._thumb_timer = QTimer(self, singleShot=True, interval=350)
        self._thumb_timer.timeout.connect(self._refresh_thumbs)
        self._dirty_thumbs = set()
        self._tmpdir = tempfile.mkdtemp(prefix="leafshot-")
        self.store = project.SessionStore()
        self._session_timer = QTimer(self, singleShot=True, interval=1500)
        self._session_timer.timeout.connect(self.flush_session)
        self._session_dirty = set()

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(icons.app_icon())
        self.setAcceptDrops(True)
        self.undo_group = QUndoGroup(self)

        self.session = SessionList(self)
        self.session.currentRowChanged.connect(self._on_row)
        self.session.customContextMenuRequested.connect(self._session_menu)
        self.stack = QStackedWidget()
        self.placeholder = QLabel(
            "<div style='color:#888; font-size:15px'>No screenshots yet.<br><br>"
            "Press your screenshot hotkey (Print),<br>use the camera button above,<br>"
            "drop an image here, or Ctrl+Shift+V to paste one.</div>")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.placeholder)
        side = QWidget()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(2)
        head = QHBoxLayout()
        head.setContentsMargins(8, 4, 4, 0)
        self.count_label = QLabel()
        self.count_label.setTextFormat(Qt.TextFormat.PlainText)
        head.addWidget(self.count_label)
        self.clear_btn = QToolButton()
        head.addWidget(self.clear_btn)
        head.addStretch(1)
        sv.addLayout(head)
        sv.addWidget(self.session)
        split = QSplitter()
        split.addWidget(side)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setSizes([210, 1000])
        self.setCentralWidget(split)

        self._build_actions()
        self._build_toolbars()
        self._build_menus()
        self.zoom_label = QLabel()
        self.statusBar().addPermanentWidget(self.zoom_label)
        self.update_delay_ui()
        self.clear_btn.setDefaultAction(self.a_close_all)
        self.clear_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.clear_btn.setIconSize(QSize(16, 16))
        self.clear_btn.setAutoRaise(True)
        self.clear_btn.setToolTip("Clear all: close every screenshot  (Ctrl+Shift+W)")
        self._apply_icons()
        self.set_tool("select")
        self._update_enabled()

        geo = self.settings.get("window_geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1200, 780)

    # -- construction ---------------------------------------------------------
    def _act(self, text, slot, shortcut=None, icon=None, tip=None):
        a = QAction(text, self)
        if icon is not None:
            a.setIcon(icon)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                a.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                a.setShortcut(QKeySequence(shortcut))
        a.setToolTip(f"{tip or text}" + (f"  ({a.shortcut().toString()})" if shortcut else ""))
        a.triggered.connect(slot)
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        ap = self.app
        cam = icons.theme("camera-photo", icons.app_icon())
        self.a_cap_region = A("Capture region…", lambda: ap.capture("region", from_editor=True),
                              "Ctrl+N", cam, "New screenshot (region / click a window)")
        self.a_cap_window = A("Capture active window", lambda: ap.capture("window", from_editor=True))
        self.a_cap_screen = A("Capture current monitor", lambda: ap.capture("screen", from_editor=True))
        self.a_cap_full = A("Capture all monitors", lambda: ap.capture("full", from_editor=True))
        self.a_cap_last = A("Repeat last region", lambda: ap.capture("last", from_editor=True),
                            "Ctrl+Shift+N")
        self.a_open = A("Open image…", self.open_dialog, QKeySequence.StandardKey.Open,
                        icons.theme("document-open"))
        self.a_paste_new = A("Paste image as new tab", self.paste_as_new, "Ctrl+Shift+V")
        self.a_save = A("Save", self.save, QKeySequence.StandardKey.Save, None,
                        "Save to your screenshot folder")
        self.a_save_as = A("Save as…", self.save_as, "Ctrl+Shift+S",
                           icons.theme("document-save-as"))
        self.a_copy_img = A("Copy image", self.copy_image, "Ctrl+Shift+C", None,
                            "Copy the annotated image to the clipboard")
        self.a_close = A("Close tab", self.close_current, QKeySequence.StandardKey.Close)
        self.a_close_all = A("Close all screenshots", self.close_all, "Ctrl+Shift+W", None,
                             "Clear the list: close every open screenshot")
        self.a_save_proj = A("Save editable copy (.leafshot)…", self.save_project_dialog,
                             "Ctrl+Alt+S", None,
                             "Save screenshot + annotation objects to continue editing later")
        self.a_dup_tab = A("Duplicate screenshot (annotations stay editable)",
                           lambda: self.duplicate_tab(False), "Ctrl+Shift+D")
        self.a_dup_flat = A("Duplicate as flattened image",
                            lambda: self.duplicate_tab(True), "Ctrl+Shift+F", None,
                            "New tab where the current annotations are part of the picture")
        self.a_settings = A("Settings…", self.app.show_settings, None,
                            icons.theme("preferences-system"))
        self.a_quit = A("Quit Leafshot", self.app.quit, "Ctrl+Q")
        self.a_quit_discard = A("Quit without saving", lambda: self.app.quit(discard=True),
                                "Ctrl+Shift+Q", None,
                                "Quit immediately and throw away all open screenshots")

        self.a_undo = self.undo_group.createUndoAction(self, "Undo")
        self.a_undo.setShortcuts([QKeySequence(QKeySequence.StandardKey.Undo)])
        self.a_redo = self.undo_group.createRedoAction(self, "Redo")
        self.a_redo.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.addAction(self.a_undo)
        self.addAction(self.a_redo)

        self.a_copy = A("Copy", self.copy, QKeySequence.StandardKey.Copy, None,
                        "Copy selected annotations (or the whole image if nothing is selected)")
        self.a_cut = A("Cut", self.cut, QKeySequence.StandardKey.Cut)
        self.a_paste = A("Paste", self.paste, QKeySequence.StandardKey.Paste)
        self.a_dup = A("Duplicate", self.duplicate, "Ctrl+D")
        self.a_delete = A("Delete", self.delete_selected, QKeySequence.StandardKey.Delete)
        self.a_select_all = A("Select all annotations", self.select_all,
                              QKeySequence.StandardKey.SelectAll)
        self.a_front = A("Bring to front", lambda: self.restack(True), "Ctrl+Shift+Up")
        self.a_back = A("Send to back", lambda: self.restack(False), "Ctrl+Shift+Down")

        self.a_zoom_in = A("Zoom in", lambda: self._zoom(1.25), ["Ctrl++", "Ctrl+="],
                           icons.theme("zoom-in"))
        self.a_zoom_out = A("Zoom out", lambda: self._zoom(0.8), "Ctrl+-", icons.theme("zoom-out"))
        self.a_zoom_fit = A("Fit to window", self._fit, "Ctrl+0")
        self.a_zoom_100 = A("Actual size (100%)", lambda: self._zoom_abs(1.0), "Ctrl+1")
        self.a_next = A("Next screenshot", lambda: self._step_tab(1), ["Ctrl+Tab", "Ctrl+PgDown"])
        self.a_prev = A("Previous screenshot", lambda: self._step_tab(-1),
                        ["Ctrl+Shift+Tab", "Ctrl+PgUp"])

        self.tool_group = QActionGroup(self)
        self.tool_actions = {}
        for tid, label, key in TOOLS:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(key))
            a.setToolTip(f"{label}  ({key})")
            a.triggered.connect(lambda _=False, t=tid: self.set_tool(t))
            self.tool_group.addAction(a)
            self.addAction(a)
            self.tool_actions[tid] = a

        self.a_pick_stroke = A("Pick color from screenshot", lambda: self.start_pick("stroke"), "I")
        self.a_pick_fill = A("Pick fill color from screenshot", lambda: self.start_pick("fill"),
                             "Shift+I")

        self.a_keep_tool = QAction("Keep tool after drawing", self)
        self.a_keep_tool.setCheckable(True)
        self.a_keep_tool.setChecked(self.settings.get("keep_tool"))
        self.a_keep_tool.setShortcut(QKeySequence("Q"))
        self.a_keep_tool.toggled.connect(self._keep_tool_toggled)
        self.addAction(self.a_keep_tool)
        self._keep_tool_toggled(self.a_keep_tool.isChecked(), save=False)

    def _build_toolbars(self):
        tb = QToolBar("Main")
        tb.setObjectName("main")
        tb.setIconSize(QSize(22, 22))
        cap_btn = QToolButton()
        cap_btn.setDefaultAction(self.a_cap_region)
        cap_btn.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        m = QMenu(cap_btn)
        for a in (self.a_cap_region, self.a_cap_window, self.a_cap_screen, self.a_cap_full,
                  self.a_cap_last):
            m.addAction(a)
        cap_btn.setMenu(m)
        tb.addWidget(cap_btn)
        self.delay_btn = QToolButton()
        self.delay_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.delay_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        dmenu = QMenu(self.delay_btn)
        for sec in (0, 1, 2, 3, 5, 10):
            dmenu.addAction("No delay" if sec == 0 else f"{sec} seconds",
                            lambda s_=sec: self.app.set_delay(s_))
        self.delay_btn.setMenu(dmenu)
        tb.addWidget(self.delay_btn)
        tb.addSeparator()
        for a in (self.a_save, self.a_copy_img):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_undo)
        tb.addAction(self.a_redo)
        tb.addSeparator()
        tb.addAction(self.a_zoom_fit)
        tb.addAction(self.a_zoom_100)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

        # property bar
        pb = QToolBar("Properties")
        pb.setObjectName("props")
        self.prop_bar = pb
        self.prop_actions = {}
        self.w_stroke = ColorButton("Color", False, lambda c: self.on_style("stroke", c),
                                    on_eyedropper=lambda: self.start_pick("stroke"),
                                    eyedropper_key="I")
        self.prop_actions["stroke"] = pb.addWidget(self.w_stroke)
        self.w_fill = ColorButton("Fill", True, lambda c: self.on_style("fill", c),
                                  on_eyedropper=lambda: self.start_pick("fill"),
                                  eyedropper_key="Shift+I")
        self.prop_actions["fill"] = pb.addWidget(self.w_fill)
        self.w_border = ColorButton("Frame", True, lambda c: self.on_style("border", c),
                                    on_eyedropper=lambda: self.start_pick("border"),
                                    none_label="No frame")
        self.w_border.setToolTip("Frame around the text (thickness = Width)")
        self.prop_actions["border"] = pb.addWidget(self.w_border)

        def spin(label, lo, hi, key, suffix=""):
            lab = QLabel(f" {label} ")
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setSuffix(suffix)
            s.setKeyboardTracking(False)
            s.valueChanged.connect(lambda v: self.on_style(key, v))
            w = QWidget()
            from PyQt6.QtWidgets import QHBoxLayout
            lay = QHBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            lay.addWidget(lab)
            lay.addWidget(s)
            self.prop_actions[key] = pb.addWidget(w)
            return s

        self.w_width = spin("Width", 0, 40, "width")
        self.w_width.setToolTip("Line width (0 = no outline, e.g. for a plain filled box)")
        self.w_family = FontBox(self.settings, lambda f: self.on_style("font_family", f))
        self.prop_actions["font_family"] = pb.addWidget(self.w_family)
        self.w_font = spin("Size", 6, 300, "font_size", " px")
        self.w_bold = QToolButton()
        self.w_bold.setText("B")
        self.w_bold.setCheckable(True)
        self.w_bold.setToolTip("Bold text")
        f = self.w_bold.font()
        f.setBold(True)
        self.w_bold.setFont(f)
        self.w_bold.toggled.connect(lambda v: self.on_style("bold", v))
        self.prop_actions["bold"] = pb.addWidget(self.w_bold)

        def font_toggle(text, tip, key, setter):
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            fb = b.font()
            setter(fb)
            b.setFont(fb)
            b.toggled.connect(lambda v: self.on_style(key, v))
            self.prop_actions[key] = pb.addWidget(b)
            return b

        self.w_italic = font_toggle("I", "Italic", "italic", lambda f: f.setItalic(True))
        self.w_underline = font_toggle("U", "Underline", "underline",
                                       lambda f: f.setUnderline(True))
        # text alignment: three exclusive buttons in one toolbar slot
        align_w = QWidget()
        al = QHBoxLayout(align_w)
        al.setContentsMargins(4, 0, 0, 0)
        al.setSpacing(0)
        self.w_align = {}
        for key, tip in (("left", "Align left"), ("center", "Center"), ("right", "Align right")):
            b = QToolButton()
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=key: self.on_style("align", k))
            al.addWidget(b)
            self.w_align[key] = b
        self.prop_actions["align"] = pb.addWidget(align_w)
        self.w_shadow = QToolButton()
        self.w_shadow.setText("Shadow")
        self.w_shadow.setCheckable(True)
        self.w_shadow.setToolTip("Drop shadow")
        self.w_shadow.toggled.connect(lambda v: self.on_style("shadow", v))
        self.prop_actions["shadow"] = pb.addWidget(self.w_shadow)
        self.w_pixel = spin("Pixel", 2, 80, "pixel_size", " px")
        # make the active state of B / I / U / alignment / Shadow obvious. Styled per button:
        # a stylesheet on the whole bar would restyle every widget (incl. the font list)
        # and add ~0.6 s to the first screenshot.
        css = ("QToolButton:checked { background-color: palette(highlight); "
               "color: palette(highlighted-text); border-radius: 3px; }")
        self.toggle_buttons = [self.w_bold, self.w_italic, self.w_underline, self.w_shadow,
                               *self.w_align.values()]
        for b in self.toggle_buttons:
            b.setStyleSheet(css)
        # one uniform size for B / I / U and the alignment buttons; bigger alignment icons
        for b in (self.w_bold, self.w_italic, self.w_underline, *self.w_align.values()):
            b.setFixedSize(28, 28)
        for b in self.w_align.values():
            b.setIconSize(QSize(20, 20))
        self.w_shadow.setFixedHeight(28)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, pb)

        tt = QToolBar("Tools")
        tt.setObjectName("tools")
        tt.setIconSize(QSize(24, 24))
        for tid, _, _ in TOOLS:
            tt.addAction(self.tool_actions[tid])
        tt.addSeparator()
        tt.addAction(self.a_keep_tool)
        self.addToolBar(Qt.ToolBarArea.LeftToolBarArea, tt)

        # Fixed row height: the property controls appear/disappear with the
        # selection, and must not make the toolbar row jump.
        h = max(tb.sizeHint().height(), pb.sizeHint().height())
        tb.setFixedHeight(h)
        pb.setFixedHeight(h)

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        for a in (self.a_cap_region, self.a_cap_window, self.a_cap_screen, self.a_cap_full,
                  self.a_cap_last, None, self.a_open, self.a_paste_new, None, self.a_save,
                  self.a_save_as, self.a_save_proj, self.a_copy_img, None, self.a_dup_tab,
                  self.a_dup_flat, None, self.a_close, self.a_close_all, None, self.a_settings, self.a_quit,
                  self.a_quit_discard):
            f.addSeparator() if a is None else f.addAction(a)
        e = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, None, self.a_cut, self.a_copy, self.a_paste,
                  self.a_dup, self.a_delete, None, self.a_select_all, None, self.a_front,
                  self.a_back):
            e.addSeparator() if a is None else e.addAction(a)
        t = mb.addMenu("&Tools")
        for tid, _, _ in TOOLS:
            t.addAction(self.tool_actions[tid])
        t.addSeparator()
        t.addAction(self.a_pick_stroke)
        t.addAction(self.a_pick_fill)
        t.addSeparator()
        t.addAction(self.a_keep_tool)
        v = mb.addMenu("&View")
        for a in (self.a_zoom_in, self.a_zoom_out, self.a_zoom_fit, self.a_zoom_100, None,
                  self.a_next, self.a_prev):
            v.addSeparator() if a is None else v.addAction(a)
        h = mb.addMenu("&Help")
        h.addAction("Keyboard shortcuts", self._show_help)

    # -- theme-aware icons ------------------------------------------------------
    def _apply_icons(self):
        from PyQt6.QtGui import QPalette
        pal = self.palette()
        fg = pal.color(QPalette.ColorRole.WindowText)
        bg = pal.color(QPalette.ColorRole.Window)
        for tid, a in self.tool_actions.items():
            a.setIcon(icons.tool_icon(tid, fg, bg))
        for a, name in ((self.a_save, "save"), (self.a_copy_img, "copy"),
                        (self.a_undo, "undo"), (self.a_redo, "redo"),
                        (self.a_zoom_fit, "fit"), (self.a_zoom_100, "100")):
            a.setIcon(icons.action_icon(name, fg))
        self.a_close_all.setIcon(icons.action_icon("trash", fg))
        if hasattr(self, "delay_btn"):
            self.delay_btn.setIcon(icons.action_icon("clock", fg))
        for key, b in getattr(self, "w_align", {}).items():  # built after the actions
            b.setIcon(icons.action_icon("align-" + key, fg))
        self.a_keep_tool.setIcon(icons.action_icon(
            "lock" if self.a_keep_tool.isChecked() else "unlock", fg))

    def changeEvent(self, e):
        from PyQt6.QtCore import QEvent
        if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange) \
                and hasattr(self, "a_keep_tool"):
            self._apply_icons()
        super().changeEvent(e)

    def _keep_tool_toggled(self, on, save=True):
        if save:
            self.settings.set("keep_tool", on)
        self.a_keep_tool.setToolTip(
            "Tool is locked: stays active after drawing  (Q)" if on else
            "After drawing, switch back to Select  (Q to lock the tool)")
        self._apply_icons()
        if save:
            self.status("Tool lock on: the tool stays active after drawing" if on else
                        "Tool lock off: back to Select after each drawing")

    def update_delay_ui(self):
        n = int(self.settings.get("capture_delay") or 0)
        self.delay_btn.setText(f"{n} s" if n else "")
        self.delay_btn.setToolTip(
            f"Capture delay: {n} s (applies to every capture, also the Print key)" if n else
            "Capture delay: off. Set a few seconds to open a menu before the screenshot")

    def after_draw(self, tool):
        """Called when an object was finished with a drawing tool."""
        if self.a_keep_tool.isChecked() or tool != self.tool:
            return
        doc = self.current_doc()
        keep = doc.scene.selectedItems() if doc else []
        self.set_tool("select")
        for it in keep:
            it.setSelected(True)

    # -- eyedropper -----------------------------------------------------------------
    def start_pick(self, key):
        doc = self.current_doc()
        if doc is None:
            return
        label = {"fill": "fill color", "border": "frame color"}.get(key, "color")

        def picked(color):
            if color is None:
                self.status("Color pick cancelled")
                return
            {"fill": self.w_fill, "border": self.w_border}.get(key, self.w_stroke).set_color(color)
            self.on_style(key, color)
            self.status(f"Picked {color.name()} as {label}")

        doc.view.start_pick(picked)
        self.status(f"Click anywhere in the screenshot to pick the {label} · Esc to cancel")

    # -- documents -------------------------------------------------------------
    def current_doc(self):
        w = self.stack.currentWidget()
        for d in self.docs:
            if d.view is w:
                return d
        return None

    def add_document(self, image, title=None, path=None, states=None, uid=None,
                     created=None, restored_dirty=False, save_session=True):
        doc = Document(image, self, title, path)
        if uid:
            doc.uid = uid
        if created:
            doc.created = created
        if states:
            doc.load_states(states)
        doc.restored_dirty = restored_dirty
        self.docs.append(doc)
        self.undo_group.addStack(doc.undo)
        self.stack.addWidget(doc.view)
        it = QListWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, id(doc))
        self.session.addItem(it)
        doc.undo.indexChanged.connect(lambda _=0, d=doc: self._doc_changed(d))
        doc.undo.cleanChanged.connect(lambda _=False, d=doc: self._update_title(d))
        doc.scene.selectionChanged.connect(lambda d=doc: self._on_selection(d))
        doc.view.zoomChanged.connect(lambda z: self._show_zoom())
        self._update_title(doc)
        self._render_thumb(doc)
        self.session.setCurrentRow(len(self.docs) - 1)
        doc.view.set_tool(self.tool)
        self._update_enabled()
        if save_session:
            self._queue_session(doc)
        return doc

    def _row_of(self, doc):
        return self.docs.index(doc) if doc in self.docs else -1

    def _on_row(self, row):
        if 0 <= row < len(self.docs):
            doc = self.docs[row]
            self.stack.setCurrentWidget(doc.view)
            self.undo_group.setActiveStack(doc.undo)
            doc.view.set_tool(self.tool)
            doc.view.setFocus()
            self._show_zoom()
            self._sync_bar()
        else:
            self.stack.setCurrentWidget(self.placeholder)
        self._update_enabled()

    def _step_tab(self, d):
        if self.docs:
            self.session.setCurrentRow((self.session.currentRow() + d) % len(self.docs))

    def _update_title(self, doc):
        row = self._row_of(doc)
        if row < 0:
            return
        it = self.session.item(row)
        size = f"{doc.background.width()}×{doc.background.height()}"
        name = os.path.basename(doc.path) if doc.path else doc.title
        mark = " •" if doc.is_modified() else ""
        it.setText(f"{name}{mark}\n{size}")
        it.setToolTip(doc.path or "Not saved yet")

    def _doc_changed(self, doc):
        self._queue_session(doc)
        self._dirty_thumbs.add(doc)
        self._thumb_timer.start()
        self._update_title(doc)
        if doc is self.current_doc():
            self._sync_bar()

    def _refresh_thumbs(self):
        for d in list(self._dirty_thumbs):
            if d in self.docs:
                self._render_thumb(d)
        self._dirty_thumbs.clear()

    def _render_thumb(self, doc):
        row = self._row_of(doc)
        if row < 0:
            return
        img = doc.render_image().scaled(self.session.iconSize(),
                                        Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation)
        from PyQt6.QtGui import QPixmap
        self.session.item(row).setIcon(QIcon(QPixmap.fromImage(img)))

    def close_current(self):
        doc = self.current_doc()
        if doc:
            self.close_doc(doc)

    def close_doc(self, doc, ask=True):
        if ask and doc.is_modified():
            r = QMessageBox.question(
                self, "Close screenshot", "This screenshot has unsaved changes.",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return False
            if r == QMessageBox.StandardButton.Save and not self.save_doc(doc):
                return False
        row = self._row_of(doc)
        self.docs.remove(doc)
        self._session_dirty.discard(doc)
        self.store.remove(doc)
        self.undo_group.removeStack(doc.undo)
        self.stack.removeWidget(doc.view)
        self.session.takeItem(row)
        doc.view.deleteLater()
        if self.docs:
            self.session.setCurrentRow(min(row, len(self.docs) - 1))
        else:
            self._on_row(-1)
        self._update_enabled()
        return True

    def close_all(self, keep=None, ask=True):
        """Close every screenshot (except `keep`). Asks once if annotations are unsaved."""
        targets = [d for d in self.docs if d is not keep]
        if not targets:
            return True
        unsaved = [d for d in targets if d.is_modified()]
        if ask and unsaved:
            box = QMessageBox(self)
            box.setWindowTitle("Close screenshots")
            box.setText(f"Close {len(targets)} screenshot(s)? {len(unsaved)} of them "
                        f"have unsaved annotations.")
            b_save = box.addButton("Save unsaved && close", QMessageBox.ButtonRole.AcceptRole)
            b_close = box.addButton("Close without saving",
                                    QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() is b_save:
                for d in unsaved:
                    if not self.save_doc(d, quiet=True):
                        return False
            elif box.clickedButton() is not b_close:
                return False
        for d in targets:
            self.close_doc(d, ask=False)
        self.status(f"Closed {len(targets)} screenshot(s)")
        return True

    def _update_enabled(self):
        has = self.current_doc() is not None
        n = len(self.docs)
        self.count_label.setText(f"Screenshots ({n})" if n else "")
        self.a_close_all.setEnabled(n > 0)
        self.clear_btn.setVisible(n > 0)
        for a in (self.a_save, self.a_save_as, self.a_save_proj, self.a_dup_tab, self.a_dup_flat,
                  self.a_copy_img, self.a_close, self.a_copy,
                  self.a_cut, self.a_paste, self.a_dup, self.a_delete, self.a_select_all,
                  self.a_front, self.a_back, self.a_zoom_in, self.a_zoom_out, self.a_zoom_fit,
                  self.a_zoom_100):
            a.setEnabled(has)

    def _session_menu(self, pos):
        it = self.session.itemAt(pos)
        if it is None:
            return
        doc = self.docs[self.session.row(it)]
        m = QMenu(self)
        m.addAction("Copy image", lambda: self.copy_image(doc))
        m.addAction("Save", lambda: self.save_doc(doc))
        m.addAction("Save as…", lambda: self.save_doc(doc, ask_path=True))
        m.addAction("Save editable copy (.leafshot)…", lambda: self.save_project_dialog(doc))
        m.addSeparator()
        m.addAction("Duplicate (annotations stay editable)",
                    lambda: self.duplicate_tab(False, doc))
        m.addAction("Duplicate as flattened image", lambda: self.duplicate_tab(True, doc))
        if doc.path:
            m.addAction("Open containing folder",
                        lambda: __import__("subprocess").Popen(
                            ["xdg-open", os.path.dirname(doc.path)]))
        m.addSeparator()
        m.addAction("Close", lambda: self.close_doc(doc))
        m.addAction("Close all others", lambda: self.close_all(keep=doc))
        m.addAction("Close all", self.close_all)
        m.exec(self.session.mapToGlobal(pos))

    # -- tools & styles -------------------------------------------------------
    def set_tool(self, tool):
        self.tool = tool
        self.tool_actions[tool].setChecked(True)
        doc = self.current_doc()
        if doc:
            if tool != "select":
                doc.scene.clearSelection()
            doc.view.set_tool(tool)
        hints = {
            "select": "Click to select · drag to move · handles to resize · double-click text "
                      "to edit · Ctrl+C/Ctrl+V copies objects",
            "crop": "Drag the area to keep, then Enter",
            "text": "Click to place text, or click existing text to edit it",
            "step": "Click to place numbered markers (1, 2, 3 …)",
        }
        self.status(hints.get(tool, "Drag to draw · Shift = straight/square · "
                                    "Esc or right-click = back to Select"))
        self._sync_bar()

    def new_item(self, tool):
        cls = TOOL_CLASSES.get(tool)
        if cls is None:
            return None
        item = cls()
        for key in cls.STYLE_KEYS:
            item.apply_style(key, self.settings.tool_style(tool, key))
        return item

    def _selected_annots(self, doc=None):
        doc = doc or self.current_doc()
        if doc is None:
            return []
        return [it for it in doc.scene.selectedItems() if is_annot(it)]

    def _on_selection(self, doc):
        try:
            if doc is self.current_doc():
                self._sync_bar()
        except RuntimeError:  # scene already destroyed (shutdown)
            pass

    def _sync_bar(self):
        """Show the style of the selection (or of the current tool)."""
        sel = self._selected_annots()
        if sel:
            keys = set()
            for it in sel:
                keys.update(it.STYLE_KEYS)
            src = sel[0]

            def val(k):
                for it in sel:
                    if k in it.STYLE_KEYS:
                        return it.style_value(k)
                return None
        else:
            cls = TOOL_CLASSES.get(self.tool)
            keys = set(cls.STYLE_KEYS) if cls else set()
            src = None

            def val(k):
                return self.settings.tool_style(self.tool, k) if cls else None
        self._updating_bar = True
        try:
            for k in ALL_KEYS:
                self.prop_actions[k].setVisible(k in keys)
            for k in keys:
                v = val(k)
                if v is None:
                    continue
                if k == "stroke":
                    self.w_stroke.set_color(v)
                elif k == "fill":
                    self.w_fill.set_color(v)
                elif k == "border":
                    self.w_border.set_color(v)
                elif k == "width":
                    self.w_width.setValue(int(round(v)))
                elif k == "font_size":
                    self.w_font.setValue(int(v))
                elif k == "font_family":
                    self.w_family.set_family(str(v))
                elif k == "align":
                    self.w_align.get(str(v), self.w_align["left"]).setChecked(True)
                elif k == "bold":
                    self.w_bold.setChecked(bool(v))
                elif k == "italic":
                    self.w_italic.setChecked(bool(v))
                elif k == "underline":
                    self.w_underline.setChecked(bool(v))
                elif k == "shadow":
                    self.w_shadow.setChecked(bool(v))
                elif k == "pixel_size":
                    self.w_pixel.setValue(int(v))
        finally:
            self._updating_bar = False
        _ = src

    def on_style(self, key, value):
        if self._updating_bar:
            return
        sel = [it for it in self._selected_annots() if key in it.STYLE_KEYS]
        if sel:
            doc = self.current_doc()
            doc.undo.beginMacro("Change style")
            for it in sel:
                before = it.get_state()
                it.apply_style(key, value)
                after = it.get_state()
                if after != before:
                    doc.undo.push(StateCommand(it, before, after, "Change style"))
                if it.TYPE in TOOL_CLASSES:
                    self.settings.set_tool_style(it.TYPE, key, value)
            doc.undo.endMacro()
        elif self.tool in TOOL_CLASSES:
            self.settings.set_tool_style(self.tool, key, value)

    # -- clipboard ---------------------------------------------------------------
    def _editing_text(self):
        doc = self.current_doc()
        return doc is not None and doc.view.editing_text()

    def copy(self):
        if self._editing_text():
            return
        sel = self._selected_annots()
        if sel:
            states = I.states_to_json_ready([it.get_state() for it in
                                             sorted(sel, key=lambda i: i.zValue())])
            md = QMimeData()
            md.setData(MIME_ITEMS, QByteArray(json.dumps(states).encode()))
            QGuiApplication.clipboard().setMimeData(md)
            self.current_doc().paste_offset = 0
            self.status(f"Copied {len(sel)} object(s) – Ctrl+V to paste (also into another tab)")
        else:
            self.copy_image()

    def cut(self):
        if self._editing_text() or not self._selected_annots():
            return
        self.copy()
        self.delete_selected()

    def paste(self):
        if self._editing_text():
            return
        doc = self.current_doc()
        if doc is None:
            self.paste_as_new()
            return
        md = QGuiApplication.clipboard().mimeData()
        if md is None:
            return
        if md.hasFormat(MIME_ITEMS):
            try:
                states = I.states_from_json(json.loads(bytes(md.data(MIME_ITEMS)).decode()))
            except (ValueError, KeyError):
                return
            doc.paste_offset += 16
            self._insert_states(doc, states, doc.paste_offset, "Paste")
        elif md.hasImage():
            img = QImage(md.imageData())
            if img.isNull():
                return
            item = I.ImageItem(img)
            vc = doc.view.mapToScene(doc.view.viewport().rect().center())
            item.setPos(vc - QPointF(img.width() / 2, img.height() / 2))
            item.setZValue(doc.next_z())
            doc.scene.addItem(item)
            doc.undo.push(AddCommand(doc.scene, [item], True, "Paste image"))
            doc.scene.clearSelection()
            item.setSelected(True)
            self.set_tool("select")
        elif md.hasText() and md.text().strip():
            item = self.new_item("text")
            item.setPlainText(md.text())
            vc = doc.view.mapToScene(doc.view.viewport().rect().center())
            item.setPos(vc)
            item.setZValue(doc.next_z())
            doc.scene.addItem(item)
            doc.undo.push(AddCommand(doc.scene, [item], True, "Paste text"))
            doc.scene.clearSelection()
            item.setSelected(True)
            self.set_tool("select")

    def _insert_states(self, doc, states, offset, label):
        if not states:
            return
        new = []
        z = doc.next_z()
        step = doc.next_step_number()
        for s in states:
            it = I.make_item(s)
            it.moveBy(offset, offset)
            it.setZValue(z)
            z += 1
            if isinstance(it, I.StepItem):
                it.number = step
                step += 1
            new.append(it)
            doc.scene.addItem(it)
        # pasted into a smaller screenshot? pull the group back into view
        br = new[0].sceneBoundingRect()
        for it in new[1:]:
            br = br.united(it.sceneBoundingRect())
        sr = doc.scene.sceneRect()
        if not sr.contains(br):
            dx = dy = 0.0
            if br.right() > sr.right():
                dx = sr.right() - br.right() - 10
            if br.left() + dx < sr.left():
                dx = sr.left() - br.left() + 10
            if br.bottom() > sr.bottom():
                dy = sr.bottom() - br.bottom() - 10
            if br.top() + dy < sr.top():
                dy = sr.top() - br.top() + 10
            for it in new:
                it.moveBy(dx, dy)
        doc.undo.push(AddCommand(doc.scene, new, True, label))
        doc.scene.clearSelection()
        for it in new:
            it.setSelected(True)
        if self.tool != "select":
            self.set_tool("select")
            for it in new:
                it.setSelected(True)

    def duplicate(self):
        if self._editing_text():
            return
        sel = self._selected_annots()
        if sel:
            states = [it.get_state() for it in sorted(sel, key=lambda i: i.zValue())]
            self._insert_states(self.current_doc(), states, 16, "Duplicate")

    def delete_selected(self):
        if self._editing_text():
            return
        sel = self._selected_annots()
        if sel:
            doc = self.current_doc()
            doc.undo.push(RemoveCommand(doc.scene, sel))

    def select_all(self):
        if self._editing_text():
            return
        doc = self.current_doc()
        if doc:
            self.set_tool("select")
            for it in doc.annotations():
                it.setSelected(True)

    def restack(self, front):
        sel = self._selected_annots()
        if not sel:
            return
        doc = self.current_doc()
        zs = [it.zValue() for it in doc.annotations()]
        doc.undo.beginMacro("Reorder")
        base = (max(zs) + 1) if front else (min(zs) - len(sel))
        for i, it in enumerate(sorted(sel, key=lambda x: x.zValue())):
            b = it.get_state()
            it.setZValue(base + i)
            doc.undo.push(StateCommand(it, b, it.get_state(), "Reorder"))
        doc.undo.endMacro()

    def copy_image(self, doc=None):
        doc = doc or self.current_doc()
        if doc is None:
            return
        img = doc.render_image()
        md = QMimeData()
        md.setImageData(img)
        md.setUrls([QUrl.fromLocalFile(self.temp_png(doc, img))])
        QGuiApplication.clipboard().setMimeData(md)
        self.status("Image copied to clipboard")

    def temp_png(self, doc, img):
        name = (os.path.splitext(os.path.basename(doc.path))[0] if doc.path
                else "Screenshot " + doc.title.replace(":", "-"))
        path = os.path.join(self._tmpdir, name + ".png")
        img.save(path, "PNG")
        return path

    def paste_as_new(self):
        md = QGuiApplication.clipboard().mimeData()
        if md is not None and md.hasImage():
            img = QImage(md.imageData())
            if not img.isNull():
                self.add_document(img, "Pasted " + time.strftime("%H:%M:%S"))
                return
        self.status("No image in the clipboard")

    # -- saving / opening ------------------------------------------------------
    def save(self):
        doc = self.current_doc()
        if doc:
            self.save_doc(doc)

    def save_as(self):
        doc = self.current_doc()
        if doc:
            self.save_doc(doc, ask_path=True)

    def default_path(self, doc):
        folder = os.path.expanduser(self.settings.get("save_dir"))
        fmt = self.settings.get("format")
        base = time.strftime(self.settings.get("filename_pattern"), time.localtime(doc.created))
        base = base.replace("/", "-") or "Screenshot"
        path = os.path.join(folder, f"{base}.{fmt}")
        n = 2
        while os.path.exists(path):
            path = os.path.join(folder, f"{base} ({n}).{fmt}")
            n += 1
        return path

    def save_doc(self, doc, ask_path=False, quiet=False):
        path = doc.path
        if ask_path or not path:
            path = self.default_path(doc)
            if ask_path:
                path, _ = QFileDialog.getSaveFileName(
                    self, "Save screenshot", path, "PNG (*.png);;JPEG (*.jpg *.jpeg)")
                if not path:
                    return False
                if not os.path.splitext(path)[1]:
                    path += ".png"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img = doc.render_image()
        ext = os.path.splitext(path)[1].lower()
        if ext in (".jpg", ".jpeg"):
            flat = QImage(img.size(), QImage.Format.Format_RGB32)
            flat.fill(QColor(255, 255, 255))
            from PyQt6.QtGui import QPainter
            p = QPainter(flat)
            p.drawImage(0, 0, img)
            p.end()
            ok = flat.save(path, "JPG", self.settings.get("jpg_quality"))
        else:
            ok = img.save(path, "PNG")
        if not ok:
            QMessageBox.warning(self, "Save failed", f"Could not write:\n{path}")
            return False
        doc.path = path
        extra = ""
        if self.settings.get("save_project_with_image"):
            ppath = os.path.splitext(path)[0] + project.EXT
            try:
                project.write_project(doc, ppath)
                doc.project_path = ppath
                extra = " (+ editable .leafshot)"
            except OSError as ex:
                extra = f" (editable copy failed: {ex})"
        doc.mark_saved()
        self._update_title(doc)
        self._queue_session(doc)
        if not quiet:
            self.status(f"Saved: {path}{extra}")
        return True

    def open_dialog(self):
        fmts = " ".join(f"*.{bytes(f).decode()}" for f in QImageReader.supportedImageFormats())
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open image or Leafshot project",
            os.path.expanduser(self.settings.get("save_dir")),
            f"Images and Leafshot projects ({fmts} *{project.EXT});;"
            f"Leafshot projects (*{project.EXT});;Images ({fmts})")
        self.open_files(paths)

    def open_files(self, paths):
        for p in paths:
            if project.is_project(p):
                try:
                    img, states, meta = project.read_project(p)
                except Exception as ex:  # noqa: BLE001 - show any load problem
                    QMessageBox.warning(self, "Cannot open", f"{p}\n\n{ex}")
                    continue
                doc = self.add_document(img, os.path.splitext(os.path.basename(p))[0],
                                        path=meta.get("image_path"), states=states)
                doc.project_path = p
                self._update_title(doc)
                continue
            img = QImage(p)
            if img.isNull():
                self.status(f"Not an image: {p}")
                continue
            doc = self.add_document(img, os.path.basename(p), path=p)
            self._update_title(doc)
        if paths:
            self.bring_up()

    # -- projects, duplicates, session -------------------------------------------------
    def save_project_dialog(self, doc=None):
        doc = doc or self.current_doc()
        if doc is None:
            return False
        if doc.project_path:
            start = doc.project_path
        elif doc.path:
            start = os.path.splitext(doc.path)[0] + project.EXT
        else:
            start = os.path.splitext(self.default_path(doc))[0] + project.EXT
        os.makedirs(os.path.dirname(start), exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(self, "Save editable copy", start,
                                              f"Leafshot project (*{project.EXT})")
        if not path:
            return False
        if not path.endswith(project.EXT):
            path += project.EXT
        return self.save_project(doc, path)

    def save_project(self, doc, path):
        try:
            project.write_project(doc, path)
        except OSError as ex:
            QMessageBox.warning(self, "Save failed", f"Could not write:\n{path}\n\n{ex}")
            return False
        doc.project_path = path
        doc.mark_saved()
        self._update_title(doc)
        self.status(f"Saved editable copy: {path}  (open it again with Ctrl+O)")
        return True

    def duplicate_tab(self, flatten, doc=None):
        doc = doc or self.current_doc()
        if doc is None:
            return None
        if flatten:
            new = self.add_document(doc.render_image(), doc.title + " (flat)")
        else:
            new = self.add_document(doc.background.copy(), doc.title + " (copy)",
                                    states=project.doc_annotation_states(doc))
        self.status("Duplicated as a new tab" + (" with annotations baked in" if flatten else
                                                 " - annotations still editable"))
        return new

    def _queue_session(self, doc):
        # always auto-saved: protects against crashes / power loss in every quit mode
        self._session_dirty.add(doc)
        self._session_timer.start()

    def flush_session(self, complete=False):
        self._session_timer.stop()
        if complete:  # also docs that were never written (e.g. setting just enabled)
            self._session_dirty.update(d for d in self.docs
                                       if not os.path.exists(self.store.path_for(d)))
        for d in list(self._session_dirty):
            if d in self.docs:
                self.store.save(d)
        self._session_dirty.clear()
        if complete:
            self.store.wait()  # quitting: make sure everything is on disk

    def restore_session(self):
        n = 0
        for created, uid, img, states, meta in self.store.load_all():
            doc = self.add_document(img, meta.get("title"), path=meta.get("image_path"),
                                    states=states, uid=uid, created=created,
                                    restored_dirty=bool(meta.get("modified")),
                                    save_session=False)
            self._update_title(doc)
            n += 1
        return n

    # -- zoom ---------------------------------------------------------------------
    def _zoom(self, f):
        doc = self.current_doc()
        if doc:
            doc.view.set_zoom(doc.view.zoom() * f)

    def _zoom_abs(self, z):
        doc = self.current_doc()
        if doc:
            doc.view.set_zoom(z)

    def _fit(self):
        doc = self.current_doc()
        if doc:
            doc.view.fit()

    def _show_zoom(self):
        doc = self.current_doc()
        self.zoom_label.setText(f"{doc.view.zoom() * 100:.0f}%  " if doc else "")

    # -- misc -------------------------------------------------------------------
    def status(self, msg):
        self.statusBar().showMessage(msg, 8000)

    def bring_up(self):
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        self.settings.set("window_geometry", self.saveGeometry())
        e.ignore()
        if self.settings.get("close_action") == "quit":
            self.app.quit()  # follows the 'When quitting' setting
        else:
            self.hide()  # keep running in the background (tray or hidden)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasUrls():
            self.open_files([u.toLocalFile() for u in md.urls() if u.isLocalFile()])
        elif md.hasImage():
            self.add_document(QImage(md.imageData()), "Dropped " + time.strftime("%H:%M:%S"))

    def _show_help(self):
        QMessageBox.information(self, "Keyboard shortcuts", HELP_TEXT)

    def unsaved_docs(self):
        return [d for d in self.docs if d.is_modified()]


HELP_TEXT = """<b>Capture</b><br>
Hotkey (e.g. Print): drag a region, or click a window · Enter = whole monitor · Esc = cancel<br>
Ctrl+N new capture · Ctrl+Shift+N repeat last region<br><br>
<b>Tools</b><br>
V select · R rectangle · E ellipse · L line · A arrow · F freehand · H highlighter ·
T text · N numbered step · P pixelate · C crop<br>
Shift while drawing = straight line / square · Esc or right-click = back to Select<br><br>
<b>Objects</b><br>
Ctrl+C / Ctrl+V copy &amp; paste objects (also between screenshots) · Ctrl+D duplicate ·
Ctrl+X cut · Del delete · Ctrl+A select all · arrow keys nudge (Shift = 10 px) ·
Ctrl+Shift+Up/Down bring to front / send to back<br>
Double-click text (or select + Enter) to edit it · Esc finishes editing<br>
With nothing selected, Ctrl+C copies the whole annotated image<br><br>
<b>File</b><br>
Ctrl+S quick save · Ctrl+Shift+S save as · Ctrl+Alt+S save editable copy (.leafshot) ·
Ctrl+Shift+C copy image ·
Ctrl+Shift+V paste clipboard image as new tab · Ctrl+W close tab · Ctrl+Shift+W close all · Ctrl+Tab next tab<br>
Ctrl+Shift+D duplicate screenshot (editable) · Ctrl+Shift+F duplicate flattened ·
Ctrl+Q quit (what happens is set in Settings) · Ctrl+Shift+Q quit without saving<br>
Drag a thumbnail from the left into any app (mail, chat, file manager)<br><br>
<b>View</b><br>
Ctrl+wheel zoom · middle-drag pan · Ctrl+0 fit · Ctrl+1 100%"""
