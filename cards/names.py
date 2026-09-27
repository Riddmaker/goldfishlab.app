"""Card-name normalisation.

Every name comparison in the application goes through here. Scattering
`.lower()` calls across the importer is how two code paths end up disagreeing
about whether "Lim-Dul's Vault" matches "Lim-Dul's Vault".

Two rules earned their place the hard way during the deck research:

1. **Double-faced cards match on the front face only.** Scryfall stores them as
   ``"Tergrid, God of Fright // Tergrid's Lantern"``, but every deck list, every
   CSV export and every human writes only ``Tergrid, God of Fright``.
2. **Apostrophes are not one character.** Exports mix U+2019 (') with U+0027 ('),
   so both fold to the same normalised form.
"""

import re
import unicodedata

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_SPACE = re.compile(r"\s+")


def front_face(name: str) -> str:
    """The front-face half of a double-faced card name.

    ``"Tergrid, God of Fright // Tergrid's Lantern"`` -> ``"Tergrid, God of Fright"``.
    Names without a ``//`` are returned unchanged.
    """
    return name.split("//")[0].strip()


def normalise(name: str) -> str:
    """A name reduced to its comparable core.

    Front face only, accents stripped, punctuation dropped, whitespace
    collapsed, casefolded. Deliberately lossy: it is the last rung of the
    import resolution ladder, used only after the exact matches have failed.
    """
    text = front_face(name)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _PUNCT.sub(" ", text)
    return _SPACE.sub(" ", text).strip().casefold()
