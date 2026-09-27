"""The one untrusted input this app has.

Same boundary as the deck importer, in the same order: size before decode,
decode before parse, parse before anything touches the database. A collection
export is bigger than a deck list - a few hundred rows rather than a hundred -
but not by enough to want a second limit, so it reuses `MAX_UPLOAD_BYTES`.
"""

from django import forms

from decks import importers
from decks.services import MAX_UPLOAD_BYTES


class CollectionImportForm(forms.Form):
    """Upload a collection export."""

    file = forms.FileField(
        help_text=(
            "Any CSV or TSV collection export, up to "
            f"{MAX_UPLOAD_BYTES // 1_000_000} MB. The same file a deck import "
            "takes - a collection export is simply a longer one."
        ),
    )
    format = forms.ChoiceField(
        required=False,
        choices=[("", "Detect automatically")],
        help_text="Only needed if the file is not recognised.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["format"].choices = [
            ("", "Detect automatically"), *importers.choices()
        ]

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"That file is {upload.size // 1024} KB. "
                f"The limit is {MAX_UPLOAD_BYTES // 1024} KB."
            )
        return upload
