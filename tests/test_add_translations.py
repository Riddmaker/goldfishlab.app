"""scripts/add_translations.py: new strings into every catalogue, checked first.

Run against a throw-away locale directory with two languages - German (two
plural forms) and Japanese (one) - so the real catalogues are never touched.
"""

import importlib.util
from pathlib import Path

import polib
import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "add_translations.py"
_spec = importlib.util.spec_from_file_location("add_translations", SCRIPT)
add_translations = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(add_translations)

HEADER = {"de": "nplurals=2; plural=(n != 1);", "ja": "nplurals=1; plural=0;"}


@pytest.fixture
def locale(tmp_path):
    for language, plurals in HEADER.items():
        folder = tmp_path / language / "LC_MESSAGES"
        folder.mkdir(parents=True)
        catalogue = polib.POFile()
        catalogue.metadata = {"Content-Type": "text/plain; charset=UTF-8",
                              "Plural-Forms": plurals}
        catalogue.append(polib.POEntry(msgid="Old", msgstr={"de": "Alt", "ja": "古い"}[language]))
        catalogue.save(str(folder / "django.po"))
    return tmp_path


def _po(locale, language):
    return polib.pofile(str(locale / language / "LC_MESSAGES" / "django.po"))


def test_adds_plain_plural_and_context_entries_and_compiles(locale):
    report = add_translations.apply([
        {"msgid": "Share", "occurrences": ["templates/x.html"], "de": "Teilen", "ja": "共有"},
        {"msgid": "%(count)s view", "msgid_plural": "%(count)s views",
         "de": ["%(count)s Aufruf", "%(count)s Aufrufe"], "ja": ["%(count)s 回"]},
        {"msgctxt": "button", "msgid": "Stop", "de": "Beenden", "ja": "停止"},
    ], locale=locale)

    assert report["de"]["added"] == ["Share", "%(count)s view", "Stop"]
    german = _po(locale, "de")
    assert german.find("Share").msgstr == "Teilen"
    plural = german.find("%(count)s view")
    assert plural.msgstr_plural == {0: "%(count)s Aufruf", 1: "%(count)s Aufrufe"}
    assert "python-format" in plural.flags
    assert german.find("Stop", msgctxt="button").msgstr == "Beenden"
    assert _po(locale, "ja").find("%(count)s view").msgstr_plural == {0: "%(count)s 回"}
    mo = locale / "de" / "LC_MESSAGES" / "django.mo"
    assert mo.read_bytes() == german.to_binary()


def test_an_entry_already_there_is_skipped_unless_replaced(locale):
    entry = {"msgid": "Old", "de": "Neu", "ja": "新"}

    skipped = add_translations.apply([entry], locale=locale)
    assert skipped["de"]["skipped"] == ["Old"]
    assert _po(locale, "de").find("Old").msgstr == "Alt"

    add_translations.apply([entry], locale=locale, replace=True)
    assert _po(locale, "de").find("Old").msgstr == "Neu"


def test_a_dry_run_writes_nothing(locale):
    before = (locale / "de" / "LC_MESSAGES" / "django.po").read_bytes()

    add_translations.apply([{"msgid": "New", "de": "Neu", "ja": "新"}],
                           locale=locale, dry_run=True)

    assert (locale / "de" / "LC_MESSAGES" / "django.po").read_bytes() == before
    assert not (locale / "de" / "LC_MESSAGES" / "django.mo").exists()


@pytest.mark.parametrize(("entry", "complaint"), [
    ({"msgid": "New", "de": "Neu"}, "missing ['ja']"),
    ({"msgid": "New", "de": "Neu", "ja": "新", "fr": "Nouveau"}, "unknown ['fr']"),
    ({"msgid": "New", "de": "", "ja": "新"}, "empty translation"),
    ({"msgid": "%(name)s", "de": "%(nom)s", "ja": "%(name)s"}, "placeholder"),
    ({"msgid": "New", "de": "<b>Neu</b>", "ja": "新"}, "markup"),
    ({"msgid": "a", "msgid_plural": "b", "de": ["x"], "ja": ["y"]}, "2 forms"),
])
def test_a_bad_entry_stops_everything_before_anything_is_written(locale, entry, complaint):
    good = {"msgid": "Fine", "de": "Gut", "ja": "良い"}
    before = (locale / "de" / "LC_MESSAGES" / "django.po").read_bytes()

    with pytest.raises(add_translations.Invalid, match=complaint.replace("[", r"\[")):
        add_translations.apply([good, entry], locale=locale)

    assert (locale / "de" / "LC_MESSAGES" / "django.po").read_bytes() == before


def test_the_real_catalogues_are_the_ones_it_finds():
    assert set(add_translations.catalogues()) == {"de", "es", "fr", "it", "ja", "pt_BR"}
