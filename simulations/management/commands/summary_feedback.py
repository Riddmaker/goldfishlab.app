"""Every feedback on deck summaries, as cases to work through.

    python manage.py summary_feedback [--since 2026-11-01] [--unhelpful]
        [--out .data/summary_feedback.jsonl]

One JSON line a feedback, the unhelpful ones first: the answer and comment,
the model and its reasoning effort, the prompt version and language, the
facts that were sent, Mistral's raw answer and the checked one. When the
prompt version is today's, ``messages`` is the whole prompt rebuilt; an older
one is in git at that `summary.PROMPT_VERSION`. Nobody is named: the user is
left out. The file goes to ``.data/`` and is not committed.
"""

import json
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from simulations import summary
from simulations.models import SummaryFeedback


class Command(BaseCommand):
    help = "Write the feedback on deck summaries, with each whole case, to a JSONL file."

    def add_arguments(self, parser):
        parser.add_argument("--since", help="first day, YYYY-MM-DD")
        parser.add_argument("--unhelpful", action="store_true",
                            help="only the summaries marked not helpful")
        parser.add_argument("--out", default=str(Path(settings.BASE_DIR) / ".data"
                                                 / "summary_feedback.jsonl"))

    def handle(self, *args, **options):
        rows = SummaryFeedback.objects.order_by("helpful", "-created_at")
        if options["since"]:
            try:
                rows = rows.filter(created_at__date__gte=date.fromisoformat(options["since"]))
            except ValueError:
                raise CommandError("--since takes a date, YYYY-MM-DD") from None
        if options["unhelpful"]:
            rows = rows.filter(helpful=False)
        out = Path(options["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out.open("w", encoding="utf-8") as file:
            for row in rows.iterator():
                case = {
                    "id": str(row.pk), "created_at": row.created_at.isoformat(),
                    "helpful": row.helpful, "comment": row.comment,
                    "model": row.model_name, "reasoning_effort": row.reasoning_effort,
                    "prompt_version": row.prompt_version, "language": row.language,
                    "facts": row.facts, "raw_answer": row.raw_answer, "content": row.content,
                }
                if row.prompt_version == summary.PROMPT_VERSION and row.facts:
                    case["messages"] = summary.messages(row.facts, row.language)
                file.write(json.dumps(case, ensure_ascii=False) + "\n")
                count += 1
        self.stdout.write(f"{count} feedback written to {out}")
