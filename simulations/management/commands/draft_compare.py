"""Compare language models drafting a card's building blocks (card model phase 5).

    python manage.py draft_compare --models mistral-medium-2604 <larger model>
        [--cards 60] [--examples 6] [--seed 1] [--out .data/draft_compare.json]

Run by hand on a database with derived profiles. Each model gets the same
cards and the same examples; the report says per model how many cards it got
entirely right, per building block what it got right, missed or added, and
what it cost in tokens. The answers go to ``--out`` for reading; nothing is
stored in the database.
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cards.models import DerivedProfile
from simulations import drafts, mistral


class Command(BaseCommand):
    help = "Compare language models drafting card building blocks on cards we read."

    def add_arguments(self, parser):
        parser.add_argument("--models", nargs="+", required=True)
        parser.add_argument("--cards", type=int, default=60)
        parser.add_argument("--examples", type=int, default=6)
        parser.add_argument("--seed", type=int, default=1)
        parser.add_argument("--out", default=str(Path(settings.BASE_DIR) / ".data"
                                                 / "draft_compare.json"))

    def handle(self, *args, **options):
        try:
            known = mistral.available_models()
        except mistral.MistralError as exc:
            raise CommandError(str(exc)) from None
        unknown = [name for name in options["models"] if name not in known]
        if unknown:
            near = sorted(name for name in known if "large" in name or "medium" in name)
            raise CommandError(f"no such model: {', '.join(unknown)}; "
                               f"large and medium ones: {', '.join(near)}")
        profiles = DerivedProfile.objects.select_related("oracle_card").filter(
            needs_review=False)
        chosen = drafts.sample(profiles, options["cards"] + options["examples"],
                               options["seed"])
        examples = [(profile.oracle_card, drafts.truth(profile))
                    for profile in chosen[:options["examples"]]]
        cards = chosen[options["examples"]:]
        scores = drafts.compare(options["models"], cards, examples)
        out = Path(options["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"seed": options["seed"],
                                   "examples": [card.name for card, _ in examples],
                                   "scores": [score.summary() | {"answers": score.answers}
                                              for score in scores]},
                                  indent=1, ensure_ascii=False), encoding="utf-8")
        for score in scores:
            self.stdout.write(f"{score.model}: {score.exact}/{score.cards} cards exact, "
                              f"{score.unusable} unusable, "
                              f"{score.prompt_tokens}+{score.completion_tokens} tokens, "
                              f"{score.seconds:.0f} s")
            self.stdout.write(f"  right {score.right}")
            self.stdout.write(f"  missed {score.missed}")
            self.stdout.write(f"  extra {score.extra}")
        self.stdout.write(f"answers written to {out}")
