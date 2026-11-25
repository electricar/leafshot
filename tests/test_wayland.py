"""Leafshot on a real Wayland compositor (weston, headless) with a mock screenshot portal.

Run via tests/run_wayland_tests.sh (starts weston + a private D-Bus + the mock portal).
"""
import os
import sys
import tempfile

os.environ["HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = os.path.join(os.environ["HOME"], ".config")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from leafshot import ipc, wayland  # noqa: E402

qapp = QApplication(sys.argv[:1])
qapp.setQuitOnLastWindowClosed(False)
from leafshot.app import App  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


def wait_for(cond, ms=5000):
    t = 0
    while not cond() and t < ms:
        QTest.qWait(50)
        t += 50
    return cond()


def mouse(w, typ, pos, buttons=Qt.MouseButton.LeftButton):
    btn = Qt.MouseButton.LeftButton if typ != QEvent.Type.MouseMove else Qt.MouseButton.NoButton
    if typ == QEvent.Type.MouseButtonRelease:
        buttons = Qt.MouseButton.NoButton
    ev = QMouseEvent(typ, QPointF(pos), QPointF(w.mapToGlobal(pos)), btn, buttons,
                     Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w, ev)


print("Qt platform:", QGuiApplication.platformName(), "| screens:",
      [s.geometry().getRect() for s in QGuiApplication.screens()])
check(QGuiApplication.platformName().startswith("wayland"), "Qt runs natively on Wayland")
check(wayland.is_wayland_session(), "Wayland session detected")

app = App(qapp, ipc.acquire_startup_lock())
ed = app.editor
fails_seen = []
app.capture_failed = lambda err: fails_seen.append(err)
check(app.portal is not None, "portal backend selected on Wayland")

# --- permission refused (run with MOCK_PORTAL_MODE=deny) -------------------------------------
if os.environ.get("MOCK_PORTAL_MODE") == "deny":
    app.handle({"cmd": "region"})
    check(wait_for(lambda: bool(fails_seen)), "refused permission is reported to the user")
    check(app.overlay is None and not ed.docs, "no overlay, no empty tab after a refusal")
    check(fails_seen and "refused" in fails_seen[0], f"clear message: {fails_seen}")
    app.handle({"cmd": "full"})
    QTest.qWait(600)
    check(len(fails_seen) == 2, "Leafshot keeps working and can try again")
    print("\nFAILURES:", FAILS if FAILS else "none", flush=True)
    os._exit(1 if FAILS else 0)

before = {f for f in os.listdir(tempfile.gettempdir()) if f.startswith("portal-shot-")}

# --- full screen via the portal -----------------------------------------------------------
virt = app.editor.screen().virtualGeometry()
app.handle({"cmd": "full"})
ok = wait_for(lambda: len(ed.docs) == 1)
check(ok, "full-screen capture through the portal adds a tab")
if ok:
    d = ed.docs[0]
    check(d.background.width() >= virt.width() and d.background.height() >= virt.height(),
          f"screenshot covers the whole desktop {d.background.size()} vs {virt.size()}")
    px = d.background.pixelColor(150, 150)
    check(px.name() == "#e0e0e0", f"pixels come from the portal image ({px.name()})")
check(app.portal.last_method == "portal", "portal (not a fallback tool) was used")
after = {f for f in os.listdir(tempfile.gettempdir()) if f.startswith("portal-shot-")}
check(not (after - before), "portal's temporary screenshot file was cleaned up")

# --- region selection: one overlay per monitor ----------------------------------------------
app.handle({"cmd": "region"})
check(wait_for(lambda: app.overlay is not None), "region capture opens the selection overlay")
panels = list(app.overlay.g.panels) if app.overlay else []
check(len(panels) == len(QGuiApplication.screens()),
      f"one overlay window per monitor ({len(panels)} / {len(QGuiApplication.screens())})")
check(all(p.isFullScreen() for p in panels), "overlays are fullscreen")
QTest.qWait(300)
p0 = panels[0]
mouse(p0, QEvent.Type.MouseButtonPress, QPoint(120, 120))
for i in range(1, 6):
    mouse(p0, QEvent.Type.MouseMove, QPoint(120 + 40 * i, 120 + 30 * i))
mouse(p0, QEvent.Type.MouseButtonRelease, QPoint(320, 270))
ok = wait_for(lambda: len(ed.docs) == 2)
check(ok, "dragging a region adds a tab")
if ok:
    d = ed.docs[1]
    check(abs(d.background.width() / 200 - d.background.height() / 150) < 0.05,
          f"region has the dragged shape {d.background.size()}")
    check(d.background.pixelColor(5, 5).name() == "#e0e0e0", "region shows the right part")
check(app.overlay is None and all(not p.isVisible() for p in panels), "all overlays closed")

# --- click without dragging = that whole monitor; Esc cancels -------------------------------
app.handle({"cmd": "region"})
wait_for(lambda: app.overlay is not None)
QTest.qWait(200)
p0 = app.overlay
mouse(p0, QEvent.Type.MouseButtonPress, QPoint(50, 50))
mouse(p0, QEvent.Type.MouseButtonRelease, QPoint(50, 50))
ok = wait_for(lambda: len(ed.docs) == 3)
scr0 = QGuiApplication.screens()[0].geometry()
scale = int(os.environ.get("MOCK_W", "1600")) / virt.width()
check(ok and abs(ed.docs[2].background.width() - scr0.width() * scale) <= 1,
      f"click captures the whole monitor ({ed.docs[2].background.width() if ok else '-'} px)")
app.handle({"cmd": "region"})
wait_for(lambda: app.overlay is not None)
QTest.qWait(200)
QTest.keyClick(app.overlay, Qt.Key.Key_Escape)
QTest.qWait(200)
check(app.overlay is None and len(ed.docs) == 3, "Esc cancels the selection")

# --- 'window' hotkey on Wayland falls back to the picker ---------------------------------------
app.handle({"cmd": "window"})
check(wait_for(lambda: app.overlay is not None), "Alt+Print (window) opens the picker on Wayland")
if app.overlay is not None:
    QTest.keyClick(app.overlay, Qt.Key.Key_Escape)
    QTest.qWait(200)

# --- repeat last region ---------------------------------------------------------------------------
app.handle({"cmd": "last"})
ok = wait_for(lambda: len(ed.docs) == 4)
check(ok, "repeat last region works on Wayland")

# --- delay still works --------------------------------------------------------------------------
app.set_delay(1)
app.handle({"cmd": "full"})
QTest.qWait(400)
check(len(ed.docs) == 4, "delay: not yet captured")
check(wait_for(lambda: len(ed.docs) == 5, 3000), "delay: captured after the countdown")
app.set_delay(0)

# --- editor works as usual (annotate + export) ---------------------------------------------------
ed.bring_up()
QTest.qWait(300)
doc = ed.current_doc()
from leafshot import items as I  # noqa: E402
r = ed.new_item("rect")
r.setPos(10, 10)
r.rect = __import__("PyQt6.QtCore", fromlist=["QRectF"]).QRectF(0, 0, 100, 60)
doc.scene.addItem(r)
img = doc.render_image()
check(img.pixelColor(10, 40).red() > 180, "annotations render on Wayland")
check(not fails_seen, f"no capture errors ({fails_seen})")

print("\nFAILURES:", FAILS if FAILS else "none", flush=True)
os._exit(1 if FAILS else 0)
