"""Corrections as patches on the blocks of a reading (card model, phase 2).

A correction used to be a frozen copy: `CardAnnotation.overrides` holds each
primitive it corrects whole, in the reader's JSON shape. Once stored, nothing
the reader learns about that card reaches it - a fixed condition, a better
count - and nobody notices when the reading under it changes.

A patch says only what the person said. Each primitive of a reading is a
*block*: ``land_search``, ``tutor``, ``combat`` - and for a primitive that is a
list, each entry on its own: ``triggers:0``, ``triggers:1``. A patch on a
block is one of:

``set``      these fields read so, the others stay the reading's (``drop``
             names the ones taken away)
``keep``     the block as read, said by a person
``replace``  this whole value - a scalar, a block the reader has not got, or
             what a diff could not say exactly
``off``      (list entries) this entry is not there
``add``      (lists) one more entry, after the read ones

Every patch on a read block keeps the digest of the block it was written
against (``base``), so a later reading can tell "you corrected this, and the
reading under it has changed since" - and "this block is gone".

The patch document also carries the judgements that are not blocks
(priority, tags, kind and the rest of `annotations`) as they are, under
``face``, and each primitive's value as it was said, under ``said``: a
correction whose block is gone - not played, waiting for its owner -
survives a save of another block (`stated`) until its owner keeps or
discards it (`settle`). ``face`` and ``said`` together are the correction
as its owner stated it (`overrides_of`); since card model phase 4 (PR 16b,
version 2) the document is the only place a correction is stored.

`from_overrides` writes a document and checks it: compiled against the same
reading it gives exactly the overrides back, key by key, or that key becomes
a ``replace``. So converting a correction never moves an engine card.
"""

import hashlib
import json
from dataclasses import dataclass

from simulations import primitives

VERSION = 2

#: The patch operations, by where they apply.
SINGLE_OPS = frozenset({"set", "keep", "replace"})
ENTRY_OPS = frozenset({"set", "keep", "off"})
LIST_OPS = frozenset({"replace", "add"})

#: What tells one entry of a list from another when the reading moved.
_ENTRY_KIND = {
    "triggers": ("event",),
    "additional_cost": ("sacrifice", "life", "life_x", "discard", "mana",
                        "exile_from_graveyard"),
}

#: A document's size limits: every key of a primitive, one entry per list
#: item and as many added.
_MOST_PATCHES = 4 * primitives.MAX_ITEMS * len(primitives.KEYS)


class PatchError(ValueError):
    """A patch document the engine cannot store."""


@dataclass(frozen=True)
class Note:
    """A patch whose block is not what it was written against."""

    key: str
    block: str
    #: ``changed``: applied, on a reading that is not the one it was written
    #: against. ``gone``: the block is not read any more; not applied.
    status: str


