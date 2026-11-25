"""Images people upload for their look (B-10), and where they live.

What arrives is never stored. Pillow reads it, from a short list of formats
recognised by the content and not by the name; it must stay under a byte and a
pixel limit (a small file can unpack into gigabytes: a decompression bomb);
then it is drawn onto a fresh image, which leaves EXIF, ICC and XMP behind,
and written as WebP under a name the server makes up.

The share is `settings.CUSTOM_IMAGES_DIR`, outside `static/` and without a URL
of its own; `looks.views.image` hands a file to its owner. Every path is
matched against `PATH` and resolved against the base folder before it is
touched, so a row that somebody managed to change still cannot reach out of it.
"""

import io
import os
import re
import uuid
import warnings
from pathlib import Path

from django.conf import settings
from django.utils.translation import gettext_lazy as _
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 25_000_000
FORMATS = ("PNG", "JPEG", "WEBP")

MAT = "mat"
SLEEVE = "sleeve"
KINDS = (MAT, SLEEVE)
#: The mat keeps its shape and shrinks to fit; the sleeve is cut to a card.
MAT_BOX = (1920, 1920)
SLEEVE_SIZE = (744, 1039)
QUALITY = 82

PATH = re.compile(r"[0-9a-f]{32}/[0-9a-f]{32}\.webp")


class ImageRefused(ValueError):
    """An upload that is not stored; the message is for the person."""


def base() -> Path:
    return Path(settings.CUSTOM_IMAGES_DIR).resolve()


def resolve(relative: str) -> Path:
    """The file for a stored path, inside the share or ValueError."""
    if not PATH.fullmatch(relative or ""):
        raise ValueError("not a stored image path")
    root = base()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("outside the images folder")
    return path


def _read(upload) -> bytes:
    if upload.size is not None and upload.size > MAX_BYTES:
        raise ImageRefused(_("The image is larger than 5 MB."))
    data = upload.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ImageRefused(_("The image is larger than 5 MB."))
    return data


def process(upload, kind: str) -> bytes:
    """The upload as clean WebP bytes, or ImageRefused."""
    if kind not in KINDS:
        raise ValueError(kind)
    data = _read(upload)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            picture = Image.open(io.BytesIO(data), formats=FORMATS)
            if picture.width * picture.height > MAX_PIXELS:
                raise ImageRefused(_("The image has more than 25 megapixels."))
            picture.load()
            picture = ImageOps.exif_transpose(picture)
    except ImageRefused:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise ImageRefused(_("The image has more than 25 megapixels.")) from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ImageRefused(_("Upload a PNG, JPEG or WebP image.")) from error

    alpha = "A" in picture.getbands() or "transparency" in picture.info
    picture = picture.convert("RGBA" if alpha else "RGB")
    if kind == MAT:
        picture.thumbnail(MAT_BOX, Image.Resampling.LANCZOS)
    else:
        picture = ImageOps.fit(picture, SLEEVE_SIZE, Image.Resampling.LANCZOS)
    clean = Image.new(picture.mode, picture.size)
    clean.paste(picture)
    out = io.BytesIO()
    clean.save(out, "WEBP", quality=QUALITY, method=4)
    return out.getvalue()


def write(look_id: uuid.UUID, data: bytes) -> str:
    """Store WebP bytes for a look; returns the new relative path."""
    relative = f"{look_id.hex}/{uuid.uuid4().hex}.webp"
    path = resolve(relative)
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    with open(partial, "wb") as handle:
        handle.write(data)
    os.chmod(partial, 0o640)
    os.replace(partial, path)
    return relative


def remove(relative: str) -> None:
    """Delete a stored image, and its look's folder once it is empty."""
    try:
        path = resolve(relative)
    except ValueError:
        return
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass
