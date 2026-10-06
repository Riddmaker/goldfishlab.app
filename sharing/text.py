"""A shared report in a few lines: for a link preview, and to paste (P4).

The same handful of numbers serve both: the description a chat app shows
under the link, and the text the owner copies into Reddit or Discord. Chosen
for somebody who has not seen the page - is the commander out early, are the
hands keepable, does the combo come together - and always ending on how much
of the deck the engine could read, because a number without that is a number
nobody should believe.

Written in the language of the page that asks, from `simulations.report.build`.
"""

from django.utils.translation import gettext, ngettext

from core.l10n import number, percent

#: The turn "the commander is out by" is read at, if the run went that far.
COMMANDER_TURN = 4
#: Milestones besides the commander's, and combos, worth a line each.
MORE_MILESTONES = 1
COMBOS = 1


def title(shared, run) -> str:
    """"Atraxa, Grand Unifier: 10,000 games simulated" - led by the commander,
    which is a card name, rather than by the deck's name, which anybody can set."""
    games = number(run.games_total)
    if shared.commander:
        return gettext("%(commander)s: %(games)s games simulated") % {
            "commander": shared.commander, "games": games}
    return gettext("A Commander deck: %(games)s games simulated") % {"games": games}


def facts(report: dict, run) -> list[str]:
    """The lines worth reading first, most telling first."""
    lines = []
    milestones = {row["key"]: row for row in report["milestones"]}
    commander = milestones.get("commander")
    if commander:
        turn = min(COMMANDER_TURN, len(commander["shares"]))
        lines.append(gettext("Commander out by turn %(turn)s: %(percent)s") % {
            "turn": turn, "percent": percent(commander["shares"][turn - 1])})
    if report["mulligans"]:
        lines.append(gettext("Kept the first seven: %(percent)s") % {
            "percent": percent(report["mulligans"][0].share)})
    others = sorted((row for key, row in milestones.items() if key != "commander"),
                    key=lambda row: -row["shares"][-1])
    for row in others[:MORE_MILESTONES]:
        lines.append(gettext("%(milestone)s by turn %(turn)s: %(percent)s") % {
            "milestone": row["label"], "turn": report["turns"],
            "percent": percent(row["shares"][-1])})
    real = [measurement for measurement in report["combos"]
            if not measurement.hypothetical and not measurement.never]
    for measurement in sorted(real, key=lambda m: -m.share)[:COMBOS]:
        names = " + ".join(card.name for card in measurement.combo.cards.all())
        lines.append(gettext("%(combo)s together by turn %(turn)s: %(percent)s") % {
            "combo": names, "turn": measurement.turns, "percent": measurement.share_label})
    lines.append(ngettext(
        "The engine read %(read)s of %(total)s card in full.",
        "The engine read %(read)s of %(total)s cards in full.",
        run.coverage_total) % {"read": run.coverage_read, "total": run.coverage_total})
    return lines


def description(report: dict, run) -> str:
    """One paragraph for a link preview."""
    return " · ".join(facts(report, run))


def plain(shared, report: dict, run, url: str) -> str:
    """The text to paste: a heading, the facts as a list, and the link."""
    rule = (gettext("1-v-1, first draw skipped") if run.on_the_play
            else gettext("multiplayer draw rule"))
    heading = gettext("%(title)s, %(turns)s turns, %(rule)s (Goldfish Lab)") % {
        "title": title(shared, run), "turns": run.turns, "rule": rule}
    return "\n".join([heading, *(f"- {line}" for line in facts(report, run)), url])