def digest(value) -> str:
    """A short, stable hash of a JSON value."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def is_list(key: str) -> bool:
    return primitives.BY_KEY[key].many


def reading(key: str, profile):
    """The block(s) of one primitive as read: an object, a scalar, a list or None."""
    value = primitives.reading_of(key, profile)
    if is_list(key):
        return list(value or [])
    return value


def _kind(key: str, entry) -> str:
    """What sort of entry this is, for finding it again (`_ENTRY_KIND`)."""
    if not isinstance(entry, dict):
        return ""
    names = _ENTRY_KIND.get(key, ())
    if key == "triggers":
        return str(entry.get("event", ""))
    return ",".join(name for name in names if entry.get(name))


# --- writing a document ----------------------------------------------------------


def from_overrides(overrides: dict, profile, previous: dict | None = None,
                   fresh=frozenset()) -> dict:
    """The patch document that says what `overrides` says, against this reading.

    Args:
        overrides: The correction, in the stored form.
        profile: The reading it is written against; None for a card without one.
        previous: The document stored before. A primitive whose value did not
            change keeps the patches it had, so saving one block never resets
            the "please check" of another.
        fresh: Primitives written against today's reading whatever `previous`
            says - the ones their owner has just checked (`settle`).

    Returns:
        dict: ``{"v", "said", "face", "blocks"}``.
    """
    overrides = dict(overrides or {})
    kept = _previous_by_key(previous, profile)
    blocks: list[dict] = []
    said: dict = {}
    for key in sorted(primitives.KEYS & set(overrides)):
        value = overrides[key]
        said[key] = value
        before = kept.get(key)
        if key not in fresh and before is not None \
                and (before[1] == value or before[0] == value):
            blocks.extend(before[2])
            continue
        blocks.extend(_exact(key, value, profile))
    return {
        "v": VERSION,
        "said": said,
        "face": {key: value for key, value in overrides.items() if key not in primitives.KEYS},
        "blocks": blocks,
    }


def _previous_by_key(previous: dict | None, profile) -> dict:
    """`{key: (value it compiles to, value it was said with, its patches)}` of a
    stored document. A block that is gone compiles to nothing (`_GONE`); its
    patches are kept by the said value alone."""
    if not is_current(previous):
        return {}
    values, _notes = compile_blocks(previous["blocks"], profile)
    said = previous.get("said") or {}
    found: dict = {}
    for patch in previous["blocks"]:
        key = key_of(patch["id"])
        found.setdefault(key, (values.get(key, _GONE), said.get(key), []))[2].append(patch)
    return found


def _exact(key: str, value, profile) -> list[dict]:
    """The patches for one primitive, checked: a diff that does not compile back
    to `value` exactly becomes one ``replace``."""
    patches = _diff(key, value, reading(key, profile))
    compiled, _notes = compile_blocks(patches, profile)
    if key in compiled and compiled[key] == value:
        return patches
    return [{"id": key, "op": "replace", "value": value}]


def _diff(key: str, value, read) -> list[dict]:
    if is_list(key):
        return _list_diff(key, value, read)
    if isinstance(value, dict) and isinstance(read, dict):
        return [_set(key, value, read)]
    # A scalar, "off", or a block the reader has not got: said as a whole.
    return [{"id": key, "op": "replace", "value": value}]


def _set(block: str, value: dict, read: dict, **extra) -> dict:
    fields = {name: item for name, item in value.items()
              if name not in read or read[name] != item}
    drop = sorted(name for name in read if name not in value)
    patch = {"id": block, "op": "set", "base": digest(read), **extra}
    if fields:
        patch["fields"] = fields
    if drop:
        patch["drop"] = drop
    if not fields and not drop:
        patch["op"] = "keep"
    return patch


def _list_diff(key: str, value, read: list) -> list[dict]:
    """Entries matched to the read ones: the same entry first, then the same
    kind of entry in order. Read entries left over are ``off``; stated ones
    left over are ``add``."""
    if not isinstance(value, list) or (not value and read):
        return [{"id": key, "op": "replace", "value": value}]
    claimed: dict[int, int] = {}
    for index, entry in enumerate(value):
        for at, item in enumerate(read):
            if at not in claimed.values() and item == entry:
                claimed[index] = at
                break
    for index, entry in enumerate(value):
        if index in claimed:
            continue
        for at, item in enumerate(read):
            if at not in claimed.values() and _kind(key, item) == _kind(key, entry):
                claimed[index] = at
                break
    patches = []
    for index, entry in enumerate(value):
        at = claimed.get(index)
        if at is None:
            continue
        item = read[at]
        extra = {"at": at, "kind": _kind(key, item)}
        if item == entry:
            patches.append({"id": f"{key}:{at}", "op": "keep", "base": digest(item), **extra})
        elif isinstance(entry, dict) and isinstance(item, dict):
            patches.append(_set(f"{key}:{at}", entry, item, **extra))
        else:
            return [{"id": key, "op": "replace", "value": value}]
    for at, item in enumerate(read):
        if at not in claimed.values():
            patches.append({"id": f"{key}:{at}", "op": "off", "base": digest(item),
                            "at": at, "kind": _kind(key, item)})
    patches.extend({"id": key, "op": "add", "value": entry}
                   for index, entry in enumerate(value) if index not in claimed)
    return patches


# --- reading a document ----------------------------------------------------------


def key_of(block: str) -> str:
    """The primitive a block id belongs to: ``triggers:1`` -> ``triggers``."""
    return block.split(":", 1)[0]


def is_current(document) -> bool:
    return isinstance(document, dict) and document.get("v") == VERSION


def overrides_of(document: dict | None) -> dict:
    """The correction as its owner stated it: the face and every primitive
    as said. What `CardAnnotation.overrides` reads."""
    if not is_current(document):
        return {}
    return {**document["face"], **document["said"]}


def effective(document: dict | None, profile) -> tuple[dict, list[Note]]:
    """What a stored correction says now: the overrides the engine plays,
    each patch applied to today's reading, and a note for every patch whose
    block changed or is gone."""
    if not is_current(document):
        return {}, []
    values, notes = compile_blocks(document["blocks"], profile)
    return {**document["face"], **values}, notes


def stated(document: dict | None, profile) -> dict:
    """What a stored correction says, for writing it again: `effective`, and
    each correction whose block is gone as it was said.

    Those are not played - there is nothing left to apply them to - but they
    are not the engine's to drop either: saving another block must not take
    the "please check" away before its owner has seen it.
    """
    said, notes = effective(document, profile)
    if not notes:
        return said
    owned = overrides_of(document)
    gone = {note.key for note in notes
            if note.status == "gone" and not is_list(note.key) and note.key in owned}
    return said | {key: owned[key] for key in gone}


def settle(document: dict | None, profile, keys) -> tuple[dict, dict]:
    """Its owner has checked these primitives: the correction as it plays now.

    A block that changed under its correction is written again against
    today's reading - the same value, so the same engine card, and nothing
    left to check. One that is gone is let go: it was not played anyway.

    Returns:
        tuple: The overrides and the document to store.
    """
    keys = frozenset(keys)
    said, notes = effective(document, profile)
    carried = stated(document, profile)
    gone = {note.key for note in notes if note.status == "gone" and note.key not in said}
    kept = {key: value for key, value in carried.items()
            if key not in keys or key not in gone}
    return kept, from_overrides(kept, profile, previous=document, fresh=keys)


def compile_blocks(patches: list[dict], profile) -> tuple[dict, list[Note]]:
    """The primitives a document's patches say, against this reading.

    Returns:
        tuple: `{key: value}` in the overrides' form, and a note for every
        patch whose block changed or is gone. A ``set`` on a block that is
        gone is not applied: there is nothing left to set the fields on.
    """
    by_key: dict[str, list[dict]] = {}
    for patch in patches:
        by_key.setdefault(key_of(patch["id"]), []).append(patch)
    values: dict = {}
    notes: list[Note] = []
    for key, own in by_key.items():
        if is_list(key):
            values[key] = _compile_list(key, own, reading(key, profile), notes)
            continue
        found = _compile_single(key, own[0], reading(key, profile), notes)
        if found is not _GONE:
            values[key] = found
    return values, notes


_GONE = object()


def _compile_single(key: str, patch: dict, read, notes: list[Note]):
    if patch["op"] == "replace":
        return patch["value"]
    if read is None or (patch["op"] == "set" and not isinstance(read, dict)):
        notes.append(Note(key, patch["id"], "gone"))
        return _GONE
    if digest(read) != patch.get("base"):
        notes.append(Note(key, patch["id"], "changed"))
    if patch["op"] == "keep":
        return read
    found = {name: item for name, item in read.items() if name not in patch.get("drop", ())}
    return found | patch.get("fields", {})


def _compile_list(key: str, patches: list[dict], read: list, notes: list[Note]) -> list:
    for patch in patches:
        if patch["op"] == "replace":
            return patch["value"]
    entries = [patch for patch in patches if patch["op"] in ENTRY_OPS]
    where = _find(key, entries, read, notes)
    added = _absorb(key, [patch for patch in patches if patch["op"] == "add"], read, where,
                    notes)
    found = []
    for at, item in enumerate(read):
        patch = where.get(at)
        if patch is None or patch["op"] == "keep":
            found.append(item)
        elif patch["op"] == "set":
            kept = {name: value for name, value in item.items()
                    if name not in patch.get("drop", ())}
            found.append(kept | patch.get("fields", {}))
        elif patch["op"] == "add":
            found.append(patch["value"])
    found.extend(patch["value"] for patch in added)
    return found


def _absorb(key: str, adds: list[dict], read: list, where: dict, notes: list[Note]) -> list:
    """Added entries the reader has learned to read since, taken in place of
    theirs - so an entry is never played twice. The same entry is the reader's
    now; one of the same kind is replaced by the added one, and noted. The
    rest are added after the read entries."""
    left = []
    for patch in adds:
        at = next((index for index, item in enumerate(read)
                   if index not in where and item == patch["value"]), None)
        if at is not None:
            where[at] = {"op": "keep"}
            continue
        kind = _kind(key, patch["value"])
        at = next((index for index, item in enumerate(read)
                   if index not in where and _kind(key, item) == kind), None)
        if at is None:
            left.append(patch)
            continue
        where[at] = patch
        notes.append(Note(key, patch["id"], "changed"))
    return left


def _find(key: str, entries: list[dict], read: list, notes: list[Note]) -> dict[int, dict]:
    """Each entry patch's read entry today, `{index: patch}`.

    The same entry where it was, then the same entry anywhere (a reading
    that only moved it), then the same kind of entry where it was, then the
    same kind anywhere - the last two noted as changed. The rest are gone.
    """
    digests = [digest(item) for item in read]
    where: dict[int, dict] = {}
    left = list(entries)

    def claim(patch, at, changed):
        where[at] = patch
        left.remove(patch)
        if changed:
            notes.append(Note(key, patch["id"], "changed"))

    for patch in list(left):
        at = patch.get("at", -1)
        if 0 <= at < len(read) and digests[at] == patch.get("base"):
            claim(patch, at, False)
    for patch in list(left):
        at = next((index for index, found in enumerate(digests)
                   if index not in where and found == patch.get("base")), None)
        if at is not None:
            claim(patch, at, False)
    for patch in list(left):
        at = patch.get("at", -1)
        if 0 <= at < len(read) and at not in where \
                and _kind(key, read[at]) == patch.get("kind", ""):
            claim(patch, at, True)
    for patch in list(left):
        at = next((index for index, item in enumerate(read)
                   if index not in where and _kind(key, item) == patch.get("kind", "")), None)
        if at is not None:
            claim(patch, at, True)
    for patch in left:
        notes.append(Note(key, patch["id"], "gone"))
    return where


# --- checking a document ---------------------------------------------------------


def validate(document) -> dict:
    """Check a patch document's structure; return it unchanged, or raise.

    The values are checked as `primitives.validate` checks an override: a
    whole value, an added entry, and the fields of a ``set`` against the
    keys its primitive may carry.
    """
    if document in (None, {}):
        return document
    if not is_current(document):
        raise PatchError("not a patch document of this version")
    if not isinstance(document.get("face"), dict) or not isinstance(document.get("blocks"), list):
        raise PatchError("a patch document has a face and a list of blocks")
    if primitives.KEYS & set(document["face"]):
        raise PatchError("a primitive is a block, not a face value")
    said = document.get("said")
    if not isinstance(said, dict) or set(said) - primitives.KEYS:
        raise PatchError("a patch document says each primitive's value")
    for key, value in said.items():
        if value is not None:
            try:
                primitives.validate(key, value)
            except primitives.PrimitiveError as exc:
                raise PatchError(str(exc)) from exc
    if len(document["blocks"]) > _MOST_PATCHES:
        raise PatchError("too many patches")
    for patch in document["blocks"]:
        _validate_patch(patch)
    return document


def _validate_patch(patch) -> None:
    if not isinstance(patch, dict) or not isinstance(patch.get("id"), str):
        raise PatchError("a patch is an object with an id")
    block, op = patch["id"], patch.get("op")
    key, _sep, index = block.partition(":")
    if key not in primitives.KEYS:
        raise PatchError(f"{block!r} is not a block")
    many = is_list(key)
    if index:
        if not many or not index.isdigit() or int(index) >= primitives.MAX_ITEMS:
            raise PatchError(f"{block!r} is not an entry")
        allowed = ENTRY_OPS
    else:
        allowed = LIST_OPS if many else SINGLE_OPS
    if op not in allowed:
        raise PatchError(f"{block}: {op!r} is not an operation here")
    try:
        if op == "replace":
            primitives.validate(key, patch.get("value"))
        elif op == "add":
            primitives.validate(key, [patch.get("value")])
        elif op == "set":
            primitives.validate_fields(key, patch.get("fields", {}))
    except primitives.PrimitiveError as exc:
        raise PatchError(str(exc)) from exc
    drop = patch.get("drop", [])
    if not isinstance(drop, list) or not all(isinstance(name, str) for name in drop) \
            or set(drop) - primitives.BY_KEY[key].keys:
        raise PatchError(f"{block}: drop names fields of the block")
