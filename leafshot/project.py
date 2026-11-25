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
import shutil
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


def write_project(doc, path, include_preview=True):
    meta = {
        "format": FORMAT,
        "app": "leafshot",
        "title": doc.title,
        "image_path": doc.path,
        "created": doc.created,
        "saved": time.time(),
        "modified": doc.is_modified(),
    }
    states = I.states_to_json_ready(doc_annotation_states(doc))
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr("meta.json", json.dumps(meta, indent=1))
        # PNG is already compressed - store it as is
        z.writestr(zipfile.ZipInfo("background.png"), _png_bytes(doc.background),
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("annotations.json", json.dumps(states), compress_type=zipfile.ZIP_DEFLATED)
        if include_preview:
            prev = doc.render_image().scaled(
                320, 320, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            z.writestr(zipfile.ZipInfo("preview.png"), _png_bytes(prev),
                       compress_type=zipfile.ZIP_STORED)
    os.replace(tmp, path)  # atomic: never leaves a half-written project behind


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
    """Keeps every open screenshot on disk so nothing is lost on quit, crash or logout."""

    def __init__(self):
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        self.dir = os.path.join(base, "leafshot", "session")
        os.makedirs(self.dir, exist_ok=True)

    def path_for(self, doc):
        if not getattr(doc, "uid", None):
            doc.uid = uuid.uuid4().hex
        return os.path.join(self.dir, doc.uid + EXT)

    def save(self, doc):
        try:
            write_project(doc, self.path_for(doc), include_preview=False)
        except OSError:
            pass

    def remove(self, doc):
        try:
            os.remove(self.path_for(doc))
        except OSError:
            pass

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
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir, exist_ok=True)
