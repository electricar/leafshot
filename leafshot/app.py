"""Application: single-instance server, tray icon, capture orchestration."""
import json
import os
import sys
import time

from PyQt6.QtCore import QRect, QTimer
from PyQt6.QtGui import QCursor, QGuiApplication
from PyQt6.QtNetwork import QLocalServer
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
                             QMessageBox, QPushButton, QSystemTrayIcon, QWidget)

from . import capture, icons, ipc
from .editor import Editor
from .settings import Settings


class App:
    def __init__(self, qapp, lock_fd):
        self.qapp = qapp
        self.lock_fd = lock_fd
        self.settings = Settings()
        self.overlay = None
        self._pending = None
        self._countdown_left = 0
        self._tick = QTimer()
        self._tick.timeout.connect(self._on_tick)
        self.editor = Editor(self)
        self.editor.restore_session()
        self._setup_server()
        self._setup_tray()
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            QTimer.singleShot(1500, lambda: self.tray_message(
                "Wayland session detected",
                "Leafshot captures via X11. On Wayland the screenshot may be black - "
                "use the X11 Cinnamon session (default on Linux Mint)."))

    # -- single instance -----------------------------------------------------------
    def _setup_server(self):
        path = ipc.socket_path()
        QLocalServer.removeServer(path)  # stale socket from a crash
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(path):
            print(f"leafshot: cannot listen on {path}: {self.server.errorString()}",
                  file=sys.stderr)
        self.server.newConnection.connect(self._on_connection)

    def _on_connection(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def ready(s=sock, b=buf):
                b.extend(bytes(s.readAll()))
                if b.endswith(b"\n"):
                    try:
                        msg = json.loads(b.decode())
                    except ValueError:
                        msg = None
                    s.disconnectFromServer()
                    if msg:
                        self.handle(msg)

            sock.readyRead.connect(ready)
            sock.disconnected.connect(sock.deleteLater)
            if sock.bytesAvailable():
                ready()

    def handle(self, msg, first=False):
        cmd = msg.get("cmd", "show")
        if cmd == "quit":
            self.quit(interactive=False)
        elif cmd == "show":
            if not (first and msg.get("background")):
                self.editor.bring_up()
        elif cmd == "open":
            if msg.get("files"):
                self.editor.open_files(msg["files"])
            else:
                self.editor.bring_up()
        elif cmd in ("region", "window", "full", "screen", "last"):
            self.capture(cmd, delay=msg.get("delay", 0))

    # -- tray --------------------------------------------------------------------------
    def _setup_tray(self):
        self.tray = QSystemTrayIcon(icons.app_icon())
        self.tray.setToolTip(f"{ipc.APP_TITLE} - screenshots")
        m = QMenu()
        m.addAction("Capture region…", lambda: self.capture("region", tray=True))
        m.addAction("Capture active window", lambda: self.capture("window", tray=True))
        m.addAction("Capture current monitor", lambda: self.capture("screen", tray=True))
        m.addAction("Capture all monitors", lambda: self.capture("full", tray=True))
        m.addAction("Repeat last region", lambda: self.capture("last", tray=True))
        dm = m.addMenu("Delay")
        self.delay_actions = {}
        cur = int(self.settings.get("capture_delay") or 0)
        for sec in self.DELAYS:
            act = dm.addAction("No delay" if sec == 0 else f"{sec} s",
                               lambda s_=sec: self.set_delay(s_))
            act.setCheckable(True)
            act.setChecked(sec == cur)
            self.delay_actions[sec] = act
        self.a_cancel_delay = m.addAction("Cancel delayed capture", self.cancel_delayed)
        self.a_cancel_delay.setVisible(False)
        m.addSeparator()
        m.addAction("Show editor", self.editor.bring_up)
        m.addAction("Settings…", self.show_settings)
        m.addSeparator()
        m.addAction("Quit", self.quit)
        self.tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(self._tray_activated)
        self.apply_tray_setting()

    def apply_tray_setting(self):
        self.tray.setVisible(bool(self.settings.get("show_tray"))
                             and QSystemTrayIcon.isSystemTrayAvailable())

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            if self.editor.isVisible() and self.editor.isActiveWindow():
                self.editor.hide()
            else:
                self.editor.bring_up()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.capture("region", tray=True)

    def tray_message(self, title, text):
        if self.tray.isVisible():
            self.tray.showMessage(title, text, icons.app_icon(), 6000)

    # -- capture ---------------------------------------------------------------------------
    DELAYS = (0, 1, 2, 3, 5, 10)

    def capture(self, mode, delay=0, from_editor=False, tray=False):
        if self.overlay is not None or self._pending is not None:
            return  # already selecting / counting down
        delay = max(float(delay or 0), float(self.settings.get("capture_delay") or 0))
        hide_editor = from_editor and self.editor.isVisible()
        if hide_editor:
            self.editor.hide()
        wait = int(delay * 1000)
        if hide_editor or tray:
            wait = max(wait, 300)  # let the window / menu vanish from the screen
        self._pending = QTimer(singleShot=True)
        self._pending.timeout.connect(lambda: self._fire(mode, hide_editor))
        self._pending.start(wait)
        self._countdown_left = int(round(delay))
        if self._countdown_left >= 1:
            self._show_countdown()
            self._tick.start(1000)

    def _show_countdown(self):
        n = self._countdown_left
        self.tray.setIcon(icons.countdown_icon(n))
        self.tray.setToolTip(f"Leafshot: capturing in {n} s")
        self.a_cancel_delay.setVisible(True)

    def _on_tick(self):
        self._countdown_left -= 1
        if self._countdown_left >= 1:
            self._show_countdown()
        else:
            self._tick.stop()

    def _end_countdown(self):
        self._tick.stop()
        self._pending = None
        self.tray.setIcon(icons.app_icon())
        self.tray.setToolTip(f"{ipc.APP_TITLE} - screenshots")
        self.a_cancel_delay.setVisible(False)

    def _fire(self, mode, restore):
        self._end_countdown()
        self._do_capture(mode, restore=restore)

    def cancel_delayed(self):
        if self._pending is not None:
            self._pending.stop()
            self._end_countdown()
            self.editor.status("Delayed capture cancelled")

    def set_delay(self, seconds):
        self.settings.set("capture_delay", int(seconds))
        for s_, act in self.delay_actions.items():
            act.setChecked(s_ == int(seconds))
        self.editor.update_delay_ui()

    def _do_capture(self, mode, restore=False):
        grab = capture.Grab()
        if mode == "full":
            self._got(grab, grab.virtual, restore)
        elif mode == "screen":
            self._got(grab, grab.screen_at(QCursor.pos()), restore)
        elif mode == "window":
            r = capture.active_window_logical(grab)
            if r is None:
                self._start_overlay(grab, restore)  # fall back to interactive
            else:
                self._got(grab, r, restore)
        elif mode == "last":
            r = self._last_region()
            if r is None:
                self._start_overlay(grab, restore)
            else:
                self._got(grab, r, restore)
        else:
            self._start_overlay(grab, restore)

    def _start_overlay(self, grab, restore):
        try:
            wins = capture.window_rects_logical(grab)
        except Exception:  # never let window detection break capturing
            wins = []
        self.overlay = capture.Overlay(grab, wins)

        def done(rect):
            self.overlay = None
            if rect is None:
                if restore:
                    self.editor.bring_up()
                return
            self._got(grab, rect, restore)

        self.overlay.done.connect(done)
        self.overlay.start()

    def _last_region(self):
        s = self.settings.get("last_region")
        try:
            x, y, w, h = (int(v) for v in str(s).split(","))
            return QRect(x, y, w, h) if w > 0 and h > 0 else None
        except ValueError:
            return None

    def _got(self, grab, rect, restore=False):
        img = grab.crop(rect)
        if img.isNull():
            if restore:
                self.editor.bring_up()
            return
        self.settings.set("last_region", f"{rect.x()},{rect.y()},{rect.width()},{rect.height()}")
        doc = self.editor.add_document(img, time.strftime("%H:%M:%S"))
        if self.settings.get("autosave"):
            self.editor.save_doc(doc, quiet=True)
        if self.settings.get("copy_on_capture"):
            self.editor.copy_image(doc)
        if self.settings.get("show_editor_on_capture") or restore:
            self.editor.bring_up()
        else:
            self.tray_message("Screenshot taken", "Click the tray icon to annotate it.")

    # -- settings / quit ------------------------------------------------------------------------
    def show_settings(self):
        if SettingsDialog(self.settings, self.editor).exec():
            self.apply_tray_setting()

    def quit(self, discard=False, interactive=True):
        """discard=True: Ctrl+Shift+Q. Otherwise the 'When quitting' setting decides:
        keep = silent, everything comes back next start
        fresh = silent, next start is empty
        ask = ask what to do (only if there are open screenshots)"""
        ed = self.editor
        mode = "fresh" if discard else self.settings.get("quit_mode")
        if not interactive and not discard:
            mode = "keep"  # 'leafshot quit' from the installer/updates: never lose anything
        if mode == "ask" and ed.docs:
            ed.bring_up()
            box = QMessageBox(ed)
            box.setWindowTitle("Quit Leafshot")
            box.setText(f"{len(ed.docs)} screenshot(s) open. What should happen with them?")
            b_keep = box.addButton("Keep for next time", QMessageBox.ButtonRole.AcceptRole)
            b_save = None
            if ed.unsaved_docs():
                b_save = box.addButton("Save unsaved as images && start fresh",
                                       QMessageBox.ButtonRole.ActionRole)
            b_fresh = box.addButton("Discard && start fresh",
                                    QMessageBox.ButtonRole.DestructiveRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(b_keep)
            box.exec()
            clicked = box.clickedButton()
            if clicked is b_keep:
                mode = "keep"
            elif b_save is not None and clicked is b_save:
                for d in ed.unsaved_docs():
                    if not ed.save_doc(d, quiet=True):
                        return
                mode = "fresh"
            elif clicked is b_fresh:
                mode = "fresh"
            else:
                return
        if mode == "fresh":
            ed.store.clear()
        else:
            ed.flush_session(complete=True)
        self.settings.set("window_geometry", self.editor.saveGeometry())
        self.server.close()
        self.tray.hide()
        self.qapp.quit()


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("Leafshot settings")
        form = QFormLayout(self)
        self.dir = QLineEdit(settings.get("save_dir"))
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.dir)
        h.addWidget(browse)
        form.addRow("Save folder", row)
        self.pattern = QLineEdit(settings.get("filename_pattern"))
        self.pattern.setToolTip("strftime pattern, e.g. Screenshot %Y-%m-%d %H-%M-%S")
        form.addRow("File name", self.pattern)
        self.fmt = QComboBox()
        self.fmt.addItems(["png", "jpg"])
        self.fmt.setCurrentText(settings.get("format"))
        form.addRow("Format", self.fmt)
        self.autosave = QCheckBox("Save every capture automatically")
        self.autosave.setChecked(settings.get("autosave"))
        form.addRow(self.autosave)
        self.copy = QCheckBox("Copy every capture to the clipboard")
        self.copy.setChecked(settings.get("copy_on_capture"))
        form.addRow(self.copy)
        self.show_ed = QCheckBox("Open the editor after each capture")
        self.show_ed.setChecked(settings.get("show_editor_on_capture"))
        form.addRow(self.show_ed)
        self.quit_mode = QComboBox()
        for key, label in (("keep", "Quit without asking, open screenshots come back next start"),
                           ("fresh", "Quit without asking, next start is empty"),
                           ("ask", "Ask what to do")):
            self.quit_mode.addItem(label, key)
        self.quit_mode.setCurrentIndex(max(0, self.quit_mode.findData(settings.get("quit_mode"))))
        self.quit_mode.setToolTip("Ctrl+Shift+Q always quits without saving. After a crash or "
                                  "power loss your screenshots always come back.")
        self.proj = QCheckBox("When saving an image, also save an editable .leafshot next to it")
        self.proj.setChecked(settings.get("save_project_with_image"))
        form.addRow(self.proj)
        sep = QLabel("<br><b>Background &amp; tray</b>")
        form.addRow(sep)
        self.show_tray = QCheckBox("Show Leafshot in the system tray")
        self.show_tray.setChecked(settings.get("show_tray"))
        form.addRow(self.show_tray)
        self.close_action = QComboBox()
        self.close_action.addItem("Keep running in the background (instant hotkey)", "background")
        self.close_action.addItem("Quit Leafshot", "quit")
        self.close_action.setCurrentIndex(
            max(0, self.close_action.findData(settings.get("close_action"))))
        form.addRow("Closing the window", self.close_action)
        form.addRow("When quitting", self.quit_mode)
        self.autostart = QCheckBox("Start Leafshot when I log in")
        self.autostart.setChecked(autostart_enabled())
        form.addRow(self.autostart)
        hint = QLabel("Without the tray icon, open Leafshot with your screenshot hotkey or "
                      "from the menu.\nIf it quits on close and doesn't start at login, the "
                      "first hotkey press takes about a second.")
        hint.setEnabled(False)
        form.addRow(hint)
        sep2 = QLabel("<br><b>Editor</b>")
        form.addRow(sep2)
        self.keep_tool = QCheckBox("Keep the drawing tool active after drawing "
                                   "(off = back to Select)")
        self.keep_tool.setChecked(settings.get("keep_tool"))
        form.addRow(self.keep_tool)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                              QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
        self.resize(520, 0)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save folder", self.dir.text())
        if d:
            self.dir.setText(d)

    def _ok(self):
        self.s.set("save_dir", self.dir.text().strip() or self.s.get("save_dir"))
        self.s.set("filename_pattern", self.pattern.text().strip() or "Screenshot %Y-%m-%d %H-%M-%S")
        self.s.set("format", self.fmt.currentText())
        self.s.set("autosave", self.autosave.isChecked())
        self.s.set("copy_on_capture", self.copy.isChecked())
        self.s.set("show_editor_on_capture", self.show_ed.isChecked())
        self.s.set("quit_mode", self.quit_mode.currentData())
        self.s.set("show_tray", self.show_tray.isChecked())
        self.s.set("close_action", self.close_action.currentData())
        if self.autostart.isChecked() != autostart_enabled():
            set_autostart(self.autostart.isChecked())
        self.s.set("save_project_with_image", self.proj.isChecked())
        ed = self.parent()
        if ed is not None and hasattr(ed, "a_keep_tool"):
            ed.a_keep_tool.setChecked(self.keep_tool.isChecked())  # also saves it
        else:
            self.s.set("keep_tool", self.keep_tool.isChecked())
        self.accept()


AUTOSTART_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                              "autostart", f"{ipc.APP_NAME}.desktop")


