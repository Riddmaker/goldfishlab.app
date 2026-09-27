"""Forms for creating and importing decks.

The upload field is the application's only untrusted input in Phase 1, so the
validation here is the security boundary: size before decode, decode before
parse, parse before anything touches the database.
"""

from django import forms

from decks import importers
from decks.importers import columns
from decks.models import Deck
from decks.services import MAX_UPLOAD_BYTES


class DeckForm(forms.ModelForm):
    """Create or rename a deck by hand."""

    class Meta:
        model = Deck
        fields = ["name", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Chainer, Dementia Master"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class ImportForm(forms.Form):
    """Upload a deck list.

    `format` is optional: the registry sniffs first, and only asks when it is
    not confident. Offering the picker up front would train users to pick
    wrongly when the sniff would have been right.
    """

    name = forms.CharField(
        max_length=120,
        required=False,
        help_text="Leave blank to name the deck after the file.",
    )
    file = forms.FileField(
        help_text=(
            "Any CSV or TSV export, or a plain text list, up to "
            f"{MAX_UPLOAD_BYTES // 1_000_000} MB. Spreadsheets are not "
            "accepted - see the note below."
        ),
    )
    format = forms.ChoiceField(
        required=False,
        choices=[("", "Detect automatically")],
        help_text="Only needed if the file is not recognised.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["format"].choices = [("", "Detect automatically"), *importers.choices()]

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"That file is {upload.size // 1024} KB. "
                f"The limit is {MAX_UPLOAD_BYTES // 1024} KB "
                "- a deck list is a few dozen."
            )
        return upload


#: The two concepts worth stopping somebody for. A missing card name means
#: nothing can be read at all; a missing quantity means everything reads as a
#: one-of, which is the bug that cost 27 Swamps and said nothing. Every other
#: concept defaults quietly to "not in this file", because interrupting
#: somebody over an unmatched `language` column teaches them to click through
#: this screen without reading it.
MUST_ANSWER = (columns.NAME, columns.QUANTITY)


class ColumnMappingForm(forms.Form):
    """Which column of *this* file means which thing.

    One dropdown per concept, and the choices are **this file's own headers**
    plus "not in this file". That bounds the answer to something real - a person
    cannot map a concept to a column that does not exist, and neither can
    anything posting at this endpoint on their behalf. `columns.map_headers`
    validates the same thing again on the way through, because a form is a
    convenience and a boundary is a boundary.

    The screen only appears when `Mapping.needs_confirmation` is true, and it
    **refuses to be clicked through**: whichever of name and quantity we could
    not match starts blank and is required, so the answer has to be somebody's
    rather than a default's. Defaulting the missing quantity to "not in this
    file" would have re-created the original bug behind one extra button.
    """

    def __init__(self, *args, mapping: columns.Mapping, **kwargs):
        super().__init__(*args, **kwargs)
        self.mapping = mapping

        # Deduplicated, order preserved: a file with two columns called the
        # same thing is legal CSV and would otherwise offer the same choice
        # twice.
        seen: dict[str, None] = {}
        for header in mapping.headers:
            if header and header.strip():
                seen.setdefault(header, None)
        available = [(header, header) for header in seen]

        for column in columns.MAPPABLE:
            current = mapping.header_for(column)
            must_answer = column in MUST_ANSWER and not current

            choices = [(columns.ABSENT, "not in this file")] + available
            if must_answer:
                choices.insert(0, ("", "choose a column"))

            self.fields[column.key] = forms.ChoiceField(
                choices=choices,
                initial=current if current else ("" if must_answer else columns.ABSENT),
                label=column.label.capitalize(),
                # Required exactly where an answer is genuinely needed, which
                # is the same place the blank choice was added. Everything else
                # falls back to what the aliases matched, so a post that omits
                # a field keeps the guess rather than silently unsetting it.
                required=must_answer,
                error_messages={
                    "required": f"Choose which column holds the {column.label}, "
                                "or say the file has not got one.",
                },
            )

    @property
    def overrides(self) -> dict:
        """The answer, in the shape `columns.map_headers` takes.

        A field that was not posted falls back to its initial - the header the
        aliases matched, or "not in this file". The two concepts that *must* be
        answered cannot reach this fallback, because when they are unmatched
        they are required and the form will not have validated without them.
        """
        answer = {}
        for column in columns.MAPPABLE:
            chosen = self.cleaned_data.get(column.key) or ""
            answer[column.key] = chosen or self.fields[column.key].initial or columns.ABSENT
        return answer
