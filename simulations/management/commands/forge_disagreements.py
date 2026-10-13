"""Card model phase 1: where Forge's blocks and our reader disagree.

    manage.py forge_disagreements                      # every Commander-legal card
    manage.py forge_disagreements --set fixed --examples 20
    manage.py forge_disagreements --out .data/disagreements.json

Needs the blocks of the pinned scripts (`ingest_scryfall --profiles` or the
nightly refresh translates them). For each card with a script, the five
goldfish facts of `cards.forge_import.facts` are compared with what our
reader and the engine make of the card today, and each fact falls in one of

* ``agree``;
* ``forge_more`` - Forge reads something where we read nothing, or more of
  it (more colours, more cards drawn): the gaps the next step closes;
* ``ours_more`` - the other way round;
* ``conflict`` - both read something, and it differs.

A card is marked ``unread`` when the engine has a reading gap on it, one
about its text; judgements ("how early to cast it") do not count.

``--out`` writes every card that is not ``agree`` on some fact, for the hand
checks; keep it out of the repository (``.data/`` is ignored). Nothing in the
database changes.
"""

import json
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from simulation.cards import Card
from simulations import coverage
from simulations.engine import adapter
from simulations.gaps import READING

FACTS = ("taps_for", "draws", "land_search", "enters_tapped", "treasures")
CLASSES = ("agree", "forge_more", "ours_more", "conflict")

_CHUNK = 1_000


def our_facts(card: Card) -> dict:
    """The five facts as our reader and the engine see the card."""
    taps_for = {letter for ability in card.mana_abilities if ability.rule == "flat"
                for key, _amount in ability.produces for letter in key}
    treasures = bool(card.treasures or any(t.treasures for t in card.triggers)
                     or any(t.treasures for t in card.combat))
    return {"taps_for": "".join(sorted(taps_for)),
            "draws": "x" if card.draws_x else card.draw_on_cast,
            "land_search": card.land_search is not None,
            "enters_tapped": card.enters_tapped or card.tapped_unless is not None,
            "treasures": treasures}


def classify(fact: str, theirs, ours) -> str:
    if theirs == ours:
        return "agree"
    if fact == "taps_for":
        theirs, ours = set(theirs), set(ours)
        if theirs > ours:
            return "forge_more"
        return "ours_more" if ours > theirs else "conflict"
    if fact == "draws":
        if not ours:
            return "forge_more"
        if not theirs:
            return "ours_more"
        if isinstance(theirs, int) and isinstance(ours, int):
            return "forge_more" if theirs > ours else "ours_more"
        return "conflict"
    return "forge_more" if theirs else "ours_more"


class Command(BaseCommand):
    help = "Phase 1: compare Forge's blocks with our reader, fact by fact."

    def add_arguments(self, parser):
        parser.add_argument("--set", choices=("catalogue", "fixed"), default="catalogue",
                            help="every Commander-legal card, or the 3,070-card coverage set")
        parser.add_argument("--examples", type=int, default=8)
        parser.add_argument("--out", type=Path)

    def handle(self, *args, **options):
        from cards import forge_import
        from cards.models import ForgeScript, OracleCard
        from simulation import blocks

        cards = OracleCard.objects.filter(legalities__commander="legal").select_related(
            "profile", "forge").order_by("name")
        if options["set"] == "fixed":
            cards = cards.filter(pk__in=coverage.ids(coverage.load_snapshot()))
        cards = list(cards)
        with_script = [card for card in cards if _forge(card) is not None]
        if not ForgeScript.objects.exists():
            raise CommandError("no blocks yet: run ingest_scryfall --profiles first")
        complete = sum(not card.forge.unmodeled for card in with_script)
        self.stdout.write(
            f"{len(with_script)} of {len(cards)} cards have a script; {complete} "
            f"({complete / max(len(with_script), 1):.1%}) use nothing outside the vocabulary")

        counts = {fact: Counter() for fact in FACTS}
        examples: dict[tuple[str, str], list[str]] = {}
        rows = []
        for start in range(0, len(with_script), _CHUNK):
            chunk = with_script[start:start + _CHUNK]
            readings = adapter.engine_readings(chunk, builtin=False)
            for card in chunk:
                engine_card, gaps = readings[card.pk]
                # A reading gap only: "how early to cast it" is a judgement
                # almost every card leaves open, and says nothing of the text.
                unread = any(gap.kind == READING for gap in gaps)
                theirs = forge_import.facts(blocks.from_json(card.forge.blocks),
                                            card.type_line)
                ours = our_facts(engine_card)
                row = {}
                for fact in FACTS:
                    found = classify(fact, theirs[fact], ours[fact])
                    counts[fact][found] += 1
                    if found != "agree":
                        row[fact] = {"class": found, "forge": theirs[fact], "ours": ours[fact]}
                        examples.setdefault((fact, found), []).append(
                            f"{card.name}: forge {theirs[fact]!r}, ours {ours[fact]!r}"
                            + (" (unread)" if unread else ""))
                if row:
                    rows.append({"name": card.name, "id": str(card.pk),
                                 "path": card.forge.path, "unread": unread, **row})

        for fact in FACTS:
            line = "  ".join(f"{name} {counts[fact][name]:6d}" for name in CLASSES)
            self.stdout.write(f"{fact:14s} {line}")
            for name in CLASSES[1:]:
                for example in examples.get((fact, name), [])[:options["examples"]]:
                    self.stdout.write(f"    {name}: {example}")
        if options["out"]:
            options["out"].write_text(json.dumps(rows, indent=1, ensure_ascii=False),
                                      encoding="utf-8")
            self.stdout.write(f"{len(rows)} cards written to {options['out']}")


def _forge(card):
    try:
        return card.forge
    except card._meta.model.forge.RelatedObjectDoesNotExist:
        return None
