"""Fake doors (P9): a button for something not built yet, counted on the server.

"Compare two versions" sat on every finished report with its price, and a
click counted once per browser session. The launch plan's gate G3 holds the
clicks against the reports viewed: at least 5 % of 200 or more. The door
closed with Paket 3, PR 13 (Themen-Doc row 31), when "Compared with your last
run" took its place; its clicks stay counted, and reports viewed still count.

Both counts are deliberately conservative: a report reloaded in the same
session is one view, and a person who clicks on several reports is one click.
"""

from metrics import counts

#: The gate (launch plan, G3): enough views to judge, and the share that passes.
GATE_VIEWS = 200
GATE_PERCENT = 5

_SEEN = "metrics:report_seen"


def report_viewed(request, run) -> None:
    """A finished report was shown; counted unless it was the last one this
    session counted (a reload, a return from the share button)."""
    key = str(run.pk)
    if request.session.get(_SEEN) == key:
        return
    request.session[_SEEN] = key
    counts.add(counts.Name.REPORT_VIEWED)

