"""Add new strings and their translations to every catalogue, then compile.

Usage:  .venv/bin/python scripts/add_translations.py strings.json [--replace] [--dry-run]

`makemessages` needs GNU xgettext, and the local one rewraps thousands of
lines of every .po (found in P1); a throw-away polib script per feature was
the workaround until P4. This is that script, once. The file is a list of
entries, each with the English source and one translation per language:

    [
      {"msgid": "Share this report",
       "occurrences": ["templates/simulations/_share.html"],
       "de": "Diesen Report teilen", "fr": "...", "it": "...", "es": "...",
       "pt_BR": "...", "ja": "..."},
      {"msgid": "%(count)s view", "msgid_plural": "%(count)s views",
       "flags": ["python-format"],
       "de": ["%(count)s Aufruf", "%(count)s Aufrufe"], "...": "..."},
      {"msgctxt": "navigation", "msgid": "Share", "...": "..."}
    ]

A plural takes a list with exactly as many forms as the language has
(Spanish three, Japanese one). `python-format` is added by itself when the
English has a %(name)s placeholder. Every entry must name every catalogue
under locale/, and is checked the way tests/test_i18n.py checks them - no
placeholder or markup the English lacks - before anything is written.

An entry already in a catalogue (same msgid and msgctxt) is left alone and
reported, unless --replace is given. Saved with polib's default wrap width,
which is the one the catalogues use (wrapwidth=0 rewraps every file).
"""

import argparse
import json
import re
import sys
from pathlib import Path

import polib

LOCALE = Path(__file__).resolve().parent.parent / "locale"
#: The same two patterns tests/test_i18n.py checks the catalogues with.
PLACEHOLDER = re.compile(r"%\(\w+\)[sdif]|%[sdif]")
TAG = re.compile(r"</?[a-zA-Z][^>]*>")
#: Keys of an entry that are not a language.
SOURCE_KEYS = {"msgid", "msgid_plural", "msgctxt", "occurrences", "flags", "comment"}


class Invalid(ValueError):
    """The file asks for something a catalogue must not get."""


def catalogues(locale: Path = LOCALE) -> dict[str, Path]:
    """Language directory name -> its django.po."""
    return {path.parts[-3]: path for path in sorted(locale.glob("*/LC_MESSAGES/django.po"))}


def _forms(catalogue: polib.POFile) -> int:
    return int(re.search(r"nplurals=(\d+)", catalogue.metadata["Plural-Forms"]).group(1))


def _check_text(source: str, text: str, where: str) -> None:
    if not text.strip():
        raise Invalid(f"{where}: empty translation")
    if not set(PLACEHOLDER.findall(text)) <= set(PLACEHOLDER.findall(source)):
        raise Invalid(f"{where}: a placeholder the English does not have: {text!r}")
    if not set(TAG.findall(text)) <= set(TAG.findall(source)):
        raise Invalid(f"{where}: markup the English does not have: {text!r}")


def validate(entries: list[dict], languages: dict[str, polib.POFile]) -> None:
    """Raise `Invalid` for the first thing wrong; write nothing."""
    if not isinstance(entries, list):
        raise Invalid("the file must hold a list of entries")
    seen = set()
    for number, entry in enumerate(entries, start=1):
        msgid = entry.get("msgid")
        if not msgid:
            raise Invalid(f"entry {number}: no msgid")
        key = (entry.get("msgctxt"), msgid)
        if key in seen:
            raise Invalid(f"entry {number}: {msgid!r} twice in the file")
        seen.add(key)
        missing = set(languages) - set(entry)
        unknown = set(entry) - set(languages) - SOURCE_KEYS
        if missing or unknown:
            raise Invalid(f"entry {number} ({msgid!r}): missing {sorted(missing)}, "
                          f"unknown {sorted(unknown)}")
        plural = entry.get("msgid_plural")
        for language, catalogue in languages.items():
            where = f"entry {number} ({msgid!r}), {language}"
            text = entry[language]
            if plural:
                if not isinstance(text, list) or len(text) != _forms(catalogue):
                    raise Invalid(f"{where}: needs a list of {_forms(catalogue)} forms")
                for index, form in enumerate(text):
                    # Form 0 is the singular, except in a one-form language.
                    source = msgid if index == 0 and len(text) > 1 else plural
                    _check_text(source, form, where)
            else:
                if not isinstance(text, str):
                    raise Invalid(f"{where}: needs a string")
                _check_text(msgid, text, where)


def _entry(entry: dict, text) -> polib.POEntry:
    flags = list(entry.get("flags", []))
    sources = [entry["msgid"], entry.get("msgid_plural") or ""]
    if "python-format" not in flags and any(PLACEHOLDER.search(s) for s in sources):
        flags.append("python-format")
    fields = {
        "msgid": entry["msgid"],
        "occurrences": [(path, "") for path in entry.get("occurrences", [])],
        "flags": flags,
        "comment": entry.get("comment", ""),
    }
    if entry.get("msgctxt"):
        fields["msgctxt"] = entry["msgctxt"]
    if entry.get("msgid_plural"):
        fields["msgid_plural"] = entry["msgid_plural"]
        fields["msgstr_plural"] = dict(enumerate(text))
    else:
        fields["msgstr"] = text
    return polib.POEntry(**fields)


def apply(entries: list[dict], *, locale: Path = LOCALE, replace: bool = False,
          dry_run: bool = False) -> dict[str, dict[str, list[str]]]:
    """Add (or with `replace`, overwrite) every entry in every catalogue.

    Returns, per language, the msgids added, replaced and skipped.
    """
    paths = catalogues(locale)
    languages = {language: polib.pofile(str(path)) for language, path in paths.items()}
    validate(entries, languages)
    report = {}
    for language, catalogue in languages.items():
        done = {"added": [], "replaced": [], "skipped": []}
        for entry in entries:
            new = _entry(entry, entry[language])
            old = catalogue.find(entry["msgid"], msgctxt=entry.get("msgctxt") or False)
            if old is None:
                catalogue.append(new)
                done["added"].append(entry["msgid"])
            elif replace:
                old.msgstr, old.msgstr_plural = new.msgstr, new.msgstr_plural
                old.flags = sorted(set(old.flags) - {"fuzzy"} | set(new.flags))
                old.obsolete = False
                done["replaced"].append(entry["msgid"])
            else:
                done["skipped"].append(entry["msgid"])
        if not dry_run and (done["added"] or done["replaced"]):
            catalogue.save(str(paths[language]))
        report[language] = done
    if not dry_run:
        # What scripts/compilemessages.py does, for this locale directory.
        for path in locale.glob("*/LC_MESSAGES/*.po"):
            polib.pofile(str(path)).save_as_mofile(str(path.with_suffix(".mo")))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file", type=Path, help="JSON list of entries")
    parser.add_argument("--replace", action="store_true",
                        help="overwrite translations that are already there")
    parser.add_argument("--dry-run", action="store_true", help="check, write nothing")
    args = parser.parse_args(argv)
    try:
        report = apply(json.loads(args.file.read_text(encoding="utf-8")),
                       replace=args.replace, dry_run=args.dry_run)
    except Invalid as exc:
        print(f"nothing written: {exc}", file=sys.stderr)
        return 1
    for language, done in report.items():
        counts = ", ".join(f"{len(ids)} {what}" for what, ids in done.items())
        print(f"{language}: {counts}")
        for msgid in done["skipped"]:
            print(f"  already there (use --replace): {msgid!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
