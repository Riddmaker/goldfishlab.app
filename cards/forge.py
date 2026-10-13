"""Forge's card scripts: reading them, and measuring how far blocks cover them.

Forge (https://github.com/Card-Forge/forge, GPL-3.0) describes each card in a
small text script, one per card under ``forge-gui/res/cardsfolder/``::

    Name:Lotus Cobra
    ManaCost:1 G
    Types:Creature Snake
    T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ... | Execute$ TrigMana
    SVar:TrigMana:DB$ Mana | Produced$ Any

The card-model rebuild takes its blocks from these scripts first and from
our own reader where a card has none (decided 2026-10-13). This module reads
them: it parses a script into faces and abilities, names the **constructs**
an ability uses - its kind with its API, trigger mode, static mode or
replacement event, every sub-ability it chains to, its cost atoms and its
keywords - so the catalogue can be counted, and keeps the pinned copy. The
scripts of one commit are packed into ``cards/data/`` (GPL-3.0, see NOTICE
and ``licenses/``); :mod:`cards.forge_import` turns them into blocks.

Scripts are input: they are parsed as text and never executed.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

#: Where the scripts lie inside a checkout of Forge.
SCRIPTS = Path("forge-gui") / "res" / "cardsfolder"

#: The lines that hold abilities: activated or spell, triggered, static,
#: replacement.
ABILITY_LINES = ("A", "T", "S", "R")

#: Parameters that name another SVar holding the next ability of a chain.
_CHAIN_PARAMS = ("SubAbility", "Execute", "ReplaceWith", "RepeatSubAbility",
                 "FallbackAbility")

#: How deep a chain of SVars is followed. A script that loops is cut here.
_MAX_DEPTH = 12


@dataclass(frozen=True)
class Ability:
    """One ability line, with the chain of sub-abilities it runs.

    ``line`` is ``A``, ``T``, ``S``, ``R`` or ``DB`` (a sub-ability), ``head``
    its API (``Mana``, ``Draw``), trigger or static mode, or replacement event,
    and ``spell`` whether an ``A`` line is a spell (``SP$``) rather than an
    activated ability (``AB$``).
    """

    line: str
    head: str
    params: tuple[tuple[str, str], ...]
    spell: bool = False
    chain: tuple["Ability", ...] = ()

    def param(self, name: str, default: str = "") -> str:
        return dict(self.params).get(name, default)


@dataclass(frozen=True)
class Face:
    name: str
    mana_cost: str = ""
    types: str = ""
    oracle: str = ""
    keywords: tuple[str, ...] = ()
    abilities: tuple[Ability, ...] = ()
    svars: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Script:
    faces: tuple[Face, ...]
    #: ``AlternateMode``: ``DoubleFaced``, ``Split``, ``Adventure``, ... or "".
    mode: str = ""
    path: str = ""

    @property
    def name(self) -> str:
        return self.faces[0].name if self.faces else ""


def _segments(text: str) -> list[tuple[str, str]]:
    """``AB$ Mana | Cost$ T | Produced$ C`` -> [("AB", "Mana"), ("Cost", "T"), ...]."""
    found = []
    for part in text.split("|"):
        key, sep, value = part.strip().partition("$")
        if sep:
            found.append((key.strip(), value.strip()))
        elif part.strip():
            found.append((part.strip(), ""))
    return found


def _ability(line: str, text: str, svars: dict, depth: int = 0) -> Ability:
    segments = _segments(text)
    params = dict(segments)
    spell = False
    if line in ("A", "DB"):
        first_key, head = segments[0] if segments else ("", "")
        spell = first_key == "SP"
    elif line == "R":
        head = params.get("Event", "")
    else:
        head = params.get("Mode", "")
    chain = []
    if depth < _MAX_DEPTH:
        for name in _CHAIN_PARAMS:
            target = params.get(name)
            if target and target in svars:
                chain.append(_ability("DB", svars[target], svars, depth + 1))
    return Ability(line, head, tuple(sorted(params.items())), spell, tuple(chain))


def _face(lines: list[str]) -> Face:
    values: dict[str, str] = {}
    keywords, raw_abilities, svars = [], [], {}
    for line in lines:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        if key in ABILITY_LINES:
            raw_abilities.append((key, value))
        elif key == "K":
            keywords.append(value)
        elif key == "SVar":
            name, _sep, body = value.partition(":")
            svars[name] = body
        else:
            values.setdefault(key, value)
    abilities = tuple(_ability(key, value, svars) for key, value in raw_abilities)
    return Face(name=values.get("Name", ""), mana_cost=values.get("ManaCost", ""),
                types=values.get("Types", ""), oracle=values.get("Oracle", ""),
                keywords=tuple(keywords), abilities=abilities,
                svars=tuple(sorted(svars.items())))


def parse(text: str, path: str = "") -> Script:
    """One script file into its faces. Faces are separated by ``ALTERNATE``
    (the back face, a split half) or ``SPECIALIZE`` lines."""
    faces, current, mode = [], [], ""
    for line in text.splitlines():
        line = line.strip()
        if line in ("ALTERNATE", "SPECIALIZE") or line.startswith("SPECIALIZE:"):
            faces.append(current)
            current = []
            continue
        if line.startswith("AlternateMode:"):
            mode = line.partition(":")[2]
        current.append(line)
    faces.append(current)
    return Script(tuple(_face(lines) for lines in faces if lines), mode, path)


def from_checkout(root: Path, commit: str):
    """``(path, text)`` of every script of ``commit`` in a git checkout of
    Forge, read from git's objects in one stream rather than from 34,000 files.
    A sparse or shallow clone will do, as long as it has the commit."""
    import subprocess  # noqa: S404 - git, with a checked hash and no shell
    import tarfile

    if not _COMMIT.match(commit):
        raise ValueError(f"not a full commit hash: {commit!r}")
    archive = subprocess.Popen(  # noqa: S603
        ["git", "-C", str(root), "archive", "--format=tar", commit, str(SCRIPTS)],  # noqa: S607
        stdout=subprocess.PIPE)
    with tarfile.open(fileobj=archive.stdout, mode="r|") as tar:
        for member in tar:
            if member.isfile() and member.name.endswith(".txt"):
                handle = tar.extractfile(member)
                yield member.name, handle.read().decode("utf-8", errors="replace")
    if archive.wait() != 0:
        raise RuntimeError(f"git archive failed for {commit} in {root}")


# --- constructs -------------------------------------------------------------------

#: Cost atoms as Forge writes them: ``T``, ``Q``, ``Sac<1/CARDNAME>``,
#: ``PayLife<2>``, ``Discard<1/Card>``, ``tapXType<1/Creature>`` ...
_COST_ATOM = re.compile(r"^([A-Za-z]+)<")
_MANA_SYMBOL = re.compile(r"^(?:\d+|[WUBRGCXSP](?:/[WUBRGCP2])*)$")


#: One part of a cost: an atom with its ``<...>`` (which may hold spaces), or
#: a mana symbol.
_COST_PART = re.compile(r"[A-Za-z]+<[^>]*>|\S+")


def cost_parts(cost: str) -> list[str]:
    return _COST_PART.findall(cost)


def cost_atoms(cost: str) -> list[str]:
    """The kinds of atom in a Forge cost: ``tap``, ``mana``, ``Sac``, ``PayLife`` ..."""
    atoms = []
    for part in cost_parts(cost):
        if part == "T":
            atoms.append("tap")
        elif part == "Q":
            atoms.append("untap")
        elif _MANA_SYMBOL.match(part):
            atoms.append("mana")
        else:
            match = _COST_ATOM.match(part)
            atoms.append(match.group(1) if match else part)
    return sorted(set(atoms))


def _keyword(text: str) -> str:
    return text.split(":", 1)[0].strip()


def constructs(script: Script) -> Counter:
    """Every construct the script uses, with how often."""
    found: Counter = Counter()

    def walk(ability: Ability) -> None:
        if ability.line == "A":
            found[f"{'SP' if ability.spell else 'AB'}:{ability.head}"] += 1
        else:
            found[f"{ability.line}:{ability.head}"] += 1
        cost = ability.param("Cost")
        if cost:
            found.update(f"cost:{atom}" for atom in cost_atoms(cost))
        for sub in ability.chain:
            walk(sub)

    for face in script.faces:
        for ability in face.abilities:
            walk(ability)
        found.update(f"K:{_keyword(keyword)}" for keyword in face.keywords)
    return found


#: ``Count$`` heads in an SVar body: what a number is counted from, the part
#: of a script whose meaning lives in Forge's Java code.
_COUNT_HEAD = re.compile(r"Count\$([A-Za-z]+)")


def count_heads(script: Script) -> set[str]:
    return {head for face in script.faces for _name, body in face.svars
            for head in _COUNT_HEAD.findall(body)}


@dataclass
class Coverage:
    """How many scripts a vocabulary of the most frequent constructs covers."""

    cards: int = 0
    vocabulary: list[str] = field(default_factory=list)
    covered: int = 0
    missing: Counter = field(default_factory=Counter)

    @property
    def share(self) -> float:
        return self.covered / self.cards if self.cards else 0.0


def vocabulary(scripts, size: int) -> list[str]:
    """The ``size`` constructs the most scripts use, most used first."""
    used: Counter = Counter()
    for script in scripts:
        used.update(set(constructs(script)))
    return [name for name, _count in used.most_common(size)]


def coverage(scripts, words: list[str]) -> Coverage:
    """How many of ``scripts`` use nothing outside ``words``."""
    known = set(words)
    result = Coverage(vocabulary=words)
    for script in scripts:
        result.cards += 1
        outside = set(constructs(script)) - known
        if outside:
            result.missing.update(outside)
        else:
            result.covered += 1
    return result


# --- the pinned copy -----------------------------------------------------------
#
# The importer reads one commit of the scripts, packed into one file in the
# repository, and nothing else: the numbers of a deck change only when a pull
# request moves the pin (`manage.py forge_pack`). A checkout is 34,000 small
# files, so the copy is one gzip stream, read a script at a time: a header line
# with the commit, then one JSON line per script.

#: The packed scripts of the pinned commit.
PACK = Path(__file__).resolve().parent / "data" / "forge_scripts.jsonl.gz"

#: The pinned commit, readable in a pull request's diff.
PIN = PACK.with_name("forge_commit.txt")

_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def pinned() -> str:
    return PIN.read_text(encoding="utf-8").strip()


def pack(texts, commit: str, date: str, out: Path | None = None) -> int:
    """Write ``(path, text)`` pairs as the packed copy of ``commit``, made on
    ``date`` (ISO 8601); how many."""
    import gzip
    import json

    if not _COMMIT.match(commit):
        raise ValueError(f"not a full commit hash: {commit!r}")
    out = out or PACK
    count = 0
    # mtime=0: the same scripts give the same bytes, so an unchanged pin is
    # no change in git.
    with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped:
        zipped.write((json.dumps({"commit": commit, "date": date}) + "\n").encode())
        for path, text in sorted(texts):
            zipped.write((json.dumps({"path": path, "text": text}, ensure_ascii=False)
                          + "\n").encode())
            count += 1
    return count


def header(path: Path | None = None) -> dict:
    """``{"commit": ..., "date": ...}`` of the packed copy."""
    import gzip
    import json

    path = path or PACK
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.loads(handle.readline())


def scripts(path: Path | None = None):
    """Every script of the packed copy, one at a time, in path order."""
    import gzip
    import json

    path = path or PACK
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        handle.readline()
        for line in handle:
            entry = json.loads(line)
            script = parse(entry["text"], entry["path"])
            if script.name:
                yield script


def load(path: Path | None = None) -> dict[str, Script]:
    """Every script of the packed copy, by the name of its first face."""
    found: dict[str, Script] = {}
    for script in scripts(path):
        found.setdefault(script.name, script)
    return found
