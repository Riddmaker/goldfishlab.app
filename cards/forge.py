"""Forge's card scripts: reading them, and measuring how far blocks cover them.

Forge (https://github.com/Card-Forge/forge, GPL-3.0) describes each card in a
small text script, one per card under ``forge-gui/res/cardsfolder/``::

    Name:Lotus Cobra
    ManaCost:1 G
    Types:Creature Snake
    T:Mode$ ChangesZone | Origin$ Any | Destination$ Battlefield | ... | Execute$ TrigMana
    SVar:TrigMana:DB$ Mana | Produced$ Any

The card-model rebuild takes its blocks from these scripts first and from
our own reader where a card has none (decided 2026-10-13). This module is the
phase-0 spike: it parses a script into faces and abilities, and names the
**constructs** an ability uses - its kind with its API, trigger mode, static
mode or replacement event, every sub-ability it chains to, its cost atoms and
its keywords - so the catalogue can be counted. The scripts are read from a
local checkout (``/.data/forge/``) and nothing of them is committed; the
importer, its NOTICE entry and the licence text come in phase 1.

Scripts are input: they are parsed as text and never executed.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

#: Where a checkout of the scripts lives, below the repository.
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


def read_all(root: Path) -> dict[str, Script]:
    """Every script below ``root``, by the name of its first face."""
    found = {}
    for path in sorted((root / SCRIPTS).rglob("*.txt")):
        script = parse(path.read_text(encoding="utf-8", errors="replace"),
                       str(path.relative_to(root)))
        if script.name:
            found.setdefault(script.name, script)
    return found


# --- constructs -------------------------------------------------------------------

#: Cost atoms as Forge writes them: ``T``, ``Q``, ``Sac<1/CARDNAME>``,
#: ``PayLife<2>``, ``Discard<1/Card>``, ``tapXType<1/Creature>`` ...
_COST_ATOM = re.compile(r"^([A-Za-z]+)<")
_MANA_SYMBOL = re.compile(r"^(?:\d+|[WUBRGCXSP](?:/[WUBRGCP2])*)$")


def cost_atoms(cost: str) -> list[str]:
    """The kinds of atom in a Forge cost: ``tap``, ``mana``, ``Sac``, ``PayLife`` ..."""
    atoms = []
    for part in cost.split():
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


# --- a packed copy, read in seconds -------------------------------------------------
#
# A checkout is 34,000 small files, and reading them on a mounted volume takes
# minutes. The first read packs them into one file named after the commit.


def commit_of(root: Path) -> str:
    """The commit a checkout stands on: a detached HEAD holds it directly."""
    head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if head.startswith("ref:"):
        head = (root / ".git" / head.split(" ", 1)[1]).read_text(encoding="utf-8").strip()
    return head


def load(root: Path) -> dict[str, Script]:
    """`read_all`, through a packed copy beside the checkout."""
    import gzip
    import json

    packed = root.parent / f"forge_scripts_{commit_of(root)[:10]}.json.gz"
    if packed.exists():
        with gzip.open(packed, "rt", encoding="utf-8") as handle:
            texts = json.load(handle)
    else:
        texts = {str(path.relative_to(root)): path.read_text(encoding="utf-8",
                                                               errors="replace")
                 for path in sorted((root / SCRIPTS).rglob("*.txt"))}
        with gzip.open(packed, "wt", encoding="utf-8") as handle:
            json.dump(texts, handle, separators=(",", ":"), ensure_ascii=False)
    found = {}
    for path, text in texts.items():
        script = parse(text, path)
        if script.name:
            found.setdefault(script.name, script)
    return found


# --- facts a goldfish plays, for the cross-check with our reader -------------------

#: Mana of any colour, as our engine writes the choice.
ANY = "WUBRG"


def _produced(text: str) -> set[str]:
    tokens = text.replace(",", " ").split()
    if tokens[:1] == ["Combo"]:
        tokens = tokens[1:]
    letters = set()
    for token in tokens:
        if token in ("Any", "ColorIdentity", "AnyColor"):
            letters |= set(ANY)
        elif token in set("WUBRGC"):
            letters.add(token)
    return letters


def _walk(ability: Ability):
    yield ability
    for sub in ability.chain:
        yield from _walk(sub)


def _self_enters(ability: Ability) -> bool:
    return (ability.line == "T" and ability.head == "ChangesZone"
            and ability.param("Destination") == "Battlefield"
            and ability.param("ValidCard") == "Card.Self")


#: A basic land type and the mana it taps for, which no script line spells out.
_BASIC_TYPES = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}

#: What a land search may name besides ``Land``: the basic land types.
_LAND_WORDS = ("Land", *_BASIC_TYPES)


def facts(script: Script) -> dict:
    """What the card does that the goldfish plays, in five coarse facts.

    The front face, and for a modal double-faced card both faces: either may
    be played, and our reader merges them.

    ``taps_for``: the colours its {T} mana abilities can make, ``C`` included;
    ``draws``: cards drawn as it resolves or enters, ``x`` for X; ``land_search``:
    a land from the library onto the battlefield; ``enters_tapped``;
    ``treasures``: it makes Treasure tokens.
    """
    faces = script.faces if script.mode == "Modal" else script.faces[:1]
    taps_for: set[str] = set()
    draws, land_search, enters_tapped, treasures = 0, False, False, False
    for face in faces:
        types = face.types.split()
        if "Land" in types:
            taps_for |= {_BASIC_TYPES[name] for name in types if name in _BASIC_TYPES}
    for ability in (ability for face in faces for ability in face.abilities):
        steps = list(_walk(ability))
        atoms = set(cost_atoms(ability.param("Cost")))
        if (ability.line == "A" and not ability.spell and ability.head == "Mana"
                and "tap" in atoms and atoms <= {"tap", "mana"}):
            taps_for |= _produced(ability.param("Produced"))
        if (ability.line == "A" and ability.spell) or _self_enters(ability):
            for step in steps:
                if step.head == "Draw":
                    amount = step.param("NumCards", "1")
                    draws = draws + int(amount) if amount.isdigit() and draws != "x" else "x"
        for step in steps:
            if (step.head == "ChangeZone" and step.param("Origin") == "Library"
                    and step.param("Destination") == "Battlefield"
                    and not step.param("DefinedPlayer")
                    and any(word in step.param("ChangeType") for word in _LAND_WORDS)):
                land_search = True
            if step.head == "Token" and "treasure" in step.param("TokenScript"):
                treasures = True
        if ability.line == "R" and ability.head == "Moved" and \
                ability.param("ValidCard") == "Card.Self" and \
                any(step.head == "Tap" and step.param("ETB") == "True" for step in steps):
            enters_tapped = True
    return {"taps_for": "".join(sorted(taps_for)), "draws": draws,
            "land_search": land_search, "enters_tapped": enters_tapped,
            "treasures": treasures}
