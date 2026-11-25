"""The Customisation form (B-10, PR 2)."""

from django import forms
from django.utils.translation import gettext_lazy as _

from looks import colours, presets, services


def _choices(table):
    return [(slug, label) for slug, (label, _css) in table.items()] + [
        (presets.OWN, _("Your own image"))
    ]


class LookForm(forms.Form):
    mat = forms.ChoiceField(choices=_choices(presets.MATS), widget=forms.RadioSelect)
    mat_file = forms.FileField(required=False)
    sleeve = forms.ChoiceField(choices=_choices(presets.SLEEVES), widget=forms.RadioSelect)
    sleeve_file = forms.FileField(required=False)
    inner_sleeve = forms.BooleanField(required=False)
    inner_colour = forms.CharField(required=False, max_length=40)
    seam = forms.BooleanField(required=False)
    seam_colour = forms.CharField(required=False, max_length=40)
    scope = forms.ChoiceField(
        choices=[(services.ALL, _("For all decks")), (services.DECK, _("Only for this deck"))],
        required=False, widget=forms.RadioSelect,
    )

    def __init__(self, *args, targets=None, **kwargs):
        # The look each scope would save to (None: a new one), so «your own
        # image» is checked against the look that keeps it.
        self.targets = targets or {}
        super().__init__(*args, **kwargs)

    def _clean_colour(self, name):
        value = self.cleaned_data.get(name, "")
        return colours.normalise(value) if value.strip() else ""

    def clean_inner_colour(self):
        return self._clean_colour("inner_colour")

    def clean_seam_colour(self):
        return self._clean_colour("seam_colour")

    def clean(self):
        data = super().clean()
        data["scope"] = data.get("scope") or services.ALL
        look = self.targets.get(data["scope"])
        for kind in ("mat", "sleeve"):
            if data.get(f"{kind}_file"):
                # A file chosen means "this one", whichever button is on.
                data[kind] = presets.OWN
            elif data.get(kind) == presets.OWN and not (
                look and getattr(look, f"{kind}_image")
            ):
                self.add_error(f"{kind}_file", _("Choose an image to upload."))
        for flag, colour in (("inner_sleeve", "inner_colour"), ("seam", "seam_colour")):
            if data.get(flag) and not data.get(colour) and colour not in self.errors:
                self.add_error(colour, _("Choose a colour."))
        return data
