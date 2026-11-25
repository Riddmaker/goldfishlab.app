"""Changing a look and its images (B-10)."""

from django.db import transaction

from looks import images, presets
from looks.models import Look


def default_look(user) -> Look | None:
    """The account's look, or None: the standard look."""
    if not user.is_authenticated:
        return None
    return Look.objects.filter(owner=user, is_default=True).first()


def _store(look: Look, kind: str, data: bytes) -> str:
    """Write processed bytes and point the look at them; the old file goes
    after the commit, the new one at once if the row cannot be saved."""
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


def set_image(look: Look, kind: str, upload) -> str:
    """Process an upload and make it the look's mat or sleeve.

    Raises `images.ImageRefused` with a message for the person; then nothing
    is written and the look is unchanged.
    """
    return _store(look, kind, images.process(upload, kind))


def clear_image(look: Look, kind: str) -> None:
    """Drop the uploaded mat or sleeve; the file goes after the commit."""
    field = f"{kind}_image"
    old = getattr(look, field)
    if not old:
        return
    setattr(look, field, "")
    look.save()
    transaction.on_commit(lambda: images.remove(old))


@transaction.atomic
def save(user, data: dict) -> Look:
    """Store the Customisation form's `cleaned_data` as the account's look.

    Both uploads are processed before anything is written, so a refused image
    (`images.ImageRefused`) leaves the look and the share as they were.
    """
    processed = {kind: images.process(data[f"{kind}_file"], kind)
                 for kind in images.KINDS if data.get(f"{kind}_file")}
    look = default_look(user) or Look(owner=user, is_default=True)
    look.mat_preset, look.sleeve_preset = data["mat"], data["sleeve"]
    look.inner_sleeve, look.inner_colour = data["inner_sleeve"], data["inner_colour"]
    look.seam, look.seam_colour = data["seam"], data["seam_colour"]
    look.save()
    for kind in images.KINDS:
        if kind in processed:
            _store(look, kind, processed[kind])
        elif data[kind] != presets.OWN:
            clear_image(look, kind)
    return look


@transaction.atomic
def reset(user) -> None:
    """Back to the standard look: the row and its files go."""
    Look.objects.filter(owner=user, is_default=True).delete()
