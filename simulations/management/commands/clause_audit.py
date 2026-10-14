"""Card model phase 0.7: which clauses of a card's text does our reader consume?

    manage.py clause_audit                   # 200 random Commander-legal cards
    manage.py clause_audit --cards 200 --seed 20261013 --sample 50

Where a card has no script to take its blocks from (a preview card the
scripts do not have yet), our own reader is the fallback - and a reader that
drops a clause silently is the risk. This audit splits each card's text into
clauses and watches the reader's patterns while it derives the card's
profile. A clause is

* ``consumed`` - patterns matched at least half of it;
* ``partial`` - patterns matched some of it: not classified;
* ``unconsumed`` - nothing matched: the reader dropped it, or it has nothing
  a goldfish plays;
* ``keyword`` - a line of keywords only (Flying, trample);
* ``reminder`` - reminder text in parentheses.

``--sample`` prints that many consumed clauses with the card's reading, for a
hand check of clauses counted as consumed that the reading does not reflect.
The reader is only observed, never changed.
"""

import random
import re
from collections import Counter

from django.core.management.base import BaseCommand

from simulations.engine import adapter

#: A clause shorter than this many letters is not counted (a lone "Flash").
_MIN_LETTERS = 3
_REMINDER = re.compile(r"\([^)]*\)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z{•])")


class _Recorder:
    """Stands in for a compiled pattern or the `re` module and keeps every match."""

    def __init__(self, target, found: list):
        self._target = target
        self._found = found

    def _keep(self, match):
        if match is not None and match.group(0):
            self._found.append(match.group(0).lower())
        return match

    def __getattr__(self, name):
        attribute = getattr(self._target, name)
        if name in ("search", "match", "fullmatch"):
            return lambda *args, **kwargs: self._keep(attribute(*args, **kwargs))
        if name == "finditer":
            return lambda *args, **kwargs: (self._keep(m) for m in attribute(*args, **kwargs))
        if name in ("findall", "sub", "subn", "split") and isinstance(self._target, re.Pattern):
            return lambda string, *args, **kwargs: self._via_finditer(attribute, string,
                                                                       *args, **kwargs)
        if name in ("findall", "sub", "subn", "split"):
            return lambda pattern, string, *args, **kwargs: self._module_call(
                attribute, pattern, string, *args, **kwargs)
        return attribute

    def _via_finditer(self, attribute, string, *args, **kwargs):
        for match in self._target.finditer(string):
            self._keep(match)
        return attribute(string, *args, **kwargs)

    def _module_call(self, attribute, pattern, string, *args, **kwargs):
        flags = kwargs.get("flags", 0)
        for match in re.finditer(pattern, string, flags) if isinstance(string, str) else ():
            self._keep(match)
        return attribute(pattern, string, *args, **kwargs)


def clauses(text: str, keywords) -> list[tuple[str, str]]:
    """The card's clauses, each with ``keyword`` or ``reminder`` already
    decided where the text alone says so, else ``""``."""
    out = []
    lowered = {word.lower() for word in keywords or ()}
    for line in (text or "").replace(" // ", "\n").splitlines():
        for reminder in _REMINDER.findall(line):
            out.append((reminder, "reminder"))
        line = _REMINDER.sub("", line).strip()
        if not line:
            continue
        words = [word.strip().lower() for word in re.split(r"[,;]", line)]
        if lowered and all(word.split(" ")[0] in lowered or word in lowered
                           for word in words if word):
            out.append((line, "keyword"))
            continue
        out.extend((sentence.strip(), "") for sentence in _SENTENCE.split(line)
                   if sentence.strip())
    return [(clause, kind) for clause, kind in out
            if sum(ch.isalpha() for ch in clause) >= _MIN_LETTERS]