def autostart_enabled():
    try:
        with open(AUTOSTART_FILE) as f:
            txt = f.read()
    except OSError:
        return False
    return "Hidden=true" not in txt and "X-GNOME-Autostart-enabled=false" not in txt


def set_autostart(on):
    """Writes the autostart entry; 'off' is kept as Hidden=true so the installer respects it."""
    launcher = os.path.expanduser(f"~/.local/bin/{ipc.APP_NAME}")
    if not os.path.exists(launcher):
        launcher = f"python3 {os.path.abspath(sys.argv[0])}"
    os.makedirs(os.path.dirname(AUTOSTART_FILE), exist_ok=True)
    with open(AUTOSTART_FILE, "w") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Leafshot\n"
                "Comment=Screenshot tool (tray)\n"
                f"Exec={launcher} --background\nIcon={ipc.APP_NAME}\nTerminal=false\n"
                f"X-GNOME-Autostart-enabled={'true' if on else 'false'}\n"
                f"Hidden={'false' if on else 'true'}\nX-GNOME-Autostart-Delay=3\n")


def run_app(first_msg, lock_fd):
    QGuiApplication.setDesktopFileName(ipc.APP_NAME)
    qapp = QApplication(sys.argv[:1])
    qapp.setApplicationName(ipc.APP_NAME)
    qapp.setApplicationDisplayName(ipc.APP_TITLE)
    qapp.setQuitOnLastWindowClosed(False)
    qapp.setWindowIcon(icons.app_icon())
    app = App(qapp, lock_fd)
    QTimer.singleShot(0, lambda: app.handle(first_msg, first=True))
    rc = qapp.exec()
    return rc
