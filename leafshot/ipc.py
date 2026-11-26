"""Single-instance plumbing without Qt (imported by the fast launcher)."""
import argparse
import fcntl
import json
import os
import socket
import time

APP_NAME = "leafshot"
APP_TITLE = "Leafshot"
VERSION = "0.5.0b3"

COMMANDS = ("region", "window", "full", "screen", "last", "show", "open", "quit")


def runtime_dir():
    d = os.environ.get("XDG_RUNTIME_DIR")
    if not d or not os.path.isdir(d):
        d = os.path.join("/tmp", f"{APP_NAME}-{os.getuid()}")
        os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def socket_path():
    return os.path.join(runtime_dir(), f"{APP_NAME}.sock")


def lock_path():
    return os.path.join(runtime_dir(), f"{APP_NAME}.lock")


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog=APP_NAME,
        description=f"{APP_TITLE} - screenshot + annotation tool (single instance, tabs).",
    )
    p.add_argument(
        "command", nargs="?", default="show", choices=COMMANDS,
        help="region (default hotkey), window (active window), full (all monitors), "
             "screen (monitor under mouse), last (repeat last region), show, "
             "open FILE..., quit",
    )
    p.add_argument("files", nargs="*", help="image files for 'open'")
    p.add_argument("--delay", type=float, default=0.0, help="seconds to wait before capturing")
    p.add_argument("--background", action="store_true",
                   help="start into the tray without opening the editor (autostart)")
    p.add_argument("--version", action="version", version=f"{APP_TITLE} {VERSION}")
    a = p.parse_args(argv)
    files = [os.path.abspath(f) for f in a.files]
    return {"cmd": a.command, "files": files, "delay": max(0.0, a.delay),
            "background": a.background}


def send(msg, retries=1):
    """Send msg to a running instance. Returns True if delivered."""
    data = (json.dumps(msg) + "\n").encode()
    for i in range(retries):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(2.0)
            s.connect(socket_path())
            s.sendall(data)
            return True
        except OSError:
            if i + 1 < retries:
                time.sleep(0.1)
        finally:
            s.close()
    return False


def acquire_startup_lock():
    """Exclusive lock held by the running instance for its whole lifetime."""
    fd = os.open(lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd
