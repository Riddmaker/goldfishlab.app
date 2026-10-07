"""The numbers of "How many lands in Commander?" (P11), from the database.

The article's prose is fixed; every number in it is worked out here from the
finished runs, so it moves when the precons or the engine do:

- the sweep (`datapages.sweep`): per precon, each land count's share of games
  with four lands on turn 4, and the spells seen by the last turn - two line
  charts and the average change per land;
- the precons by land count: how many run each, and their average share.

A precon whose sweep is not complete (a run still playing, a variant held)
is left out rather than drawn with a gap.
"""

from collections import defaultdict
from dataclasses import dataclass

from django.core.cache import cache
from django.utils.translation import gettext

from core.l10n import number, percent
from datapages import numbers, sweep
from datapages.models import LandSweep
from datapages.views import CACHE_SECONDS, rows
from simulations import charts
from simulations.models import SimulationRun

KEY = "datapages:article"


@dataclass(frozen=True)
class Line:
    """One precon across the sweep's land counts."""

    name: str
    slug: str
    on_curve: tuple[float, ...]
    spells_seen: tuple[float, ...]


@dataclass(frozen=True)
class Group:
    """The precons that run one number of lands."""

    lands: int
    decks: int
    on_curve: float

    @property
    def share(self) -> str:
        """In the page's language; the group itself is cached for all."""
        return percent(self.on_curve)


def _lines() -> list[Line]:
    by_precon = defaultdict(dict)
    sweeps = (LandSweep.objects.filter(precon__in=sweep.chosen())
              .select_related("precon").prefetch_related("deck__runs"))
    for variant in sweeps:
        done = [run for run in variant.deck.runs.all()
                if run.status == SimulationRun.Status.DONE]
        if done:
            by_precon[variant.precon][variant.lands] = numbers.of(done[0])
    found = []
    for precon, by_lands in by_precon.items():
        if set(by_lands) != set(sweep.LANDS):
            continue
        ordered = [by_lands[lands] for lands in sweep.LANDS]
        found.append(Line(name=precon.name, slug=precon.slug,
                          on_curve=tuple(n.on_curve for n in ordered),
                          spells_seen=tuple(n.spells_seen for n in ordered)))
    return sorted(found, key=lambda line: line.name)


def _groups() -> list[Group]:
    by_lands = defaultdict(list)
    for row in rows():
        by_lands[row.numbers.lands].append(row.numbers.on_curve)
    return [Group(lands=lands, decks=len(shares), on_curve=sum(shares) / len(shares))
            for lands, shares in sorted(by_lands.items())]


def data() -> dict:
    """The language-free numbers, cached as long as a report is."""
    found = cache.get(KEY)
    if found is None:
        found = {"lines": _lines(), "groups": _groups()}
        cache.set(KEY, found, CACHE_SECONDS)
    return found


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def context() -> dict:
    """What the template needs, in the page's language."""
    found = data()
    lines, groups = found["lines"], found["groups"]
    low, high = sweep.LANDS[0], sweep.LANDS[-1]
    steps = high - low
    x_labels = [str(lands) for lands in sweep.LANDS]
    result = {"lines": lines, "groups": groups, "low": low, "high": high,
              "precons": sum(group.decks for group in groups)}
    if lines:
        on_low = _mean(line.on_curve[0] for line in lines)
        on_high = _mean(line.on_curve[-1] for line in lines)
        spells_low = _mean(line.spells_seen[0] for line in lines)
        spells_high = _mean(line.spells_seen[-1] for line in lines)
        result.update({
            "on_low": percent(on_low),
            "on_high": percent(on_high),
            "gain_per_land": number((on_high - on_low) / steps, 1),
            "spells_low": number(spells_low, 1),
            "spells_high": number(spells_high, 1),
            "cost_per_land": number((spells_low - spells_high) / steps, 2),
            "on_curve_chart": charts.line_chart(
                [(line.slug, line.name, line.on_curve) for line in lines], x_labels,
                top_value=charts.PERCENT_TOP, y_ticks=charts.percent_ticks(),
                infos={line.slug: gettext(
                    "From %(low)s of games at %(first)s lands to %(high)s at %(last)s.") % {
                    "low": percent(line.on_curve[0]), "high": percent(line.on_curve[-1]),
                    "first": low, "last": high} for line in lines}),
        })
        top, ticks = charts.count_scale(max(max(line.spells_seen) for line in lines))
        result["spells_chart"] = charts.line_chart(
            [(line.slug, line.name, line.spells_seen) for line in lines], x_labels,
            top_value=top, y_ticks=ticks,
            infos={line.slug: gettext(
                "From %(low)s spells seen at %(first)s lands to %(high)s at %(last)s.") % {
                "low": number(line.spells_seen[0], 1), "high": number(line.spells_seen[-1], 1),
                "first": low, "last": high} for line in lines})
    if groups:
        result["fewest"] = groups[0].lands
        result["most"] = groups[-1].lands
        result["usual"] = max(groups, key=lambda group: (group.decks, -group.lands)).lands
    return result
