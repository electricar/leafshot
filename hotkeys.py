#!/usr/bin/env python3
"""Bind / unbind Leafshot to the Print keys in Cinnamon (Greenshot-style layout).

  Print          -> leafshot region   (drag a region or click a window)
  Alt+Print      -> leafshot window   (active window)
  Ctrl+Print     -> leafshot full     (all monitors)
  Shift+Print    -> leafshot last     (repeat last region)

Cinnamon's own screenshot bindings on those keys are cleared and their old
values are saved, so `hotkeys.py uninstall` restores them exactly.
"""
import ast
import json
import os
import subprocess
import sys

MEDIA = "org.cinnamon.desktop.keybindings.media-keys"
KB = "org.cinnamon.desktop.keybindings"
CUSTOM_SCHEMA = "org.cinnamon.desktop.keybindings.custom-keybinding"
CUSTOM_PATH = "/org/cinnamon/desktop/keybindings/custom-keybindings/{}/"
BACKUP = os.path.expanduser("~/.local/share/leafshot/hotkeys-backup.json")

BINDINGS = [
    # id, name, command arg, key, cinnamon key that must give it up
    ("leafshot_region", "Leafshot: region / window", "region", "Print", "screenshot"),
    ("leafshot_window", "Leafshot: active window", "window", "<Alt>Print", "window-screenshot"),
    ("leafshot_full", "Leafshot: all monitors", "full", "<Control>Print", "screenshot-clip"),
    ("leafshot_last", "Leafshot: repeat last region", "last", "<Shift>Print", "area-screenshot"),
]


def gs(*args, check=True):
    r = subprocess.run(["gsettings", *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"gsettings {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def parse_list(s):
    s = s.strip()
    if s.startswith("@as"):
        s = s[3:].strip()
    try:
        v = ast.literal_eval(s)
        return list(v) if isinstance(v, (list, tuple)) else []
    except (ValueError, SyntaxError):
        return []


def fmt_list(lst):
    return "[" + ", ".join("'" + x.replace("'", "\\'") + "'" for x in lst) + "]"


def have_schema(schema):
    return schema in gs("list-schemas", check=False).split()


def install(cmd_path):
    if not have_schema(KB):
        print("Cinnamon keybinding settings not found - set the hotkey manually "
              "(System Settings > Keyboard > Shortcuts > Custom).")
        return 1
    media_keys = set(gs("list-keys", MEDIA, check=False).split()) if have_schema(MEDIA) else set()
    backup = {}
    if os.path.exists(BACKUP):
        with open(BACKUP) as f:
            backup = json.load(f)
    for _, _, _, _, ckey in BINDINGS:
        if ckey in media_keys:
            if ckey not in backup:
                backup[ckey] = gs("get", MEDIA, ckey)
            gs("set", MEDIA, ckey, "[]")
    os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
    with open(BACKUP, "w") as f:
        json.dump(backup, f, indent=1)

    custom = parse_list(gs("get", KB, "custom-list"))
    for bid, name, arg, key, _ in BINDINGS:
        rel = f"{CUSTOM_SCHEMA}:{CUSTOM_PATH.format(bid)}"
        gs("set", rel, "name", name)
        gs("set", rel, "command", f"{cmd_path} {arg}")
        gs("set", rel, "binding", fmt_list([key]))
        if bid not in custom:
            # keep Cinnamon's '__dummy__' placeholder last, if present
            if "__dummy__" in custom:
                custom.insert(custom.index("__dummy__"), bid)
            else:
                custom.append(bid)
    gs("set", KB, "custom-list", fmt_list(custom))
    print("Hotkeys set: Print = region/window, Alt+Print = active window, "
          "Ctrl+Print = all monitors, Shift+Print = repeat last region")
    return 0


def uninstall():
    if not have_schema(KB):
        return 0
    custom = parse_list(gs("get", KB, "custom-list"))
    ids = [b[0] for b in BINDINGS]
    gs("set", KB, "custom-list", fmt_list([c for c in custom if c not in ids]))
    for bid in ids:
        subprocess.run(["dconf", "reset", "-f", CUSTOM_PATH.format(bid)], capture_output=True)
    if os.path.exists(BACKUP):
        with open(BACKUP) as f:
            backup = json.load(f)
        for ckey, val in backup.items():
            gs("set", MEDIA, ckey, val, check=False)
        os.remove(BACKUP)
    else:
        for _, _, _, _, ckey in BINDINGS:
            gs("reset", MEDIA, ckey, check=False)
    print("Hotkeys removed, Cinnamon's screenshot keys restored.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "uninstall":
        sys.exit(uninstall())
    if len(sys.argv) >= 3 and sys.argv[1] == "install":
        sys.exit(install(sys.argv[2]))
    print(__doc__)
    print("usage: hotkeys.py install /path/to/leafshot | hotkeys.py uninstall")
    sys.exit(2)
