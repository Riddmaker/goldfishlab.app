"""P20 F1: every shape a real export comes in, read without a hand on it.

The product rule (2026-10-12): an import works **with foil and printing
information in whatever form it comes**. Lauf 1 pasted the real exports of
Moxfield, Archidekt, ManaBox, TappedOut and Arena, and each failed in its own
way. The lines below are built on the same patterns as those files - the
files themselves are a person's decks and stay out of the repository.

Each case checks the parser the registry picks, how many cards land in which
board, which card the file marks as commander, and what it said about one
card's printing.
"""

import pytest

from decks import importers, services
from decks.importers import boards
from decks.importers.text import PlainTextParser, heading

MOXFIELD_POPUP = """\
1 Krark, the Thumbless (CMR) 189
1 Thrasios, Triton Hero (SPG) 16
1 Archmage Emeritus (STX) 377 *F*
1 Harrow (PLST) C18-150
1 Sol Ring (SLD) ★12 *E*
"""

MOXFIELD_MTGO = """\
1 Ancient Tomb
1 Arcane Signet

1 Krark, the Thumbless
1 Thrasios, Triton Hero
"""

MOXFIELD_ARENA = """\
About
Name The Real Friends [TOODEEP]

Commander
1 Thrasios, Triton Hero

Deck
1 Ancient Tomb
1 Arcane Signet
"""

ARCHIDEKT_TEXT = """\
1x Prosper, Tome-Bound (afc) 2 [Commander{top}]
1x Sol Ring (frc) 21 [Artifact]
1x Armed // Dangerous (dgm) 122 [Instant,Removal] ^Have,#37d67a^
1x Lightning Bolt (2x2) 117 [Maybeboard{noDeck}{noPrice},Instant]
1x Birgi, God of Storytelling / Harnfel, Horn of Bounty (khm) 123
"""

ARCHIDEKT_CSV_HEADLESS = """\
1,Armed // Dangerous
1,"Xyris, the Writhing Storm"
10,Forest
"""

#: P20: Archidekt's deck CSV with edition and Scryfall id ticked - still no
#: header, and an ownership flag at the end (`echt/mono_black commander.csv`).
ARCHIDEKT_CSV_PRINTINGS = """\
1,Animate Dead,5ed,1f2cd314-8f99-4443-ae86-a967effc7490,own exact edition
1,Arcane Signet,40k,44ff370e-b72d-4a6c-b6dd-8434820559cc,own exact edition
1,Swamp,ppp1,95e936cf-3bbb-4f3b-8e1a-4be1d4702b99,own different edition
"""

ARCHIDEKT_CSV_MAYBE = """\
Quantity,Name,Finish,Tags,Edition Code,Collector Number
1,"Prosper, Tome-Bound",Foil,Commander,afc,2
1,Sol Ring,Normal,,frc,21
1,Lightning Bolt,Normal,Maybeboard,2x2,117
"""

MANABOX_EXPORT = """\
// COMMANDER
1 Ghoulcaller Gisa (FDC) 6 *F*

1 Arcane Signet (FDC) 245
1 Army of the Damned (FDC) 85
"""

MANABOX_SHARED_FR = """\
[COMMANDER]
1 Ghoulcaller Gisa

[CRÉATURES]
1 Carrion Feeder

[TERRAINS]
33 Swamp
"""

TAPPEDOUT = """\
1x Prosper, Tome-Bound *CMDR*
1x Sol Ring
"""

LIGAMAGIC = """\
Comandante:
1 Meren of Clan Nel Toth

Deck:
1 Sol Ring
"""

EXCEL_CH = """\
Anzahl;Kartenname;Edition;Bemerkung
1;Prosper, Tome-Bound;AFC;Commander
2;Sol Ring;FRC;
"""

ARENA_SIDEBOARD = """\
Deck
4 Abrade (SOC) 234
4 Impact Tremors (FDN) 717

Sideboard
1 Kolaghan's Command (2X2) 239
2 Bitter Triumph (TDC) 173
"""

HAND_TYPED = """\
prosper, tome-bound
sol ring
"""


