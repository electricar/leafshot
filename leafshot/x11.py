"""Minimal X11 window geometry queries via ctypes (no extra dependencies).

Used for: highlighting the window under the cursor in the capture overlay and
for `leafshot window` (active window). Everything fails soft: if Xlib or the
EWMH properties are unavailable, functions return [] / None.
"""
import ctypes
import ctypes.util
import os

_c_ulong_p = ctypes.POINTER(ctypes.c_ulong)

_ERROR_HANDLER_T = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


@_ERROR_HANDLER_T
def _ignore_errors(_display, _event):
    return 0


_xlib = None


def _lib():
    global _xlib
    if _xlib is not None:
        return _xlib or None
    try:
        name = ctypes.util.find_library("X11") or "libX11.so.6"
        lib = ctypes.cdll.LoadLibrary(name)
    except OSError:
        _xlib = False
        return None
    lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    lib.XOpenDisplay.restype = ctypes.c_void_p
    lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
    lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    lib.XDefaultRootWindow.restype = ctypes.c_ulong
    lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    lib.XInternAtom.restype = ctypes.c_ulong
    lib.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
        ctypes.c_int, ctypes.c_ulong, _c_ulong_p, ctypes.POINTER(ctypes.c_int),
        _c_ulong_p, _c_ulong_p, ctypes.POINTER(ctypes.c_void_p)]
    lib.XGetWindowProperty.restype = ctypes.c_int
    lib.XFree.argtypes = [ctypes.c_void_p]
    lib.XGetGeometry.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, _c_ulong_p,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
        ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    lib.XGetGeometry.restype = ctypes.c_int
    lib.XTranslateCoordinates.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), _c_ulong_p]
    lib.XTranslateCoordinates.restype = ctypes.c_int
    lib.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    lib.XSetErrorHandler.restype = ctypes.c_void_p
    lib.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _xlib = lib
    return lib


class _Conn:
    def __init__(self):
        self.lib = _lib()
        self.dpy = None
        self.old_handler = None
        if self.lib and os.environ.get("DISPLAY"):
            self.dpy = self.lib.XOpenDisplay(None)
        if self.dpy:
            self.old_handler = self.lib.XSetErrorHandler(
                ctypes.cast(_ignore_errors, ctypes.c_void_p))
            self.root = self.lib.XDefaultRootWindow(self.dpy)
        self._atoms = {}

    def __enter__(self):
        return self if self.dpy else None

    def __exit__(self, *exc):
        if self.dpy:
            self.lib.XSync(self.dpy, 0)
            self.lib.XSetErrorHandler(self.old_handler)
            self.lib.XCloseDisplay(self.dpy)
            self.dpy = None
        return False

    def atom(self, name):
        if name not in self._atoms:
            self._atoms[name] = self.lib.XInternAtom(self.dpy, name.encode(), 0)
        return self._atoms[name]

    def prop32(self, window, name):
        """Read a 32-bit-format property as a list of ints ([] if missing)."""
        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        nitems = ctypes.c_ulong()
        after = ctypes.c_ulong()
        data = ctypes.c_void_p()
        status = self.lib.XGetWindowProperty(
            self.dpy, window, self.atom(name), 0, 4096, 0, 0,  # AnyPropertyType
            ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(nitems), ctypes.byref(after), ctypes.byref(data))
        if status != 0 or not data.value:
            return []
        try:
            if actual_format.value != 32:
                return []
            arr = ctypes.cast(data, _c_ulong_p)  # format 32 is stored as C long
            return [arr[i] for i in range(nitems.value)]
        finally:
            self.lib.XFree(data)

    def frame_rect(self, window):
        """Visible on-screen rect of a client window incl. WM frame, excl. CSD shadow."""
        root = ctypes.c_ulong()
        x, y = ctypes.c_int(), ctypes.c_int()
        w, h, bw, depth = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        if not self.lib.XGetGeometry(self.dpy, window, ctypes.byref(root), ctypes.byref(x),
                                     ctypes.byref(y), ctypes.byref(w), ctypes.byref(h),
                                     ctypes.byref(bw), ctypes.byref(depth)):
            return None
        ax, ay, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        if not self.lib.XTranslateCoordinates(self.dpy, window, self.root, 0, 0,
                                              ctypes.byref(ax), ctypes.byref(ay),
                                              ctypes.byref(child)):
            return None
        rx, ry, rw, rh = ax.value, ay.value, int(w.value), int(h.value)
        fe = self.prop32(window, "_NET_FRAME_EXTENTS")
        if len(fe) == 4:
            l, r, t, b = (int(v) for v in fe)
            rx, ry, rw, rh = rx - l, ry - t, rw + l + r, rh + t + b
        ge = self.prop32(window, "_GTK_FRAME_EXTENTS")
        if len(ge) == 4:
            l, r, t, b = (int(v) for v in ge)
            rx, ry, rw, rh = rx + l, ry + t, rw - l - r, rh - t - b
        if rw <= 0 or rh <= 0:
            return None
        return (rx, ry, rw, rh)

    def is_visible_client(self, window, current_desktop):
        types = self.prop32(window, "_NET_WM_WINDOW_TYPE")
        if self.atom("_NET_WM_WINDOW_TYPE_DESKTOP") in types:
            return False
        if self.atom("_NET_WM_STATE_HIDDEN") in self.prop32(window, "_NET_WM_STATE"):
            return False
        if current_desktop is not None:
            desk = self.prop32(window, "_NET_WM_DESKTOP")
            if desk and desk[0] not in (current_desktop, 0xFFFFFFFF):
                return False
        return True


def window_rects():
    """Visible top-level windows on the current workspace, topmost first."""
    with _Conn() as c:
        if c is None:
            return []
        stack = c.prop32(c.root, "_NET_CLIENT_LIST_STACKING")
        cur = c.prop32(c.root, "_NET_CURRENT_DESKTOP")
        cur = cur[0] if cur else None
        out = []
        for win in reversed(stack):
            if not c.is_visible_client(win, cur):
                continue
            r = c.frame_rect(win)
            if r:
                out.append(r)
        return out
    return []


def active_window_rect():
    with _Conn() as c:
        if c is None:
            return None
        act = c.prop32(c.root, "_NET_ACTIVE_WINDOW")
        if not act or not act[0]:
            return None
        return c.frame_rect(act[0])
    return None
