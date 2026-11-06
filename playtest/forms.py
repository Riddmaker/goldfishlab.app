"""Turning a form post into an engine action.

Every action the board offers arrives as a real `<form method="post">`, so this
is the boundary where untrusted input becomes an `Action`. Two rules:

1. **`kind` picks from a list**, never from anything importable. The list is
   `actions.BY_KIND` and the free actions (`playtest.free.BY_KIND`), the same
   registers the database replays through. Which of them a session accepts is
   its mode's business (`services.kinds`).
2. **Only the fields that action actually has are read.** A post carrying
   `{"kind": "draw", "index": 7}` yields `Draw(count=...)` and nothing else -
   a stray key cannot become a keyword argument, which is how
   `Action(**payload)` would have gone wrong.

Zones are validated against `actions.ZONES` rather than left to the engine,
because a bad zone from a form is a 400 and a bad zone from a replay is a bug,
and the two should not share an error path.
"""

import dataclasses

from django import forms

from playtest import free
from playtest.models import PlaytestSession
from simulation import actions

#: The most cards a single `Draw` may ask for. A goldfish draws one at a time;
#: this is here so that a hand-edited form cannot ask for a million.
MAX_DRAW = 20

#: Life is a small integer in both directions - Necropotence pays a lot.
MIN_LIFE, MAX_LIFE = -100, 999


class ActionForm(forms.Form):
    """One action, as the board posts it."""

    kind = forms.ChoiceField(
        choices=lambda: [(kind, kind) for kind in sorted({*actions.BY_KIND, *free.BY_KIND})],
    )
    index = forms.IntegerField(required=False, min_value=0, max_value=500)
    count = forms.IntegerField(required=False, min_value=1, max_value=MAX_DRAW)
    #: X for a spell with {X} in its cost (P19 R9); the engine checks that
    #: the pool pays for it.
    x = forms.IntegerField(required=False, min_value=0, max_value=99)
    #: How an additional cost is paid, or what an altar sacrifices (P19 R15):
    #: an index into the options the board offered; the engine checks it.
    payment = forms.IntegerField(required=False, min_value=0, max_value=200)
    #: The creature an Equipment goes on (P19 R22): an index into the ones
    #: the board offered; the engine checks it.
    target = forms.IntegerField(required=False, min_value=0, max_value=500)
    total = forms.IntegerField(required=False, min_value=MIN_LIFE, max_value=MAX_LIFE)
    zone = forms.ChoiceField(required=False,
                             choices=[(zone, zone) for zone in actions.ZONES])
    from_zone = forms.ChoiceField(required=False,
                                  choices=[(zone, zone) for zone in actions.ZONES])
    to_zone = forms.ChoiceField(required=False,
                                choices=[(zone, zone) for zone in actions.ZONES])

    def action(self) -> actions.Action:
        """The action this post describes.

        Only valid after `is_valid()`. Fields the action does not declare are
        dropped rather than passed on, and fields it declares but the post left
        empty keep the action's own default.
        """
        kind = self.cleaned_data["kind"]
        cls = free.BY_KIND.get(kind) or actions.BY_KIND[kind]
        kwargs = {
            field.name: self.cleaned_data[field.name]
            for field in dataclasses.fields(cls)
            if self.cleaned_data.get(field.name) not in (None, "")
        }
        return cls(**kwargs)


class ForkForm(forms.Form):
    """Branch a session at one action."""

    seq = forms.IntegerField(min_value=0, max_value=100_000)


class StartForm(forms.Form):
    """Open a session. The seed is optional so that a game can be shared."""

    #: Checked means the two-player rule: the starting player skips the first
    #: draw. Unchecked is multiplayer Commander, where nobody does (CR 103.8c).
    #: The deck page posts its "First draw" dropdown here as "0" or "1" (one
    #: choice for both buttons), which a checkbox widget reads as checked.
    on_the_play = forms.BooleanField(required=False, initial=False)
    seed = forms.IntegerField(required=False, min_value=0, max_value=2**62)
    #: Free unless the player asks for strict (PR 18): a hand dealt from the
    #: deck page is for trying lines, and the rules engine is one switch away.
    mode = forms.ChoiceField(required=False, choices=PlaytestSession.MODES)

    def clean_mode(self) -> str:
        return self.cleaned_data.get("mode") or PlaytestSession.FREE

    def clean_on_the_play(self) -> bool:
        return str(self.data.get("on_the_play", "")).lower() in {"1", "on", "true"}
