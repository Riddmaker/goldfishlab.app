"""The stats page in the terminal (P2).

    python manage.py stats
    python manage.py stats --weeks 26

The same numbers as /admin/stats/ (metrics/report.py), for a shell on the
server or a look without signing in to the admin.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from metrics import report


class Command(BaseCommand):
    help = "Print the lead metric, the weekly counts and the return rates."

    def add_arguments(self, parser):
        parser.add_argument("--weeks", type=int, default=12, help="How many weeks to show.")

    def handle(self, *args, **options):
        today = timezone.localdate()
        count = max(1, options["weeks"])
        recent = report.weeks(today, count)
        lead = report.lead(today, recent)
        write = self.stdout.write

        if lead.week:
            write(f"People who simulated, week of {lead.week.start}: {lead.week.simulators}")
        if lead.target:
            progress = f" ({lead.percent}%)" if lead.percent is not None else ""
            write(f"Next target: {lead.target.people} a week by {lead.target.by}{progress}")
        else:
            write("Every target date has passed.")

        labels = ["week of", "people", *(name.label for name in report.COLUMNS)]
        rows = [[f"{week.start}{'' if week.complete else '*'}", week.simulators, *week.cells]
                for week in recent]
        write("")
        write(_table(labels, rows))
        write("* this week so far")

        rows = [[cohort.start, cohort.people, _share(cohort, 7), _share(cohort, 30)]
                for cohort in report.cohorts(today, count)]
        write("")
        write(_table(["first simulation", "accounts", "again in 7 days", "in 30 days"], rows))


def _share(cohort, days: int) -> str:
    percent = cohort.percent(days)
    return "-" if percent is None else f"{percent}% ({cohort.back[days]})"


def _table(labels: list[str], rows: list[list]) -> str:
    cells = [[str(cell) for cell in row] for row in [labels, *rows]]
    widths = [max(len(row[column]) for row in cells) for column in range(len(labels))]
    return "\n".join("  ".join(cell.rjust(width) for cell, width in zip(row, widths, strict=True))
                     for row in cells)
