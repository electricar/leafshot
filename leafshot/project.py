""".leafshot project files and the auto-saved session.

A .leafshot file is a zip:
    meta.json         title, original image path, timestamps, format version
    background.png    the untouched screenshot (after crops)
    annotations.json  every annotation object (same state dicts as copy/paste)
    preview.png       small flattened preview (handy for file managers / later)

The screenshot is kept *without* annotations, so pixelated or covered areas can
still be un-hidden in the project. Only ever share the exported PNG/JPG, never
the .leafshot file, if it contains hidden secrets.
"""
import json
import os
import queue
import shutil
import threading
import time
import uuid
import zipfile

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QImage

from . import items as I
from .document import is_annot

FORMAT = 1
EXT = ".leafshot"


def _png_bytes(img):
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def doc_annotation_states(doc):
    its = sorted((it for it in doc.annotations() if is_annot(it)), key=lambda i: i.zValue())
    return [it.get_state() for it in its]


# The screenshot itself never changes while you annotate (a crop makes a new image),
# so its PNG is encoded once and reused for every later save of that screenshot.
_PNG_CACHE = {}  # QImage.cacheKey() -> PNG bytes
_PNG_LOCK = threading.Lock()


def background_png(img):
    key = img.cacheKey()
    with _PNG_LOCK:
        data = _PNG_CACHE.get(key)
    if data is None:
        data = _png_bytes(img)
        with _PNG_LOCK:
            if len(_PNG_CACHE) > 24:
                _PNG_CACHE.pop(next(iter(_PNG_CACHE)))
            _PNG_CACHE[key] = data
    return data


def _meta(doc):
    return {
        "format": FORMAT,
        "app": "leafshot",
        "title": doc.title,
        "image_path": doc.path,
        "created": doc.created,
        "saved": time.time(),
        "modified": doc.is_modified(),
    }


def _write_zip(path, meta, background, states_json, preview_png=None):
    """background: QImage (PNG-encoded via the cache) or ready PNG bytes."""
    bg = background if isinstance(background, (bytes, bytearray)) else background_png(background)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr("meta.json", json.dumps(meta, indent=1))
        # PNG is already compressed - store it as is
        z.writestr(zipfile.ZipInfo("background.png"), bg, compress_type=zipfile.ZIP_STORED)
        z.writestr("annotations.json", states_json, compress_type=zipfile.ZIP_DEFLATED)
        if preview_png is not None:
            z.writestr(zipfile.ZipInfo("preview.png"), preview_png,
                       compress_type=zipfile.ZIP_STORED)
    os.replace(tmp, path)  # atomic: never leaves a half-written project behind


def write_project(doc, path, include_preview=True):
    states = json.dumps(I.states_to_json_ready(doc_annotation_states(doc)))
    preview = None
    if include_preview:
        prev = doc.render_image().scaled(
            320, 320, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        preview = _png_bytes(prev)
    _write_zip(path, _meta(doc), doc.background, states, preview)


def read_project(path):
    """Returns (QImage background, list of item states, meta dict)."""
    with zipfile.ZipFile(path) as z:
        meta = json.loads(z.read("meta.json"))
        img = QImage()
        img.loadFromData(z.read("background.png"), "PNG")
        states = I.states_from_json(json.loads(z.read("annotations.json")))
    if img.isNull():
        raise ValueError("background image missing or damaged")
    return img, states, meta


def is_project(path):
    return path.lower().endswith(EXT)


# ---------------------------------------------------------------------------
class SessionStore:
    """Keeps every open screenshot on disk so nothing is lost on quit, crash or logout.

    Saving runs on a background thread, so annotating never waits for the disk or
    for PNG encoding. Jobs run in order, so a 'remove' can't be undone by an older save.
    """

    def __init__(self):
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        self.dir = os.path.join(base, "leafshot", "session")
        os.makedirs(self.dir, exist_ok=True)
        self._q = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="leafshot-session", daemon=True)
        self._worker.start()

    def path_for(self, doc):
        if not getattr(doc, "uid", None):
            doc.uid = uuid.uuid4().hex
        return os.path.join(self.dir, doc.uid + EXT)

    def save(self, doc):
        # snapshot on the UI thread (cheap); encoding + writing happen in the background
        try:
            states = json.dumps(I.states_to_json_ready(doc_annotation_states(doc)))
        except Exception:  # never let a save break annotating
            return
        self._q.put(("save", self.path_for(doc), _meta(doc), QImage(doc.background), states))

    def remove(self, doc):
        self._q.put(("remove", self.path_for(doc)))

    def wait(self):
        """Block until every queued save/remove is on disk (quit, tests)."""
        self._q.join()

    def _run(self):
        while True:
            job = self._q.get()
            try:
                if job[0] == "save":
                    _, path, meta, bg, states = job
                    _write_zip(path, meta, bg, states)
                elif job[0] == "remove":
                    try:
                        os.remove(job[1])
                    except FileNotFoundError:
                        pass
            except Exception:  # a failed write must not kill the worker
                pass
            finally:
                self._q.task_done()

    def load_all(self):
        out = []
        for name in os.listdir(self.dir):
            if not name.endswith(EXT):
                continue
            p = os.path.join(self.dir, name)
            try:
                img, states, meta = read_project(p)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile):
                continue
            out.append((meta.get("created", 0), name[:-len(EXT)], img, states, meta))
        out.sort(key=lambda t: t[0])
        return out

    def clear(self):
        self.wait()
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir, exist_ok=True)
