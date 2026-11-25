#!/usr/bin/env python3
"""Leafshot launcher.

Fast path: if an instance is already running, hand the command over a Unix
socket and exit without importing Qt (keeps the hotkey snappy).
Otherwise start the full application.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from leafshot import ipc  # noqa: E402  (pure Python, no Qt)


def main():
    try:
        msg = ipc.parse_args(sys.argv[1:])
    except SystemExit:
        raise
    if ipc.send(msg):
        return 0
    lock = ipc.acquire_startup_lock()
    if lock is None:
        # Another instance is starting right now - wait for it, then hand over.
        if ipc.send(msg, retries=40):
            return 0
        print("leafshot: another instance seems stuck starting up", file=sys.stderr)
        return 1
    # Re-check: an instance may have come up between our first try and the lock.
    if ipc.send(msg):
        return 0
    from leafshot.app import run_app
    return run_app(msg, lock)


if __name__ == "__main__":
    sys.exit(main())
