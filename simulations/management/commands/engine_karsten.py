"""The engine against Frank Karsten's coloured-source table (P16).

    manage.py engine_karsten                    # 1,000,000 games, about 4 minutes
    manage.py engine_karsten --games 50000      # a quick look

For every mana cost of Karsten's 99-card table: his number of sources, the
chance Karsten's own mulligan rule gives at that number and at one fewer (his
counting, reproduced here), and the number the engine's mulligan rule and draws
need. The engine column is `karsten.REQUIRED`, which the methodology page
prints; a mismatch fails the command, so the page cannot quietly go stale.
"""

from django.core.management.base import BaseCommand, CommandError

from simulation import karsten


class Command(BaseCommand):
    help = "Compare the engine with Frank Karsten's coloured-source table."

    def add_arguments(self, parser):
        parser.add_argument("--games", type=int, default=1_000_000)
        parser.add_argument("--seed", type=int, default=2022)

    def handle(self, *args, **options):
        games, seed = options["games"], options["seed"]
        engine = karsten.engine_tally(games, seed)
        asked = sorted({sources for cost in karsten.COSTS
                        for sources in (karsten.PUBLISHED[cost.text] - 1,
                                        karsten.PUBLISHED[cost.text])})
        his = {sources: karsten.karsten_tally(sources, games, seed) for sources in asked}

        self.stdout.write("cost    target  Karsten  his rule here (n / n-1)  engine  kept")
        stale = []
        for cost in karsten.COSTS:
            published = karsten.PUBLISHED[cost.text]
            needed = engine.required(cost)
            if needed != karsten.REQUIRED[cost.text]:
                stale.append(cost.text)
            self.stdout.write(
                f"{cost.text:7} {cost.target:5} % {published:7}  "
                f"{his[published].chance(cost, published):7.2%} / "
                f"{his[published - 1].chance(cost, published - 1):7.2%}"
                f"      {needed!s:>6}  {karsten.REQUIRED[cost.text]:4}")
        if stale:
            raise CommandError(f"karsten.REQUIRED is stale for {', '.join(stale)}")