@pytest.mark.parametrize(
    ("text", "parser", "in_boards", "commanders", "probe", "printing"),
    [
        pytest.param(MOXFIELD_POPUP, "text", {"main": 5}, [],
                     "Archmage Emeritus", ("stx", "377", "foil"), id="moxfield-popup"),
        pytest.param(MOXFIELD_MTGO, "text", {"main": 4}, [],
                     "Ancient Tomb", ("", "", ""), id="moxfield-mtgo"),
        pytest.param(MOXFIELD_ARENA, "text", {"commander": 1, "main": 2},
                     ["Thrasios, Triton Hero"], "Ancient Tomb", ("", "", ""),
                     id="moxfield-arena-about"),
        pytest.param(ARCHIDEKT_TEXT, "text", {"commander": 1, "main": 3, "maybe": 1},
                     ["Prosper, Tome-Bound"], "Armed // Dangerous", ("dgm", "122", ""),
                     id="archidekt-text"),
        pytest.param(ARCHIDEKT_CSV_HEADLESS, "csv", {"main": 12}, [],
                     "Xyris, the Writhing Storm", ("", "", ""), id="archidekt-csv-headless"),
        pytest.param(ARCHIDEKT_CSV_MAYBE, "csv", {"commander": 1, "main": 1, "maybe": 1},
                     ["Prosper, Tome-Bound"], "Prosper, Tome-Bound", ("afc", "2", "foil"),
                     id="archidekt-csv-maybeboard-tag"),
        pytest.param(MANABOX_EXPORT, "text", {"commander": 1, "main": 2},
                     ["Ghoulcaller Gisa"], "Ghoulcaller Gisa", ("fdc", "6", "foil"),
                     id="manabox-export"),
        pytest.param(MANABOX_SHARED_FR, "text", {"commander": 1, "main": 34},
                     ["Ghoulcaller Gisa"], "Swamp", ("", "", ""), id="manabox-shared-fr"),
        pytest.param(TAPPEDOUT, "text", {"commander": 1, "main": 1},
                     ["Prosper, Tome-Bound"], "Prosper, Tome-Bound", ("", "", ""),
                     id="tappedout-cmdr"),
        pytest.param(LIGAMAGIC, "text", {"commander": 1, "main": 1},
                     ["Meren of Clan Nel Toth"], "Sol Ring", ("", "", ""), id="ligamagic"),
        pytest.param(EXCEL_CH, "csv", {"commander": 1, "main": 2},
                     ["Prosper, Tome-Bound"], "Sol Ring", ("frc", "", ""), id="excel-ch"),
        pytest.param(ARENA_SIDEBOARD, "text", {"main": 8, "side": 3}, [],
                     "Abrade", ("soc", "234", ""), id="arena-sideboard"),
        pytest.param(HAND_TYPED, "text", {"main": 2}, [],
                     "prosper, tome-bound", ("", "", ""), id="hand-typed"),
    ],
)
def test_every_export_shape_is_read_as_it_comes(text, parser, in_boards, commanders,
                                                 probe, printing):
    reader, rows = services.read(text)

    assert reader.name == parser
    counted = {}
    for row in rows:
        counted[row.board] = counted.get(row.board, 0) + row.quantity
    assert counted == in_boards
    assert [row.name for row in rows if row.is_commander] == commanders
    row = next(row for row in rows if row.name == probe)
    assert (row.set_code, row.collector_number, row.finish) == printing


def test_the_arena_about_block_names_the_deck():
    reader, _ = services.read(MOXFIELD_ARENA)
    assert reader.deck_name == "The Real Friends [TOODEEP]"


@pytest.mark.parametrize(
    ("line", "name", "set_code", "number", "finish", "board"),
    [
        ("1 Sol Ring (CMD) 1", "Sol Ring", "cmd", "1", "", "main"),
        ("1 Sol Ring (C21)", "Sol Ring", "c21", "", "", "main"),
        ("1 Sol Ring (PLST) ABC-123", "Sol Ring", "plst", "ABC-123", "", "main"),
        ("1 Sol Ring (30A) 12", "Sol Ring", "30a", "12", "", "main"),
        ("1 Sol Ring (pAFR) 2024-15", "Sol Ring", "pafr", "2024-15", "", "main"),
        ("1 Sol Ring (SLD) 123a *F*", "Sol Ring", "sld", "123a", "foil", "main"),
        ("1 Sol Ring [C21]", "Sol Ring", "c21", "", "", "main"),
        ("1 Sol Ring <foil>", "Sol Ring", "", "", "foil", "main"),
        ("1 Sol Ring (Foil)", "Sol Ring", "", "", "foil", "main"),
        ("1 Sol Ring #ramp #!rocks", "Sol Ring", "", "", "", "main"),
        ("1 Krenko, Mob Boss # !Commander", "Krenko, Mob Boss", "", "", "", "commander"),
        ("1 Fire // Ice (MH2) 290", "Fire // Ice", "mh2", "290", "", "main"),
        # A name that ends in a bracket of its own keeps it: title case is
        # never a set code. Checked against the catalogue (P20 F1).
        ("1 Hazmat Suit (Used)", "Hazmat Suit (Used)", "", "", "", "main"),
        ("1 Unearth (Theme)", "Unearth (Theme)", "", "", "", "main"),
        ("1 Sol Ring [Land]", "Sol Ring", "", "", "", "main"),
        ("1 Sol Ring [Sideboard{noDeck}]", "Sol Ring", "", "", "", "side"),
    ],
)
def test_the_tail_of_a_line_is_taken_off_piece_by_piece(line, name, set_code, number,
                                                        finish, board):
    row = next(iter(PlainTextParser().parse(line)))
    assert (row.name, row.set_code, row.collector_number, row.finish, row.board) == (
        name, set_code, number, finish, board)


