"""Stand-in for org.freedesktop.portal.Screenshot (for tests on a private D-Bus).

Behaves like the real portal: returns a request handle at once, then emits
org.freedesktop.portal.Request.Response on that path with a file:// URI.
MOCK_PORTAL_MODE=deny answers with code 2 (like a refused permission).
"""
import os
import sys
import tempfile

from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtClassInfo, pyqtSlot
from PyQt6.QtDBus import QDBusConnection, QDBusMessage, QDBusObjectPath
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter

W, H = int(os.environ.get("MOCK_W", "1600")), int(os.environ.get("MOCK_H", "1000"))


def make_image(path):
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor("#2a4d69"))
    p = QPainter(img)
    p.fillRect(100, 100, 400, 300, QColor("#e0e0e0"))   # a "window"
    p.fillRect(W // 2 + 100, 150, 300, 200, QColor("#f4b400"))
    p.end()
    img.save(path, "PNG")


@pyqtClassInfo("D-Bus Interface", "org.freedesktop.portal.Screenshot")
class Portal(QObject):
    calls = 0

    # a trailing QDBusMessage parameter makes QtDBus pass the incoming call (to see the sender)
    @pyqtSlot(str, "QVariantMap", QDBusMessage, result=QDBusObjectPath)
    def Screenshot(self, parent_window, options, msg):
        Portal.calls += 1
        sender = msg.service().lstrip(":").replace(".", "_")
        token = options.get("handle_token", f"t{Portal.calls}")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        QTimer.singleShot(200, lambda: self._respond(path, options))
        return QDBusObjectPath(path)

    def _respond(self, path, options):
        sig = QDBusMessage.createSignal(path, "org.freedesktop.portal.Request", "Response")
        if os.environ.get("MOCK_PORTAL_MODE") == "deny":
            sig.setArguments([2, {}])
        else:
            fd, f = tempfile.mkstemp(prefix="portal-shot-", suffix=".png")
            os.close(fd)
            make_image(f)
            sig.setArguments([0, {"uri": "file://" + f}])
        QDBusConnection.sessionBus().send(sig)


def main():
    app = QGuiApplication(sys.argv[:1]) if os.environ.get("QT_QPA_PLATFORM") else \
        QCoreApplication(sys.argv[:1])
    bus = QDBusConnection.sessionBus()
    portal = Portal()
    if not bus.registerObject("/org/freedesktop/portal/desktop", portal,
                              QDBusConnection.RegisterOption.ExportAllSlots):
        print("mock portal: registerObject failed", file=sys.stderr)
        return 1
    if not bus.registerService("org.freedesktop.portal.Desktop"):
        print("mock portal: registerService failed", file=sys.stderr)
        return 1
    print("mock portal ready", flush=True)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
