#!/usr/bin/env bash
# Removes Leafshot and restores Cinnamon's screenshot hotkeys.
# Your saved screenshots and settings (~/.config/leafshot) are kept.
set -uo pipefail
DEST="$HOME/.local/share/leafshot"
BIN="$HOME/.local/bin/leafshot"

[ -x "$BIN" ] && "$BIN" quit >/dev/null 2>&1
[ -f "$DEST/hotkeys.py" ] && python3 "$DEST/hotkeys.py" uninstall
rm -f "$BIN" "$HOME/.local/share/applications/leafshot.desktop" \
      "$HOME/.config/autostart/leafshot.desktop" \
      "$HOME/.local/share/icons/hicolor/256x256/apps/leafshot.png" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/leafshot.svg"
rm -f "$HOME/.local/share/mime/packages/leafshot.xml"
update-mime-database "$HOME/.local/share/mime" >/dev/null 2>&1 || true
rm -rf "$DEST/leafshot" "$DEST/run.py" "$DEST/hotkeys.py"
echo "Leafshot removed."
echo "Kept: settings (~/.config/leafshot) and open screenshots (~/.local/share/leafshot/session)."