@pytest.mark.parametrize(
    ("line", "board"),
    [
        ("Commander", boards.COMMANDER), ("Kommandeur", boards.COMMANDER),
        ("Commandant", boards.COMMANDER), ("Comandante:", boards.COMMANDER),
        ("統率者", boards.COMMANDER), ("[COMMANDER]", boards.COMMANDER),
        ("Sideboard (15)", boards.SIDE), ("Banquillo", boards.SIDE), ("Réserve", boards.SIDE),
        ("Maybeboard", boards.MAYBE), ("Considering", boards.MAYBE),
        ("Companion", boards.COMPANION), ("Tokens", boards.TOKENS),
        ("Deck", boards.MAIN), ("[CRÉATURES]", boards.MAIN), ("Creatures:", boards.MAIN),
        ("about", "about"),
        ("1 Sol Ring", None), ("Sol Ring", None), ("1 Commander's Sphere", None),
    ],
)
def test_headings_in_every_language_open_their_board(line, board):
    assert heading(line) == board


def test_a_commander_block_ends_at_the_blank_line_after_it():
    """ManaBox: `// COMMANDER`, one card, a blank line, then the deck with no
    heading of its own. Before F1 the whole deck was the commander."""
    rows = list(PlainTextParser().parse(MANABOX_EXPORT))
    assert [row.board for row in rows] == ["commander", "main", "main"]


def test_a_comment_resets_to_the_main_deck_unless_it_names_a_board():
    text = ("// Commander\n1 Prosper, Tome-Bound\n// Creatures\n1 Grim Hireling\n"
            "// Sideboard\n1 Abrade\n")
    rows = list(PlainTextParser().parse(text))
    assert [row.board for row in rows] == ["commander", "main", "side"]


# --- sniffing ---------------------------------------------------------------


def test_a_comma_in_a_card_name_no_longer_refuses_a_text_list():
    """E1.15: `1 Krark, the Thumbless` on line 1 sent Moxfield's own export to
    the format picker."""
    assert importers.sniff(MOXFIELD_POPUP) is PlainTextParser


def test_a_csv_with_an_unknown_header_still_reaches_the_format_picker():
    assert importers.sniff("col_a,col_b,col_c\nq,Sol Ring,cmd\n") is None


def test_a_semicolon_file_is_a_table_not_a_text_list():
    assert PlainTextParser.sniff("", EXCEL_CH) == 0.0
    assert importers.sniff(EXCEL_CH) is importers.TabularParser


def test_a_headerless_table_is_read_only_from_the_first_line():
    """Below a real header every data line has the headerless shape; reading
    one of them as "no header" would lose the header's column names."""
    from decks.importers import tabular

    table = tabular.locate(ARCHIDEKT_CSV_HEADLESS)
    assert table.headerless and table.headers[:2] == ["Quantity", "Name"]

    headed = tabular.locate("Id,Name\n12345,Sol Ring\n2,Swamp\n")
    assert not headed.headerless and headed.headers == ["Id", "Name"]


def test_a_headerless_table_keeps_its_printing():
    """Edition and Scryfall id are read by their shape, so a file with no
    header still says which printing it means (P20, the mono-black export)."""
    _, rows = services.read(ARCHIDEKT_CSV_PRINTINGS)

    assert [(row.set_code, row.scryfall_id) for row in rows] == [
        ("5ed", "1f2cd314-8f99-4443-ae86-a967effc7490"),
        ("40k", "44ff370e-b72d-4a6c-b6dd-8434820559cc"),
        ("ppp1", "95e936cf-3bbb-4f3b-8e1a-4be1d4702b99"),
    ]


@pytest.mark.parametrize(
    ("text", "headers"),
    [
        pytest.param("1,Sol Ring,c21,263\n2,Swamp,LEA,295\n",
                     ["Quantity", "Name", "Set Code", "Collector Number"], id="set-and-number"),
        pytest.param("1,Sol Ring,Ramp\n2,Swamp,Land\n",
                     ["Quantity", "Name", "Column 3"], id="a-category-is-not-a-set"),
        pytest.param("1,Sol Ring,263\n2,Swamp,295\n",
                     ["Quantity", "Name", "Column 3"], id="a-lone-number-is-not-read"),
        pytest.param("1,Sol Ring,own exact edition\n",
                     ["Quantity", "Name", "Column 3"], id="a-note-is-not-read"),
    ],
)
def test_a_headerless_column_is_named_only_by_an_unmistakable_shape(text, headers):
    from decks.importers import tabular

    assert tabular.locate(text).headers == headers


# --- links ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "names"),
    [
        ("https://moxfield.com/decks/q7Hs2kL9xEaBc3mW1nYzRt\n", "Moxfield"),
        ("https://archidekt.com/decks/123456/elves\n", "Archidekt"),
        ("www.manabox.app/decks/abc\n", "ManaBox"),
        ("https://tappedout.net/mtg-decks/krenko/\n", "Export or Download"),
    ],
)
def test_a_link_is_refused_with_where_that_site_keeps_its_export(text, names):
    with pytest.raises(services.LinkNotList) as refusal:
        services.read(text)
    assert "cannot open links" in str(refusal.value)
    assert names in str(refusal.value)


def test_a_link_among_card_lines_is_just_a_line():
    _, rows = services.read("https://moxfield.com/decks/x\n1 Sol Ring\n")
    assert [row.name for row in rows][-1] == "Sol Ring"
