"""The forms: starting a simulation, and recording a judgement about a card.

`RunForm` is small but not skippable. The three fields it validates go straight
into a worker's workload, so "games" arriving as `999999999` from a crafted
POST is the difference between a simulation and a denial of service. The plan's
own limits are checked again in `services.start_run`; this form is the friendly
first line, not the security boundary.

`AnnotationForm` writes the judgements the engine cannot derive. The rule it
obeys, and the one worth reading `simulations/annotations.py` for: **an empty
field means "no opinion" and removes the override.** Every field is therefore
three-state - yes, no, and nobody said - and none of them is a plain checkbox,
because a checkbox cannot say the third thing and would quietly save "no" for
every card it was never asked about.
"""

from django import forms

from simulations.annotations import (
    BY_KEY,
    JUDGEMENTS,
    KIND_CHOICES,
    ROLE_CHOICES,
    SUBTYPE_CHOICES,
    ManaTextError,
    format_mana,
    parse_mana,
)
from simulations.engine.adapter import DECK_SCOPE, USER_SCOPE

#: Offered sizes. Ten thousand games already gives roughly +/-0.5 percentage
#: points at 95% confidence, which is why the interface meters turns rather
#: than iterations - selling a bigger number would sell precision the answer
#: does not gain.
GAME_CHOICES = (
    (1_000, "1,000 games - quick look"),
    (10_000, "10,000 games - the usual answer"),
    (50_000, "50,000 games - for small differences"),
)

TURN_CHOICES = tuple((turns, f"{turns} turns") for turns in (3, 4, 5, 6, 8, 10))


class RunForm(forms.Form):
    """How many games, how many turns, and which side of the table."""

    games = forms.TypedChoiceField(
        choices=GAME_CHOICES, coerce=int, initial=10_000, label="Games",
    )
    turns = forms.TypedChoiceField(
        choices=TURN_CHOICES, coerce=int, initial=6, label="Turns per game",
    )
    # Stored as `on_the_play`, and True still means "skip the first draw" -
    # every stored run and the golden snapshot are keyed on it. Only the words
    # changed. The old help text argued that a four-player seat is "on the
    # draw" three times out of four; the rule is simpler than that, and it cuts
    # the other way: in a multiplayer game other than Two-Headed Giant nobody
    # skips their first draw (CR 103.8c), so drawing on turn one is right in
    # every seat. Skipping it is the two-player rule, for a 1-v-1 game.
    on_the_play = forms.TypedChoiceField(
        choices=(
            (0, "Multiplayer - everyone draws on turn one"),
            (1, "1-v-1 - you start and skip your first draw"),
        ),
        coerce=lambda value: bool(int(value)),
        initial=0,
        label="First draw",
        help_text=(
            "In a multiplayer Commander game nobody skips the draw on their first "
            "turn (Comprehensive Rules 103.8c). Only a two-player game makes the "
            "starting player skip it."
        ),
    )


#: The two scopes a user may write. **`builtin` is deliberately absent.** A
#: built-in annotation applies to every deck of every user, which makes it
#: exactly the wrong place for "in my deck, Ashnod's Altar is a sac engine" -
#: and a form that offered it would let one user's judgement about one deck
#: change everybody else's numbers.
SCOPE_CHOICES = (
    (DECK_SCOPE, "This deck only"),
    (USER_SCOPE, "All of my decks"),
)

#: Yes, no, and the one a checkbox cannot say.
NO_OPINION = ""
TRISTATE_CHOICES = (
    (NO_OPINION, "Nobody has said"),
    ("1", "Yes"),
    ("0", "No"),
)


def _tristate(key: str) -> forms.ChoiceField:
    """A yes/no/unsaid field, labelled from the judgement vocabulary."""
    judgement = BY_KEY[key]
    return forms.ChoiceField(
        choices=TRISTATE_CHOICES,
        required=False,
        label=judgement.label,
        help_text=judgement.help,
    )


def _as_bool(value):
    """``"1"`` and ``"0"`` from a tristate; `None` for "nobody has said"."""
    if value in (None, NO_OPINION):
        return None
    return value == "1"


