# Testing Leafshot on Wayland (beta)

Thanks for helping test! On Wayland, Leafshot asks your desktop for the screenshot
through the standard screenshot portal (the same way other screenshot apps do),
then shows its own region selection on top.

## Install

```bash
git clone -b wayland https://github.com/electricar/leafshot
cd leafshot
./install.sh
```

(or download the `wayland` branch as a ZIP from GitHub). The installer installs PyQt6 if
it's missing (apt / dnf / pacman / zypper).

**Set a shortcut** (the automatic Print-key setup only exists for Cinnamon):

- **KDE Plasma:** System Settings → Keyboard → Shortcuts → *Add New* → *Command or Script*,
  command `~/.local/bin/leafshot region` (use the full path, e.g. `/home/you/.local/bin/leafshot region`),
  then assign a key. Print is taken by Spectacle by default; remove it there first, or use
  e.g. Meta+Print for testing.
- **GNOME:** Settings → Keyboard → Custom Shortcuts, same command.
- **Cinnamon on Wayland:** `./install.sh` sets Print up as on X11.

Or simply run `leafshot region` in a terminal.

## What to try

1. **First capture:** press your shortcut. Your desktop may ask once whether Leafshot may
   take screenshots: allow it. Does the frozen screen with the magnifier appear?
2. **Region:** drag a rectangle. Does the right part end up in the editor?
3. **Click without dragging:** should capture the whole monitor under the mouse.
4. **Multiple monitors (if you have them):** does every monitor show the dimmed screenshot?
   Does a selection on the second monitor capture the right area?
5. **Scaling:** if you use 125 % / 150 % / 200 %, is the result sharp and the right area?
6. **Other captures:** tray menu → *Capture all monitors*, *Repeat last region*, and the
   ⏱ delay (e.g. 3 s, then open a menu).
7. **Editor:** annotate, copy (Ctrl+Shift+C) and paste into another app, save.

## Known differences on Wayland

- **No "click a window"**: Wayland doesn't let apps see other windows. Alt+Print (window)
  opens the normal picker instead.
- The editor may not jump to the front by itself after a capture (focus protection);
  click its taskbar entry if needed.

## If something goes wrong

Please send:

- Your distro, desktop and version (e.g. Fedora 44, KDE Plasma 6.x), monitors and scaling
- What you did and what happened (a photo/screenshot helps)
- The output of these commands:

```bash
leafshot quit
LEAFSHOT_DEBUG=1 ~/.local/bin/leafshot region      # reproduce the problem, then close Leafshot
echo $XDG_SESSION_TYPE $XDG_CURRENT_DESKTOP
```

**"Your desktop refused the screenshot"** on KDE Plasma 6.4+: check System Settings →
Application Permissions (or reset with
`rm ~/.local/share/flatpak/db/screenshot; systemctl --user restart xdg-permission-store`)
and try again.
