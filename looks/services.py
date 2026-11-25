"""Changing a look's images (B-10)."""

from django.db import transaction

from looks import images
from looks.models import Look


def set_image(look: Look, kind: str, upload) -> str:
    """Process an upload and make it the look's mat or sleeve.

    Raises `images.ImageRefused` with a message for the person. The image it
    replaces is deleted once the change is committed; a failed save deletes
    the new file instead, so the share holds only what a row points at.
    """
    data = images.process(upload, kind)
    field = f"{kind}_image"
    old = getattr(look, field)
    new = images.write(look.pk, data)
    try:
        setattr(look, field, new)
        look.save()
    except Exception:
        images.remove(new)
        raise
    if old:
        transaction.on_commit(lambda: images.remove(old))
    return new


def clear_image(look: Look, kind: str) -> None:
    """Back to the standard mat or sleeve."""
    field = f"{kind}_image"
    old = getattr(look, field)
    if not old:
        return
    setattr(look, field, "")
    look.save()
    transaction.on_commit(lambda: images.remove(old))
