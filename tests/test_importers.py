"""Reading any delimited export, and refusing the ones that are not.

These cover `decks/importers/tabular.py`, which replaced a plan to write seven
format-specific parsers. The plan's premise was that a format is identified by
its header row; the premise was false, because Archidekt and Delver Lens both
let the user choose which columns to export. So the unit under test here is
never a website - it is **a concept and the words tools use for it**.

Two properties carry most of the weight:

1. **The same row reads the same whatever its columns are called.** That is
   what makes an unsupported format a thirty-second answer rather than a phase.
2. **A file we cannot read is refused out loud, and a file we can read wrongly
   is refused louder.** Everything that could silently produce a plausible
   wrong deck gets its own test here, because that failure mode is the one this
   application exists to avoid.

No database. These are the parsers alone - resolution against the catalogue is
`test_decks_import.py`.
"""

import pytest

from decks.importers import columns, tabular
from decks.importers.base import (
    MalformedTable,
    MissingColumn,
    NotTabular,
    TooManyRows,
)
from decks.importers.tabular import TabularParser


def rows(text, overrides=None):
    return list(TabularParser(overrides).parse(text))


# --- finding the table ------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("comma", "Quantity,Name,Set code\n4,Sol Ring,cmd\n"),
        ("semicolon", "Quantity;Name;Set code\n4;Sol Ring;cmd\n"),
        ("tab", "Quantity\tName\tSet code\n4\tSol Ring\tcmd\n"),
    ],
)
def test_the_delimiter_is_chosen_by_what_it_yields(label, text):
    """Not by counting commas - by how many concepts the split produces.

    A semicolon file is not exotic: it is what Excel writes on a machine with a
    European locale, which is most of the ones this application is aimed at.
    """
    table = tabular.locate(text)
    assert table.delimiter_label == label
    assert rows(text)[0].quantity == 4


def test_a_tools_preamble_is_skipped_by_looking_for_the_real_header():
    """`preamble_skip` used to be a constant somebody had to know per format.

    Dragon Shield writes junk above its header. Rather than record that fact
    about Dragon Shield, the header is *located*: every candidate line is
    scored on how many concepts it names, and the winner is the header. That
    generalises to tools nobody has told us about.
    """
    text = (
        "Folder Name,dragonshield\n"
        "sep=,\n"
        "\n"
        "Quantity,Card Name,Set Code,Card Number,Printing\n"
        "7,Swamp,TOR,341,Normal\n"
    )
    table = tabular.locate(text)

    assert table.skip == 3
    assert (rows(text)[0].quantity, rows(text)[0].name) == (7, "Swamp")


def test_a_plain_text_list_is_not_mistaken_for_a_table():
    """`1 Chainer, Dementia Master` has a comma and is not a CSV.

    The consistency check is what keeps it out: that line splits into two
    fields and `1 Sol Ring` splits into one, so the pair is not a table. Read
    as one, every card name would arrive with half a row glued to it and every
    single row would then fail to resolve for a reason nobody could act on.
    """
    assert tabular.locate("1 Chainer, Dementia Master\n1 Sol Ring\n") is None

    with pytest.raises(NotTabular):
        rows("1 Sol Ring\n1 Dark Ritual\n")


def test_a_quoted_newline_survives_when_there_is_no_preamble():
    """The file goes to `csv` untouched unless junk has to be dropped first."""
    text = 'Quantity,Name,Tags\n1,Sol Ring,"ramp\nartifact"\n'
    assert [row.name for row in rows(text)] == ["Sol Ring"]


# --- one vocabulary, many spellings -----------------------------------------


@pytest.mark.parametrize(
    "header",
    [
        "Quantity,Name,Edition Code,Collector Number",    # Archidekt
        "Count,Name,Edition,Collector Number",            # Moxfield
        "Quantity,Name,Set code,Collector number",        # ManaBox
        "Quantity,Card Name,Set Code,Card Number",        # Dragon Shield
        "Count,Name,Edition Code,Card Number",            # Deckbox
        "QUANTITY,NAME,SETCODE,COLLECTOR NUMBER",         # Topdecked
        "quantity , name , set_code , collector_number",  # spacing and case
    ],
)
def test_the_same_row_reads_the_same_whatever_its_columns_are_called(header):
    """The property that made seven parsers unnecessary."""
    parsed = rows(f"{header}\n28,Swamp,tor,341\n")

    assert len(parsed) == 1
    assert parsed[0].quantity == 28
    assert parsed[0].name == "Swamp"
    assert parsed[0].set_code == "tor"
    assert parsed[0].collector_number == "341"


def test_columns_nobody_recognises_are_ignored_rather_than_refused():
    """A real export carries prices, tags and a date added. That is normal."""
    parsed = rows(
        "Quantity,Name,Purchase Price,Date Added,Multiverse Id\n"
        "2,Sol Ring,3.49,2026-06-21,29704\n"
    )
    assert (parsed[0].quantity, parsed[0].name) == (2, "Sol Ring")


def test_a_commander_is_read_from_a_tags_cell_and_never_inferred():
    parsed = rows(
        "Quantity,Name,Tags\n"
        "1,Chainer,\"Commander,Ramp\"\n"
        "1,Sol Ring,Ramp\n"
    )
    assert [row.is_commander for row in parsed] == [True, False]


# --- refusing rather than defaulting ----------------------------------------


