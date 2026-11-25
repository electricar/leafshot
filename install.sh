#!/usr/bin/env bash
# Leafshot installer (per-user, no root needed except for the PyQt6 package).
#   ./install.sh               install + ask about the Print-key hotkeys
#   ./install.sh --hotkeys     install + set hotkeys without asking
#   ./install.sh --no-hotkeys  install only
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/.local/share/leafshot"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/256x256/apps"
AUTOSTART="$HOME/.config/autostart"
HOTKEYS="ask"
for a in "$@"; do
  case "$a" in
    --hotkeys) HOTKEYS="yes" ;;
    --no-hotkeys) HOTKEYS="no" ;;
  esac
done

echo "== Leafshot installer =="

if ! python3 -c "import PyQt6.QtWidgets, PyQt6.QtNetwork" 2>/dev/null; then
  echo "PyQt6 is missing. Installing it (needs your password)…"
  if command -v apt >/dev/null; then sudo apt install -y python3-pyqt6
  elif command -v dnf >/dev/null; then sudo dnf install -y python3-pyqt6
  elif command -v pacman >/dev/null; then sudo pacman -S --needed --noconfirm python-pyqt6
  elif command -v zypper >/dev/null; then sudo zypper install -y python3-PyQt6
  else echo "Please install PyQt6 for Python 3 with your package manager, then run this again."; exit 1
  fi
fi

# Wayland: Qt's Wayland plugin (without it Leafshot still works, through XWayland)
if [ -n "${WAYLAND_DISPLAY:-}" ] && ! ls /usr/lib*/qt6/plugins/platforms/libqwayland* \
     /usr/lib/*/qt6/plugins/platforms/libqwayland* >/dev/null 2>&1; then
  echo "Tip: for native Wayland support install Qt's Wayland plugin"
  echo "     (qt6-wayland on Debian/Ubuntu/Mint, qt6-qtwayland on Fedora, qt6-wayland on Arch)."
fi

# stop a running instance so the new code is used
if [ -x "$BIN/leafshot" ]; then "$BIN/leafshot" quit >/dev/null 2>&1 || true; sleep 0.5; fi

mkdir -p "$DEST" "$BIN" "$APPS" "$ICONS" "$AUTOSTART"
rm -rf "$DEST/leafshot"
cp -r "$SRC/leafshot" "$DEST/"
cp "$SRC/run.py" "$SRC/hotkeys.py" "$DEST/"
cp "$SRC/assets/leafshot.png" "$ICONS/leafshot.png"
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"
cp "$SRC/assets/leafshot.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/leafshot.svg"
find "$DEST" -name "__pycache__" -type d -prune -exec rm -rf {} +

cat > "$BIN/leafshot" <<EOF
#!/bin/sh
exec python3 "$DEST/run.py" "\$@"
EOF
chmod +x "$BIN/leafshot"

cat > "$APPS/leafshot.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Leafshot
GenericName=Screenshot annotation
Comment=Take screenshots and annotate them (arrows, text, steps, pixelate…)
Exec=$BIN/leafshot open %F
Icon=leafshot
MimeType=application/x-leafshot;image/png;image/jpeg;image/webp;image/bmp;
Terminal=false
Categories=Graphics;Utility;
Keywords=screenshot;annotate;snip;greenshot;capture;
StartupWMClass=leafshot
Actions=region;window;full;

[Desktop Action region]
Name=Capture region
Exec=$BIN/leafshot region

[Desktop Action window]
Name=Capture active window
Exec=$BIN/leafshot window

[Desktop Action full]
Name=Capture all monitors
Exec=$BIN/leafshot full
EOF

if grep -q "Hidden=true" "$AUTOSTART/leafshot.desktop" 2>/dev/null; then
  : # you switched "Start at login" off in Settings - leave it off
else
cat > "$AUTOSTART/leafshot.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Leafshot
Comment=Screenshot tool (tray)
Exec=$BIN/leafshot --background
Icon=leafshot
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
EOF
fi

# .leafshot files: own file type, opened by double-click
MIME="$HOME/.local/share/mime"
mkdir -p "$MIME/packages"
cat > "$MIME/packages/leafshot.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-leafshot">
    <comment>Leafshot screenshot (editable annotations)</comment>
    <sub-class-of type="application/zip"/>
    <glob pattern="*.leafshot" weight="80"/>
    <icon name="leafshot"/>
  </mime-type>
</mime-info>
EOF
update-mime-database "$MIME" >/dev/null 2>&1 || true
update-desktop-database "$APPS" >/dev/null 2>&1 || true
xdg-mime default leafshot.desktop application/x-leafshot >/dev/null 2>&1 || true
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

if [ "$HOTKEYS" = "ask" ]; then
  echo
  echo "Bind Leafshot to the Print keys? (Cinnamon's own screenshot keys get"
  echo "moved aside and are restored by ./uninstall.sh)"
  echo "  Print = region/window · Alt+Print = active window"
  echo "  Ctrl+Print = all monitors · Shift+Print = repeat last region"
  read -r -p "Set hotkeys? [Y/n] " ans
  case "${ans:-y}" in [nN]*) HOTKEYS="no" ;; *) HOTKEYS="yes" ;; esac
fi
if [ "$HOTKEYS" = "yes" ]; then
  python3 "$DEST/hotkeys.py" install "$BIN/leafshot" || echo "Hotkey setup failed - set it in System Settings > Keyboard > Shortcuts."
fi

# start it now (tray)
nohup "$BIN/leafshot" --background >/dev/null 2>&1 &

echo
echo "Done. Leafshot is running in the tray and starts with your session."
echo "Press Print to try it. Help: Leafshot window > Help > Keyboard shortcuts."
