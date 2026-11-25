"""Persistent settings (~/.config/leafshot/leafshot.conf)."""
import os

from PyQt6.QtCore import QSettings, QStandardPaths
from PyQt6.QtGui import QColor

from .ipc import APP_NAME


def _default_save_dir():
    pics = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
    return os.path.join(pics or os.path.expanduser("~"), "Screenshots")


DEFAULTS = {
    "save_dir": None,  # resolved lazily
    "filename_pattern": "Screenshot %Y-%m-%d %H-%M-%S",
    "format": "png",
    "jpg_quality": 92,
    "autosave": False,
    "copy_on_capture": False,
    "show_editor_on_capture": True,
    "keep_tool": False,
    "capture_delay": 0,  # seconds, applies to every capture incl. hotkeys
    "close_action": "background",  # background | quit
    "show_tray": True,
    "quit_mode": "keep",  # keep | fresh | ask
    "save_project_with_image": False,
    "last_region": "",  # "x,y,w,h"
    "window_geometry": None,
}


STYLE_DEFAULTS = {
    "stroke": "#ffe53935",
    "fill": "",
    "border": "",
    "width": 3,
    "font_size": 20,
    "font_family": "Sans",
    "bold": True,
    "italic": False,
    "underline": False,
    "align": "left",
    "shadow": False,
    "pixel_size": 10,
}

TOOL_STYLE_OVERRIDES = {
    "highlighter": {"stroke": "#fffff176", "width": 4},
    "pixelate": {"shadow": False},
    "text": {"shadow": False, "width": 2},
}


class Settings:
    def __init__(self):
        self.q = QSettings(APP_NAME, APP_NAME)

    # per-tool style memory (like Greenshot: each tool remembers its look)
    def tool_style(self, tool, key):
        default = TOOL_STYLE_OVERRIDES.get(tool, {}).get(key, STYLE_DEFAULTS[key])
        v = self.q.value(f"style/{tool}/{key}", default)
        if key in ("stroke", "fill", "border"):
            return QColor(v) if v else QColor()
        if key in ("bold", "italic", "underline", "shadow"):
            return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
        if key == "font_family":
            return str(v) if v else "Sans"
        if key == "align":
            return str(v) if v in ("left", "center", "right") else "left"
        try:
            return int(v)
        except (TypeError, ValueError):
            return int(default)

    def recent_fonts(self):
        v = self.q.value("recent_fonts", "")
        if isinstance(v, (list, tuple)):
            v = "\n".join(v)
        return [f for f in str(v).split("\n") if f][:5]

    def add_recent_font(self, family):
        rec = [family] + [f for f in self.recent_fonts() if f != family]
        self.q.setValue("recent_fonts", "\n".join(rec[:5]))

    def set_tool_style(self, tool, key, value):
        if key in ("stroke", "fill", "border"):
            value = value.name(QColor.NameFormat.HexArgb) if value is not None and value.isValid() else ""
        self.q.setValue(f"style/{tool}/{key}", value)

    def get(self, key):
        default = DEFAULTS.get(key)
        if key == "save_dir" and default is None:
            default = _default_save_dir()
        v = self.q.value(key, default)
        # QSettings returns strings for ini values - coerce by default's type.
        if isinstance(default, bool):
            return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
        if isinstance(default, int):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        return v

    def set(self, key, value):
        self.q.setValue(key, value)

    def color(self, key):
        v = self.get(key)
        return QColor(v) if v else QColor()

    def set_color(self, key, c):
        self.set(key, c.name(QColor.NameFormat.HexArgb) if c.isValid() else "")
