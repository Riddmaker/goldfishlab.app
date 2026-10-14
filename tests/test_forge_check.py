"""Card model phase 2, PR 6: a new Forge pin checked against the old one.

The weekly sync (.github/workflows/forge-sync.yml) runs `manage.py forge_check`
without a database, so nothing here touches one: a query would fail the test.
The scripts are made up in the format's shape; none is copied from Forge.
"""

import pytest
from django.core.management import CommandError, call_command

from cards import forge, forge_import
from tests.conftest import FORGE_COMMIT, FORGE_SCRIPTS

NEW_COMMIT = "e" * 40

#: Reads more than the Sol Ring of the old pin's: nothing left open.
SOL_RING_UNKNOWN = ("Name:Sol Ring\nManaCost:1\nTypes:Artifact\n"
                    "A:AB$ ZzzMadeUpApi | Cost$ T\n")
DRUID = ("Name:Test Druid\nManaCost:G\nTypes:Creature Elf Druid\nPT:1/1\n"
         "A:AB$ Mana | Cost$ T | Produced$ G\n")


def _pack(tmp_path, name, scripts, commit):
    path = tmp_path / name
    forge.pack(scripts.items(), commit, "2026-10-20T08:00:00+00:00", path)
    return path


@pytest.fixture
def old(tmp_path):
    return _pack(tmp_path, "old.jsonl.gz", FORGE_SCRIPTS, FORGE_COMMIT)


def test_the_same_scripts_are_no_change(tmp_path, old):
    new = _pack(tmp_path, "new.jsonl.gz", FORGE_SCRIPTS, NEW_COMMIT)

    found = forge_import.compare(old, new)

    assert (found.old_commit, found.new_commit) == (FORGE_COMMIT, NEW_COMMIT)
    assert found.scripts == 3
    assert found.changes == 0
    assert found.blocks_changed == found.failed == []


def test_added_removed_and_changed_scripts_are_named(tmp_path, old):
    scripts = dict(FORGE_SCRIPTS)
    del scripts["z/zz_not_a_card.txt"]
    scripts["t/test_druid.txt"] = DRUID
    scripts["l/llanowar_elves.txt"] += "Oracle:{T}: Add {G}.\n"
    new = _pack(tmp_path, "new.jsonl.gz", scripts, NEW_COMMIT)

    found = forge_import.compare(old, new)

    assert found.added == ["Test Druid"]
    assert found.removed == ["Not In The Catalogue"]
    assert found.changed == ["Llanowar Elves"]
    # Its rules text changed, not what it does; the new card has blocks.
    assert found.blocks_changed == ["Test Druid"]
    assert found.changes == 3


def test_a_script_that_reads_less_is_listed_for_review(tmp_path, old):
    new = _pack(tmp_path, "new.jsonl.gz",
                {**FORGE_SCRIPTS, "s/sol_ring.txt": SOL_RING_UNKNOWN}, NEW_COMMIT)

    found = forge_import.compare(old, new)

    assert found.blocks_changed == ["Sol Ring"]
    assert found.reads_less == ["Sol Ring"]
    assert found.reads_more == []
    back = forge_import.compare(new, old)
    assert back.reads_more == ["Sol Ring"]
    assert back.reads_less == []


def test_failures_are_counted_and_more_than_one_percent_is_too_many(tmp_path, old, monkeypatch):
    translate = forge_import.translate

    def breaks_on_sol_ring(script):
        if script.name == "Sol Ring":
            raise ValueError("a new parameter")
        return translate(script)

    monkeypatch.setattr(forge_import, "translate", breaks_on_sol_ring)
    new = _pack(tmp_path, "new.jsonl.gz", FORGE_SCRIPTS, NEW_COMMIT)

    found = forge_import.compare(old, new)

    assert found.failed == ["s/sol_ring.txt: ValueError: a new parameter"]
    assert found.too_many_failures
    monkeypatch.setattr(forge_import, "MAX_FAILURES", 0.5)
    assert not found.too_many_failures


def test_the_report_names_the_commits_and_cuts_long_lists(old):
    found = forge_import.Comparison(FORGE_COMMIT, NEW_COMMIT, scripts=40,
                                    changed=[f"Card {n:02}" for n in range(40)],
                                    reads_less=["Sol Ring"])

    text = forge_import.report(found)

    assert f"compare/{FORGE_COMMIT}...{NEW_COMMIT}" in text
    assert "| Changed | 40 |" in text
    assert "**Read less than before, check these (1):** `Sol Ring`" in text
    assert "`Card 29` and 10 more" in text
    assert "Card 30" not in text


def test_names_from_the_scripts_are_never_rendered_as_markdown():
    """A script is data: a name cannot link, mention or break out of its span."""
    found = forge_import.Comparison(FORGE_COMMIT, NEW_COMMIT, scripts=1,
                                    added=["@someone [x](https://example.com) `y`\nz"])

    text = forge_import.report(found)

    assert "`@someone [x](https://example.com) 'y' z`" in text


def test_the_command_prints_the_count_and_writes_the_description(tmp_path, old, capsys):
    new = _pack(tmp_path, "new.jsonl.gz",
                {**FORGE_SCRIPTS, "t/test_druid.txt": DRUID}, NEW_COMMIT)
    body = tmp_path / "body.md"

    call_command("forge_check", old=str(old), new=str(new), out=str(body))

    assert capsys.readouterr().out.strip() == "changes=1"
    assert "**New scripts (1):** `Test Druid`" in body.read_text(encoding="utf-8")


def test_the_command_fails_above_the_limit(tmp_path, old, monkeypatch):
    monkeypatch.setattr(forge_import, "translate", lambda script: 1 / 0)
    new = _pack(tmp_path, "new.jsonl.gz", FORGE_SCRIPTS, NEW_COMMIT)
    body = tmp_path / "body.md"

    with pytest.raises(CommandError, match="3 of 3 scripts failed"):
        call_command("forge_check", old=str(old), new=str(new), out=str(body))
    # The description is written all the same, for the failed run's log.
    assert "| Failed to translate | 3" in body.read_text(encoding="utf-8")