class AnnotationForm(forms.Form):
    """One user's judgement about one card, in one scope.

    **Never pre-filled from the derived reading.** The fields start empty
    unless an annotation at this scope already fills them, and the derived
    reading is shown beside them instead. Pre-filling would mean that saving
    the form without touching anything froze a community tag or a regex match
    into a human judgement - and the provenance panel would then credit the
    user with a decision they never made, which is the one thing it exists not
    to do.
    """

    scope = forms.ChoiceField(
        choices=SCOPE_CHOICES,
        initial=DECK_SCOPE,
        label="Applies to",
        help_text=(
            "A deck-scoped judgement wins over one you set for every deck, "
            "which wins over the application's own default."
        ),
    )

    priority = forms.IntegerField(
        required=False, min_value=0, max_value=100,
        label=BY_KEY["priority"].label, help_text=BY_KEY["priority"].help,
    )
    kind = forms.ChoiceField(
        choices=((NO_OPINION, "Nobody has said"), *KIND_CHOICES),
        required=False,
        label=BY_KEY["kind"].label, help_text=BY_KEY["kind"].help,
    )
    mana_produces = forms.CharField(
        required=False, max_length=64,
        label=BY_KEY["mana_produces"].label, help_text=BY_KEY["mana_produces"].help,
    )

    replace_tags = forms.BooleanField(
        required=False,
        label="Replace the roles below",
        help_text=(
            "Leave this alone to keep the community roles. Tick it and the "
            "selection below becomes the complete list - including an empty "
            "one, which is how you say a card has no role at all."
        ),
    )
    tags = forms.MultipleChoiceField(
        choices=ROLE_CHOICES, required=False,
        label=BY_KEY["tags"].label, help_text=BY_KEY["tags"].help,
    )

    replace_subtypes = forms.BooleanField(
        required=False,
        label="Replace the land types below",
        help_text=(
            "Same rule as the roles: leave it alone to keep what the type line "
            "says, tick it to state the complete list. Ticking it with nothing "
            "selected is how you stop a land tapping for a colour at all."
        ),
    )
    subtypes = forms.MultipleChoiceField(
        choices=SUBTYPE_CHOICES, required=False,
        label=BY_KEY["subtypes"].label, help_text=BY_KEY["subtypes"].help,
    )

    tutor_count = forms.IntegerField(
        required=False, min_value=0, max_value=10,
        label=BY_KEY["tutor_count"].label, help_text=BY_KEY["tutor_count"].help,
    )
    mana_activation = forms.IntegerField(
        required=False, min_value=0, max_value=10,
        label=BY_KEY["mana_activation"].label, help_text=BY_KEY["mana_activation"].help,
    )

    note = forms.CharField(
        required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 2}),
        label="Why",
        help_text="Shown beside the value on the provenance panel. Optional, and worth it.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Built in a loop rather than declared three times, so that the
        # tristates cannot drift apart from each other or from the vocabulary.
        for key in ("accelerant", "goldfish_castable", "enters_tapped", "untaps"):
            self.fields[key] = _tristate(key)
        self.order_fields(
            ["scope", *[judgement.key for judgement in JUDGEMENTS],
             "replace_tags", "replace_subtypes", "note"]
        )

    def clean_mana_produces(self):
        text = self.cleaned_data.get("mana_produces", "")
        try:
            return parse_mana(text)
        except ManaTextError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def judgements(self) -> dict:
        """The editable keys, with `None` wherever nobody expressed an opinion.

        Handed straight to `annotations.apply`, which is what turns a `None`
        into a removed key rather than into a stored zero.
        """
        data = self.cleaned_data
        return {
            "priority": data.get("priority"),
            "accelerant": _as_bool(data.get("accelerant")),
            "goldfish_castable": _as_bool(data.get("goldfish_castable")),
            "enters_tapped": _as_bool(data.get("enters_tapped")),
            "untaps": _as_bool(data.get("untaps")),
            "kind": data.get("kind") or None,
            "mana_produces": data.get("mana_produces"),
            # An empty list is a real answer - "this card has no roles at all"
            # - so it is only `None` when the box saying so is unticked.
            "tags": list(data.get("tags") or []) if data.get("replace_tags") else None,
            "subtypes": (
                list(data.get("subtypes") or [])
                if data.get("replace_subtypes") else None
            ),
            # Zero is a real answer here - "this is not a tutor" - and an empty
            # box is still no opinion, so this cannot go through `or None`.
            "tutor_count": data.get("tutor_count"),
            # Zero is a statement too: "it only has to tap".
            "mana_activation": data.get("mana_activation"),
        }

    @staticmethod
    def initial_for(annotation) -> dict:
        """Fill the form from an existing annotation, and from nothing else."""
        if annotation is None:
            return {}
        overrides = annotation.overrides or {}
        initial = {"note": annotation.note}
        if "priority" in overrides:
            initial["priority"] = overrides["priority"]
        if "kind" in overrides:
            initial["kind"] = overrides["kind"]
        if "mana_produces" in overrides:
            initial["mana_produces"] = format_mana(overrides["mana_produces"])
        if "tags" in overrides:
            initial["replace_tags"] = True
            initial["tags"] = list(overrides["tags"])
        if "subtypes" in overrides:
            initial["replace_subtypes"] = True
            initial["subtypes"] = list(overrides["subtypes"])
        for key in ("accelerant", "goldfish_castable", "enters_tapped", "untaps"):
            if key in overrides:
                initial[key] = "1" if overrides[key] else "0"
        if "mana_activation" in overrides:
            initial["mana_activation"] = overrides["mana_activation"]
        return initial
