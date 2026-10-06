"""Fake doors (P9): a button for something not built yet, counted on the server.

"Compare two versions" sits on every finished report with its price. A click
counts once per browser session and answers honestly that the feature does
not exist yet; nothing is charged or signed up. The launch plan's gate G3
holds the clicks against the reports viewed: at least 5 % of 200 or more.

Both counts are deliberately conservative: a report reloaded in the same
session is one view, and a person who clicks on several reports is one click.
"""

from metrics import counts

#: The price shown with the door: the plan large comparison series would come
#: with (launch plan P5/P15). No plan name until P5 renames the plans.
COMPARE_PRICE_CHF = 9

#: The gate (launch plan, G3): enough views to judge, and the share that passes.
GATE_VIEWS = 200
GATE_PERCENT = 5

_ASKED = "metrics:compare_asked"
_SEEN = "metrics:report_seen"


def report_viewed(request, run) -> None:
    """A finished report was shown; counted unless it was the last one this
    session counted (a reload, a return from the share button)."""
    key = str(run.pk)
    if request.session.get(_SEEN) == key:
        return
    request.session[_SEEN] = key
    counts.add(counts.Name.REPORT_VIEWED)


def asked(request) -> bool:
    """Has this browser session clicked "Compare two versions" already?"""
    return bool(request.session.get(_ASKED))


def ask(request) -> None:
    if asked(request):
        return
    request.session[_ASKED] = True
    counts.add(counts.Name.COMPARE_CLICKED)
