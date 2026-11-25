"""Screen capture on Wayland.

Wayland does not let applications read the screen directly. Leafshot asks the
desktop instead, through the standard screenshot portal
(org.freedesktop.portal.Screenshot), which Cinnamon (xdg-desktop-portal-xapp),
KDE Plasma, GNOME and wlroots desktops provide. If the portal is missing or
fails, command-line tools are tried (spectacle on KDE, grim on wlroots).

The portal is asynchronous: the answer arrives as a D-Bus signal, possibly
after the desktop asked the user for permission (first time only).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSlot
from PyQt6.QtDBus import (QDBusConnection, QDBusMessage, QDBusPendingCallWatcher,
                          QDBusPendingReply)
from PyQt6.QtGui import QGuiApplication, QImage

PORTAL_SERVICE = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_IFACE = "org.freedesktop.portal.Screenshot"
REQUEST_IFACE = "org.freedesktop.portal.Request"
TIMEOUT_MS = 60_000  # the user may be looking at a permission dialog


def log(*args):
    """LEAFSHOT_DEBUG=1 prints what the capture does (for bug reports)."""
    if os.environ.get("LEAFSHOT_DEBUG"):
        print("[leafshot]", *args, file=sys.stderr, flush=True)


def is_wayland_session():
    forced = os.environ.get("LEAFSHOT_BACKEND", "").lower()
    if forced in ("x11", "wayland"):
        return forced == "wayland"
    return (os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY"))
            or QGuiApplication.platformName().startswith("wayland"))


def qt_runs_native_wayland():
    return QGuiApplication.platformName().startswith("wayland")


class PortalGrabber(QObject):
    """grab(callback): callback(QImage) on success, callback(None, message) on failure."""

    _counter = 0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bus = QDBusConnection.sessionBus()
        self._cb = None
        self._path = None
        self._started = 0.0
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(lambda: self._finish(None, "The screenshot service did not "
                                                         "answer in time."))
        self._watcher = None
        self.last_method = None

    def busy(self):
        return self._cb is not None

    # -- public -------------------------------------------------------------------
    def grab(self, callback):
        if self._cb is not None:
            return
        self._cb = callback
        self._started = time.time()
        if not self.bus.isConnected():
            self._fallback("no D-Bus session")
            return
        PortalGrabber._counter += 1
        token = f"leafshot{os.getpid()}_{PortalGrabber._counter}"
        sender = self.bus.baseService().lstrip(":").replace(".", "_")
        self._path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        # subscribe BEFORE calling, so the answer can't be missed
        self.bus.connect(PORTAL_SERVICE, self._path, REQUEST_IFACE, "Response", self._on_response)
        msg = QDBusMessage.createMethodCall(PORTAL_SERVICE, PORTAL_PATH, PORTAL_IFACE,
                                            "Screenshot")
        msg.setArguments(["", {"handle_token": token, "interactive": False, "modal": False}])
        log("portal: Screenshot request", self._path)
        self._watcher = QDBusPendingCallWatcher(self.bus.asyncCall(msg, 5000), self)
        self._watcher.finished.connect(self._on_called)
        self._timer.start(TIMEOUT_MS)

    # -- portal ---------------------------------------------------------------------
    def _on_called(self, watcher):
        reply = QDBusPendingReply(watcher).reply()
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            log("portal: call failed:", reply.errorName(), reply.errorMessage())
            self._disconnect()
            self._fallback(reply.errorMessage() or reply.errorName())
            return
        args = reply.arguments()
        handle = args[0] if args else None
        handle = getattr(handle, "path", lambda: handle)() if handle is not None else None
        if handle and str(handle) != self._path:
            # very old portals ignore handle_token: listen on the path they gave us
            self._disconnect()
            self._path = str(handle)
            self.bus.connect(PORTAL_SERVICE, self._path, REQUEST_IFACE, "Response",
                             self._on_response)

    @pyqtSlot(QDBusMessage)
    def _on_response(self, msg):
        args = msg.arguments()
        code = int(args[0]) if args else 2
        results = args[1] if len(args) > 1 else {}
        log("portal: response code", code, "results", dict(results) if isinstance(results, dict)
            else results)
        self._disconnect()
        if code == 1:
            self._finish(None, "cancelled")
            return
        uri = results.get("uri") if isinstance(results, dict) else None
        if code != 0 or not uri:
            self._fallback(f"portal answered with code {code}")
            return
        self.last_method = "portal"
        self._finish(self._load(QUrl(str(uri)).toLocalFile()))

    def _disconnect(self):
        if self._path:
            self.bus.disconnect(PORTAL_SERVICE, self._path, REQUEST_IFACE, "Response",
                                self._on_response)

    # -- command-line fallbacks ---------------------------------------------------------------
    def _fallback(self, reason):
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        tools = []
        if "KDE" in desktop and shutil.which("spectacle"):
            tools.append(("spectacle", ["spectacle", "-b", "-n", "-f", "-o"]))
        if shutil.which("grim"):
            tools.append(("grim", ["grim"]))
        log("fallback: reason:", reason, "| tools:", [t[0] for t in tools] or "none")
        for name, cmd in tools:
            fd, path = tempfile.mkstemp(prefix="leafshot-", suffix=".png")
            os.close(fd)
            try:
                subprocess.run(cmd + [path], timeout=15, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                img = self._load(path, always_delete=True)
                if not img.isNull():
                    self.last_method = name
                    self._finish(img)
                    return
            except (OSError, subprocess.SubprocessError) as ex:
                log(f"fallback: {name} failed:", ex)
            finally:
                if os.path.exists(path):
                    os.remove(path)
        if reason.startswith("portal answered"):
            msg = ("Your desktop refused the screenshot. It may be waiting for permission, "
                   "or Leafshot's screenshot permission was denied earlier.")
        else:
            msg = f"No screenshot service available ({reason})."
        self._finish(None, msg)

    def _load(self, path, always_delete=False):
        img = QImage(path)
        # the portal saved this file just for us - don't leave it lying around
        try:
            if path and (always_delete or os.path.getmtime(path) >= self._started - 2):
                os.remove(path)
        except OSError:
            pass
        return img

    def _finish(self, img, error=None):
        log("result:", f"{img.width()}x{img.height()} via {self.last_method}"
            if img is not None and not img.isNull() else f"error: {error}")
        self._timer.stop()
        cb, self._cb = self._cb, None
        if cb is None:
            return
        if img is not None and not img.isNull():
            cb(img)
        else:
            cb(None, error or "The screenshot could not be read.")
