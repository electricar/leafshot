#!/usr/bin/env bash
# Runs Leafshot's Wayland tests on a headless weston with a private D-Bus and a mock portal.
# usage: tests/run_wayland_tests.sh [outputs]   (default: 2 monitors)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OUTS="${1:-2}"
export XDG_RUNTIME_DIR="$(mktemp -d)"; chmod 700 "$XDG_RUNTIME_DIR"
# headless parent compositor + nested weston with N outputs (= N monitors)
weston --backend=headless --width=2000 --height=1200 --socket=wl-parent --idle-time=0 \
       >/tmp/weston-parent.log 2>&1 &
PPID_W=$!
for i in $(seq 50); do [ -S "$XDG_RUNTIME_DIR/wl-parent" ] && break; sleep 0.1; done
SCALE="${SCALE:-1}"
WAYLAND_DISPLAY=wl-parent weston --backend=wayland --output-count="$OUTS" --scale="$SCALE" --width=800 \
       --height=500 --socket=wl-test --idle-time=0 >/tmp/weston.log 2>&1 &
WPID=$!
for i in $(seq 50); do [ -S "$XDG_RUNTIME_DIR/wl-test" ] && break; sleep 0.1; done
export WAYLAND_DISPLAY=wl-test XDG_SESSION_TYPE=wayland QT_QPA_PLATFORM=wayland
unset DISPLAY
dbus-run-session -- bash -c '
  MOCK_W=$((800*'"$OUTS"'*'"$SCALE"')) MOCK_H=$((500*'"$SCALE"')) QT_QPA_PLATFORM=offscreen MOCK_PORTAL_MODE="${MOCK_PORTAL_MODE:-}" python3 "'"$HERE"'/mock_portal.py" > /tmp/mock.log 2>&1 &
  for i in $(seq 50); do grep -q ready /tmp/mock.log 2>/dev/null && break; sleep 0.1; done
  MOCK_W=$((800*'"$OUTS"'*'"$SCALE"')) python3 "'"$HERE"'/test_wayland.py"
'
RC=$?
kill $WPID $PPID_W 2>/dev/null
exit $RC