def covered(clause: str, found: list[str]) -> float:
    """The share of the clause's characters some recorded match covers."""
    lowered = clause.lower()
    hit = [False] * len(lowered)
    for match in found:
        match = match.strip()
        if len(match) < 2:
            continue
        start = lowered.find(match)
        while start != -1:
            for index in range(start, start + len(match)):
                hit[index] = True
            start = lowered.find(match, start + 1)
    letters = [flag for flag, ch in zip(hit, lowered, strict=True) if not ch.isspace()]
    return sum(letters) / len(letters) if letters else 0.0


class Command(BaseCommand):
    help = "Phase-0 audit: which clauses of random cards our reader consumes."

    def add_arguments(self, parser):
        parser.add_argument("--cards", type=int, default=200)
        parser.add_argument("--seed", type=int, default=20261013)
        parser.add_argument("--sample", type=int, default=50)

    def handle(self, *args, **options):
        from cards import profiles
        from cards.models import OracleCard

        ids = sorted(str(pk) for pk in OracleCard.objects.filter(
            legalities__commander="legal").values_list("pk", flat=True))
        chosen = random.Random(options["seed"]).sample(ids, options["cards"])  # noqa: S311
        cards = list(OracleCard.objects.filter(pk__in=chosen).order_by("name"))

        found: list[str] = []
        patched = {name: value for name, value in vars(profiles).items()
                   if isinstance(value, re.Pattern)}
        original_re = profiles.re
        try:
            for name, value in patched.items():
                setattr(profiles, name, _Recorder(value, found))
            profiles.re = _Recorder(re, found)
            results = self._audit(cards, found, profiles)
        finally:
            for name, value in patched.items():
                setattr(profiles, name, value)
            profiles.re = original_re
        self._report(results, options)

    def _audit(self, cards, found, profiles):
        branches, revivals = profiles.tutor_branches(), profiles.reanimate_branches()
        results = []
        for card in cards:
            found.clear()
            tags = set(card.tags.values_list("slug", flat=True))
            card.profile = profiles.derive(card, tags, branches=branches, revivals=revivals)
            matches = list(found)
            reading, gaps = adapter.engine_readings([card], builtin=False)[card.pk]
            for clause, kind in clauses(card.oracle_text, card.keywords):
                if not kind:
                    share = covered(clause, matches)
                    kind = ("consumed" if share >= 0.5 else
                            "partial" if share > 0 else "unconsumed")
                results.append((card, clause, kind, reading, gaps))
        return results

    def _report(self, results, options):
        from simulations import coverage

        kinds = Counter(kind for _card, _clause, kind, _reading, _gaps in results)
        total = sum(kinds.values())
        classified = total - kinds["partial"]
        self.stdout.write(f"{len({id(r[0]) for r in results})} cards with text, {total} clauses")
        for kind, count in kinds.most_common():
            self.stdout.write(f"  {kind:11s} {count:5d}  {count / total:6.1%}")
        self.stdout.write(f"classified: {classified / total:.1%}")
        consumed = [row for row in results if row[2] == "consumed"]
        sample = random.Random(options["seed"]).sample(  # noqa: S311
            consumed, min(options["sample"], len(consumed)))
        self.stdout.write(f"\n-- {len(sample)} consumed clauses for the hand check")
        for number, (card, clause, _kind, reading, gaps) in enumerate(sample, 1):
            read = coverage.reading(reading, gaps)
            fields = {key: value for key, value in read["read"].items()
                      if key not in ("categories", "tags", "cost", "colors", "types", "mv",
                                     "generic", "pips", "printed_subtypes", "creature_types",
                                     "ritual_color", "power", "toughness", "kind")}
            self.stdout.write(f"{number:2d}. {card.name}: {clause}\n"
                              f"    kind {read['read'].get('kind', 'land')}, {fields}"
                              + (f", unread {read['unread']}" if read["unread"] else ""))
        unconsumed = Counter(clause.split(" ")[0].lower() for _c, clause, kind, _r, _g
                             in results if kind == "unconsumed")
        self.stdout.write(f"\nunconsumed clauses start with: {unconsumed.most_common(15)}")