def test_a_missing_quantity_column_is_refused_when_nobody_has_been_asked():
    """The 28-Swamp bug. Silent corruption became a loud refusal."""
    with pytest.raises(MissingColumn) as excinfo:
        rows("Name,Edition Code\nSwamp,tor\n")

    assert "quantity" in str(excinfo.value)
    assert "Quantity" in str(excinfo.value), "the message says how to fix it"


def test_a_confirmed_mapping_may_say_there_is_no_quantity_column():
    """The honesty rule, stated exactly.

    The application may not invent a quantity. A person is allowed to say there
    is not one - and the difference between those two sentences is the whole of
    `Mapping.confirmed`.
    """
    parsed = rows(
        "Name,Edition Code\nSwamp,tor\n",
        {"name": "Name", "quantity": columns.ABSENT, "set_code": "Edition Code"},
    )
    assert [(row.quantity, row.name) for row in parsed] == [(1, "Swamp")]


def test_a_missing_name_column_is_refused_even_with_a_confirmed_mapping():
    """Nothing can be read from a file with no card in it, whoever says so."""
    with pytest.raises(MissingColumn):
        rows("Quantity,Edition Code\n4,tor\n", {"quantity": "Quantity"})


def test_a_file_that_is_not_a_table_at_all_is_refused_by_name():
    with pytest.raises(NotTabular):
        rows("just one line of prose with no delimiter in it")


def test_csv_the_module_itself_rejects_becomes_a_form_error():
    """Not a 500 on an upload form a stranger can reach.

    A single field larger than the csv module's 128 KB limit is legal CSV and
    raises `_csv.Error`. `MAX_UPLOAD_BYTES` already bounds it at a megabyte, so
    this is about answering with a sentence rather than a stack trace.
    """
    huge = "Quantity,Name\n1,\"" + ("a" * 200_000) + "\n"
    with pytest.raises(MalformedTable):
        rows(huge)


def test_more_rows_than_any_real_export_has_are_refused():
    """A bound on memory, not on ambition.

    A megabyte of `1,a` is a quarter of a million rows and a quarter of a
    million dataclasses, which is most of a 128 MiB cloudlet.
    """
    text = "Quantity,Name\n" + ("1,Sol Ring\n" * (tabular.MAX_ROWS + 1))
    with pytest.raises(TooManyRows):
        rows(text)


def test_a_card_name_longer_than_any_card_is_truncated():
    """It is not a card name, and it is on its way to a 256-char column."""
    parsed = rows("Quantity,Name\n1," + ("x" * 5_000) + "\n")
    assert len(parsed[0].name) == 256


# --- the mapping, and what it will not accept -------------------------------


def test_only_a_missing_name_or_quantity_is_worth_interrupting_somebody_for():
    """Asking about `language` would teach people to click through the screen."""
    _, mapping = TabularParser().read("Quantity,Name\n1,Sol Ring\n")

    assert not mapping.needs_confirmation
    assert not mapping.has(columns.LANGUAGE), "still unmatched, still not worth asking"


def test_a_mapping_cannot_name_a_column_the_file_has_not_got():
    """Overrides arrive straight from a POST, so nothing in them is trusted.

    The worst a hostile mapping can do is map nothing, which is the same
    outcome as an empty file - and the same outcome as the honest refusal a
    person would get for leaving the dropdowns alone.
    """
    headers = ["Quantity", "Name"]
    mapping = columns.map_headers(
        headers,
        {
            "name": "Name",
            "quantity": "../../etc/passwd",   # not a header in this file
            "not_a_concept": "Name",          # not a concept at all
        },
    )

    assert mapping.found["quantity"] == "Quantity", "the invented header was not adopted"
    assert mapping.found["name"] == "Name"
    assert "not_a_concept" not in mapping.found
    assert set(mapping.found) <= columns.KEYS


def test_a_mapping_survives_a_round_trip_through_different_spelling():
    """A form posts back what HTML gave it, which is not always byte-identical."""
    mapping = columns.map_headers(["Set code"], {"set_code": "SET CODE"})
    assert mapping.found["set_code"] == "Set code", "matched to the file's own spelling"


def test_the_sample_shown_on_the_mapping_screen_lines_up_with_its_headers():
    """A short row must not shift every cell one column to the left."""
    text = "Quantity,Name,Set code\n4,Sol Ring\n1,Swamp,tor\n"
    table = tabular.locate(text)

    assert tabular.sample(text, table) == [
        ["4", "Sol Ring", ""],
        ["1", "Swamp", "tor"],
    ]


# --- deciding whether we were asked to read it ------------------------------


def test_a_table_we_recognise_nothing_in_scores_below_the_threshold():
    """Knowing how to read a file is not knowing we were asked to.

    Such a file still imports - the person picks CSV and maps the columns - but
    the sniffer does not decide that on their behalf.
    """
    opaque = "col_a,col_b\n1,Sol Ring\n"
    assert TabularParser.sniff("col_a,col_b", opaque) < 0.5

    named = "Quantity,Name\n1,Sol Ring\n"
    assert TabularParser.sniff("Quantity,Name", named) >= 0.5


def test_a_format_specific_signature_outranks_the_generic_reader():
    """So an Archidekt file is still recorded as Archidekt and not as `csv`."""
    from decks.importers import sniff
    from decks.importers.archidekt import ArchidektParser

    archidekt = (
        "Quantity,Name,Finish,Date Added,Edition Code,Scryfall ID,Collector Number\n"
        "1,Sol Ring,Normal,2026-06-21,cmd,abc,263\n"
    )
    assert sniff(archidekt) is ArchidektParser
