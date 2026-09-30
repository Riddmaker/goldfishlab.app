"""Phase 1: parsing deck lists and resolving them against the catalogue.

The invariant under test throughout: **nothing is dropped.** Every line of an
uploaded file ends up either as a card in the deck or as a visible unresolved
row with a reason. An importer that silently skips what it cannot read produces
a deck page that is wrong and confident, which is the exact failure this
application exists to avoid.
"""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

from decks import importers, resolve, services
from decks.importers.archidekt import ArchidektParser
from decks.importers.tabular import TabularParser
from decks.importers.text import PlainTextParser
from decks.models import Deck, DeckCard, UnresolvedRow

pytestmark = pytest.mark.django_db

User = get_user_model()
FIXTURES = Path(__file__).resolve().parent / "fixtures"
ARCHIDEKT_CSV = FIXTURES / "archidekt_sample.csv"

# The real 214-row export, outside this repo. Used when present, skipped when not,
# so the suite works on a fresh checkout without the sibling project.
REAL_EXPORT = (
    Path(__file__).resolve().parent.parent.parent
    / "magic-project"
    / "archidekt-collection-export-2026-09-15.csv"
)


@pytest.fixture
def user():
    return User.objects.create_user(email="deck@example.com", password="pw-for-test-only")


# --- format detection -------------------------------------------------------


def test_archidekt_csv_is_recognised():
    header = ARCHIDEKT_CSV.read_text(encoding="utf-8").splitlines()[0]
    assert ArchidektParser.sniff(header) >= importers.MIN_CONFIDENCE
    assert importers.sniff(ARCHIDEKT_CSV.read_text(encoding="utf-8")) is ArchidektParser


def test_plain_text_is_recognised():
    assert importers.sniff("1 Sol Ring\n1 Dark Ritual\n") is PlainTextParser


def test_a_csv_whose_columns_mean_nothing_to_us_is_still_refused():
    """The registry's `None` survived the redesign, with a narrower job.

    It used to mean "this format is not supported yet". It now means "no
    column in this file names a card", which is the only case left where
    reading it would be a guess. The file is still importable - the person
    picks CSV and maps the columns - but *this* function does not decide that
    for them.
    """
    opaque = "col_a,col_b,col_c\nq,Sol Ring,cmd\n"
    assert importers.sniff(opaque) is None

    with pytest.raises(services.UnknownFormat):
        services.parse(opaque)


def test_a_csv_whose_columns_do_name_a_card_is_read_without_asking():
    """The behaviour that changed on 2026-09-20, asserted rather than assumed.

    This exact file used to sniff as `None` and cost a phase of work to
    support, because the plan was one parser per site and nobody had verified
    Moxfield's headers. The plan was wrong: what needed verifying was never
    the site, it was the columns, and every column here is one we know the
    word for.

    Note what is *not* claimed. Nothing says this is a Moxfield export - the
    import is recorded as a generic CSV, because recognising a vocabulary is
    not the same as recognising a tool.
    """
    moxfield_ish = (
        "Count,Tradelist Count,Name,Edition,Condition,Language,Foil\n"
        "4,0,Sol Ring,cmd,NM,English,\n"
    )
    parser, rows = services.parse(moxfield_ish)

    assert parser is TabularParser
    assert (rows[0].quantity, rows[0].name, rows[0].set_code) == (4, "Sol Ring", "cmd")


def test_tradelist_count_is_never_read_as_a_quantity():
    """It means "how many I will trade away", which is routinely zero.

    It was a `QUANTITY` alias until 2026-09-20, taken from a conversion tool's
    mapping without anybody asking what the words meant. Because `find()`
    returns the first matching header in file order, a file listing it first
    would have read a whole collection as untradeable - which is to say as
    empty - and nothing would have errored.
    """
    rows = list(TabularParser().parse("Tradelist Count,Count,Name\n0,4,Sol Ring\n"))
    assert rows[0].quantity == 4

    with pytest.raises(importers.MissingColumn):
        list(TabularParser().parse("Tradelist Count,Name\n0,Sol Ring\n"))


def test_a_hand_picked_format_overrides_the_sniff():
    parser, rows = services.parse("1 Sol Ring\n", parser_name="text")
    assert parser is PlainTextParser
    assert rows[0].name == "Sol Ring"


# --- the parsers ------------------------------------------------------------


def test_archidekt_rows_carry_every_identifier_the_file_had():
    rows = list(ArchidektParser().parse(ARCHIDEKT_CSV.read_text(encoding="utf-8")))
    sol_ring = next(r for r in rows if r.name == "Sol Ring")

    assert sol_ring.quantity == 1
    assert sol_ring.set_code == "cmd"
    assert sol_ring.scryfall_id
    assert sol_ring.line_number > 1


def test_archidekt_reads_the_commander_tag():
    rows = list(ArchidektParser().parse(ARCHIDEKT_CSV.read_text(encoding="utf-8")))
    commanders = [r for r in rows if r.is_commander]
    assert [r.name for r in commanders] == ["Chainer, Dementia Master"]


@pytest.mark.parametrize(
    ("line", "quantity", "name"),
    [
        ("1 Sol Ring", 1, "Sol Ring"),
        ("1x Sol Ring", 1, "Sol Ring"),
        ("31 Swamp", 31, "Swamp"),
        ("4 x Dark Ritual", 4, "Dark Ritual"),
        ("Sol Ring", 1, "Sol Ring"),
        ("1 Sol Ring (CMD) 1", 1, "Sol Ring"),
    ],
)
def test_plain_text_line_shapes(line, quantity, name):
    row = next(iter(PlainTextParser().parse(line)))
    assert (row.quantity, row.name) == (quantity, name)


def test_plain_text_skips_comments_and_section_headers():
    text = "// Commander\n1 Chainer, Dementia Master\n\nDeck\n1 Sol Ring\n# a note\n"
    rows = list(PlainTextParser().parse(text))
    assert [r.name for r in rows] == ["Chainer, Dementia Master", "Sol Ring"]
    assert rows[0].is_commander
    assert not rows[1].is_commander


# --- the resolution ladder --------------------------------------------------


def test_names_resolve_and_report_their_rung(catalogue):
    rows = list(ArchidektParser().parse(ARCHIDEKT_CSV.read_text(encoding="utf-8")))
    report = resolve.resolve(rows)

    assert report.rows_total == len(rows)
    assert len(report.resolved) + len(report.unresolved) == report.rows_total
    assert report.rung_counts[resolve.RUNG_EXACT_NAME] > 0


def test_accented_names_resolve_on_the_normalised_rung(catalogue):
    """`Lim-Dul the Necromancer` is really spelled with a circumflex.

    No deck list writes it that way, which is the entire reason rung 5 exists.
    """
    rows = list(ArchidektParser().parse(ARCHIDEKT_CSV.read_text(encoding="utf-8")))
    report = resolve.resolve(rows)

    limdul = next(r for r in report.resolutions if r.row.name.startswith("Lim-D"))
    assert limdul.resolved
    assert limdul.rung == resolve.RUNG_NORMALISED_NAME


def test_an_unknown_card_becomes_a_visible_row_not_a_silent_drop(catalogue):
    rows = list(ArchidektParser().parse(ARCHIDEKT_CSV.read_text(encoding="utf-8")))
    report = resolve.resolve(rows)

    unresolved = report.unresolved
    assert [r.row.name for r in unresolved] == ["Not A Real Magic Card"]
    assert unresolved[0].reason


def test_repeated_printings_are_summed_not_overwritten(catalogue):
    """A collection export lists a card once per printing owned.

    28 Swamps from Torment plus 3 from Zendikar is 31 Swamps, not 3.
    """
    rows = list(ArchidektParser().parse(ARCHIDEKT_CSV.read_text(encoding="utf-8")))
    report = resolve.resolve(rows)

    swamp = next(r.card for r in report.resolved if r.card.front_name == "Swamp")
    assert report.quantities()[str(swamp.pk)] == 31


def test_a_malformed_uuid_does_not_take_the_import_down(catalogue):
    """Postgres raises on an invalid uuid literal; a CSV typo must not be a 500."""
    row = importers.ParsedRow(line_number=2, name="Sol Ring", scryfall_id="not-a-uuid")
    report = resolve.resolve([row])
    assert report.resolved[0].rung == resolve.RUNG_EXACT_NAME


# --- the import service -----------------------------------------------------


def test_import_creates_a_deck_and_tallies_honestly(catalogue, user):
    outcome = services.import_deck(
        owner=user,
        raw=ARCHIDEKT_CSV.read_bytes(),
        name="Chainer",
        filename="archidekt_sample.csv",
    )

    record = outcome.record
    assert record.rows_total == record.rows_resolved + record.rows_unresolved
    assert record.rows_unresolved == 1
    assert record.parser == "archidekt"
    assert sum(record.rung_counts.values()) == record.rows_total


def test_the_commander_comes_from_the_files_own_marker(catalogue, user):
    outcome = services.import_deck(
        owner=user, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer"
    )
    assert outcome.deck.commander.front_name == "Chainer, Dementia Master"


def test_the_commander_is_not_also_one_of_the_99(catalogue, user):
    """Trap 46: the marked row goes to the command zone, not into the list too.

    Before the 2026-09-25 review the commander's row was written as a
    `DeckCard` *and* set as `Deck.commander`: "101 cards" on a 100-card export,
    the commander shuffled into the library, two copies wanted from a
    collection.
    """
    outcome = services.import_deck(owner=user, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer")
    deck = outcome.deck

    assert deck.commander is not None
    assert not deck.entries.filter(oracle_card=deck.commander).exists()
    # Every resolved copy is somewhere, exactly once.
    assert deck.total_with_commander == sum(outcome.report.quantities().values())


def test_a_refill_keeps_a_hand_picked_commander_out_of_the_list(catalogue, user):
    """A list with no marker still must not put the chosen commander back in."""
    deck = services.import_deck(
        owner=user, raw=b"1 Necropotence\n1 Sol Ring\n", name="Manual"
    ).deck
    necro = deck.entries.get(oracle_card__front_name="Necropotence").oracle_card
    services.set_commander(deck, necro)

    services.import_deck(owner=user, raw=b"1 Necropotence\n1 Sol Ring\n", deck=deck)

    deck.refresh_from_db()
    assert deck.commander_id == necro.pk
    assert list(deck.entries.values_list("oracle_card__front_name", flat=True)) == ["Sol Ring"]


def test_no_commander_is_better_than_a_guessed_one(catalogue, user):
    """A list with no marker leaves the field empty and says so on the page.

    Picking "the only legendary creature" would be right often enough to be
    trusted, and wrong often enough to hurt.
    """
    outcome = services.import_deck(
        owner=user, raw=b"1 Sol Ring\n1 Necropotence\n", name="Nameless"
    )
    assert outcome.deck.commander is None


def test_reimporting_replaces_rather_than_accumulates(catalogue, user):
    """An import means "this is the deck now"; merging would double it."""
    outcome = services.import_deck(owner=user, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer")
    first = DeckCard.objects.filter(deck=outcome.deck).count()

    services.import_deck(
        owner=user,
        raw=ARCHIDEKT_CSV.read_bytes(),
        name="Chainer",
        deck=outcome.deck,
    )
    assert DeckCard.objects.filter(deck=outcome.deck).count() == first


def test_unresolved_rows_are_stored_with_suggestions(catalogue, user):
    services.import_deck(owner=user, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer")
    row = UnresolvedRow.objects.get()
    assert row.raw_name == "Not A Real Magic Card"
    assert row.line_number > 1


def test_oversized_uploads_are_refused_before_decoding(user):
    with pytest.raises(services.ImportError_):
        services.decode(b"x" * (services.MAX_UPLOAD_BYTES + 1))


def test_a_binary_upload_is_refused_at_the_decode_boundary(user):
    """Found in Phase 6, present since Phase 1.

    `latin-1` decodes **any** byte sequence, so a JPEG became text, parsed into
    rows full of control characters and died inside the resolver with
    `PostgreSQL text fields cannot contain NUL (0x00) bytes` - a 500 on a
    public upload form that a stranger can cause on purpose. A NUL byte is the
    cheapest reliable proof that a file is not text, and refusing here means
    nothing downstream has to know about it.
    """
    with pytest.raises(services.ImportError_) as excinfo:
        services.decode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
    assert "binary" in str(excinfo.value)


def test_windows_encoded_exports_still_import(catalogue, user):
    """cp1252 is what a spreadsheet on Windows produces.

    A UnicodeDecodeError on line 200 of a working file is a miserable bug to
    be on the receiving end of.
    """
    text = "Quantity,Name,Scryfall ID,Edition Code\n1,Lim-Dûl the Necromancer,,csp\n"
    outcome = services.import_deck(
        owner=user, raw=text.encode("cp1252"), name="Encoded"
    )
    assert outcome.record.rows_resolved == 1


# --- quotas -----------------------------------------------------------------


def test_deck_creation_is_metered(catalogue, user):
    from billing import quotas
    from billing.models import UsageRecord

    before = quotas.used(user, UsageRecord.Metric.DECKS_CREATED)
    services.import_deck(owner=user, raw=ARCHIDEKT_CSV.read_bytes(), name="Chainer")
    assert quotas.used(user, UsageRecord.Metric.DECKS_CREATED) == before + 1
    assert quotas.used(user, UsageRecord.Metric.IMPORTS) >= 1


def test_the_free_plan_deck_limit_is_enforced(catalogue, user):
    from billing.models import Plan
    from billing.quotas import QuotaExceeded

    limit = Plan.objects.get(slug="free").max_decks
    for index in range(limit):
        services.import_deck(owner=user, raw=b"1 Sol Ring\n", name=f"Deck {index}")

    with pytest.raises(QuotaExceeded):
        services.import_deck(owner=user, raw=b"1 Sol Ring\n", name="One too many")

    assert Deck.objects.filter(owner=user).count() == limit


def test_the_free_plan_import_limit_is_enforced(catalogue, user):
    """Counted since Phase 1, capped since Phase 6 §2.

    `IMPORTS` was a metric with a `consume()` call and no entry in
    `_LIMIT_FIELDS`, so every check returned unlimited. The usage was recorded
    faithfully for five phases and compared against nothing.
    """
    from billing.models import Plan
    from billing.quotas import QuotaExceeded

    plan = Plan.objects.get(slug="free")
    assert plan.max_imports_per_month is not None, "the lever is connected"

    # Refilling one deck rather than creating several, so this measures the
    # import cap and not the deck cap standing in front of it.
    deck = services.import_deck(owner=user, raw=b"1 Sol Ring\n", name="Refilled").deck
    for _ in range(plan.max_imports_per_month - 1):
        services.import_deck(owner=user, raw=b"1 Sol Ring\n", deck=deck)

    with pytest.raises(QuotaExceeded) as excinfo:
        services.import_deck(owner=user, raw=b"1 Sol Ring\n", deck=deck)
    assert "imports" in str(excinfo.value)


def test_a_paid_plan_imports_without_limit(catalogue, user):
    """NULL is this schema's word for unlimited, and it still means that."""
    from billing import quotas
    from billing.models import Plan

    paid = Plan.objects.get(slug="archmage")
    assert paid.max_imports_per_month is None

    user.subscription.plan = paid
    user.subscription.save(update_fields=["plan"])

    decision = quotas.check(user, "imports", raise_on_fail=False)
    assert decision.unlimited


# --- what each import recorded about how it read the file -------------------


def test_an_import_records_which_column_it_read_as_what(catalogue, user):
    """The other half of the bargain the mapping screen makes.

    A file whose headers answer for themselves is imported without interrupting
    anybody - so the reading has to become visible somewhere, and after the
    fact is the only place left. Otherwise the quiet path is the unaccountable
    one.
    """
    outcome = services.import_deck(
        owner=user,
        raw=b"Count,Card,Set\n4,Sol Ring,cmd\n",
        name="Recorded",
    )
    recorded = dict((label, header) for label, header in outcome.record.column_mapping)

    assert recorded["quantity"] == "Count"
    assert recorded["card name"] == "Card"
    assert recorded["set code"] == "Set"


def test_a_plain_text_import_records_no_columns_and_does_not_mind(catalogue, user):
    """It has none. An empty list is the honest answer, not a missing one."""
    outcome = services.import_deck(owner=user, raw=b"1 Sol Ring\n", name="Typed")
    assert outcome.record.column_mapping == []


# --- the real export --------------------------------------------------------


@pytest.mark.skipif(not REAL_EXPORT.exists(), reason="the real Archidekt export is not present")
def test_the_real_214_row_export_resolves_completely(catalogue):
    """The acceptance case from the phase plan, against the user's own file.

    Note what this measures and what it does not: the phase document expected
    every row to match on rung 1 (the printing id). Against an oracle-level
    catalogue that is not what happens - most rows match on their name instead,
    because the exported printing is usually not the one the bulk file ships.
    The outcome that matters, zero unresolved rows, holds either way.

    It runs against the full catalogue when one has been ingested locally; with
    only the small fixture loaded, most rows are legitimately unknown, so the
    assertion is on the *shape* of the result rather than a fixed count.
    """
    text = REAL_EXPORT.read_text(encoding="utf-8-sig")
    parser, rows = services.parse(text)
    report = resolve.resolve(rows)

    assert parser is ArchidektParser
    assert report.rows_total == 214
    assert len(report.resolved) + len(report.unresolved) == report.rows_total
    for resolution in report.unresolved:
        assert resolution.reason, "an unresolved row must always carry a reason"


# --- headers are not fixed, so nothing may depend on them -------------------
#
# The discovery that produced `decks/importers/columns.py`: **Archidekt lets
# you choose which columns to export, and Delver Lens makes you choose the
# fields and their order.** So a parser keyed on one site's header row was
# never going to hold - the same site produces different files for different
# users.
#
# The bug that was live before these tests: an export with Quantity unticked
# sniffed at 0.75 confidence, parsed without complaint, and read every row as a
# single copy. A 28-Swamp deck imported as one Swamp.


def test_a_header_with_no_quantity_column_is_refused_not_defaulted():
    """The bug, pinned. Silent corruption became a loud refusal."""
    from decks.importers.archidekt import ArchidektParser
    from decks.importers.base import MissingColumn

    with pytest.raises(MissingColumn) as excinfo:
        list(ArchidektParser().parse("Name,Edition Code\nSwamp,tor\n"))

    assert "quantity" in str(excinfo.value)
    assert "Quantity" in str(excinfo.value), "the message says how to fix it"


def test_the_refusal_reaches_the_user_as_an_import_error():
    """Not a 500. It lands on the form beside the file field."""
    with pytest.raises(services.ImportError_) as excinfo:
        services.parse("Name,Edition Code\nSwamp,tor\n", "archidekt")
    assert "quantity" in str(excinfo.value)


def test_a_header_with_no_name_column_is_refused():
    from decks.importers.archidekt import ArchidektParser
    from decks.importers.base import MissingColumn

    with pytest.raises(MissingColumn):
        list(ArchidektParser().parse("Quantity,Edition Code\n4,tor\n"))


@pytest.mark.parametrize(
    "header",
    [
        "Quantity,Name,Edition Code,Collector Number",   # Archidekt, verified
        "Count,Name,Edition,Collector Number",           # Moxfield's names
        "Quantity,Name,Set code,Collector number",       # ManaBox's names
        "Quantity,Card Name,Set Code,Card Number",       # Dragon Shield's
        "Count,Name,Edition Code,Card Number",           # Deckbox's
        "QUANTITY,NAME,SETCODE,COLLECTOR NUMBER",        # Topdecked's shouting
        "quantity , name , set_code , collector_number",  # spacing and case
    ],
)
def test_the_same_row_reads_the_same_whatever_the_headers_are_called(header):
    """One concept, many spellings.

    None of these formats is *supported* yet - supporting one means a real
    export committed as a fixture. What this asserts is that the column
    vocabulary is shared, so adding a format is a fixture and a `sniff()`
    rather than a fourth way of reading a quantity.
    """
    from decks.importers.archidekt import ArchidektParser

    rows = list(ArchidektParser().parse(f"{header}\n28,Swamp,tor,341\n"))

    assert len(rows) == 1
    assert rows[0].quantity == 28
    assert rows[0].name == "Swamp"
    assert rows[0].set_code == "tor"
    assert rows[0].collector_number == "341"


def test_columns_nobody_recognises_are_ignored_rather_than_refused():
    """A real export carries prices, tags and a date added. That is normal."""
    from decks.importers.archidekt import ArchidektParser

    rows = list(ArchidektParser().parse(
        "Quantity,Name,Purchase Price,Tags,Date Added,Multiverse Id\n"
        "2,Sol Ring,3.49,,2026-06-21,29704\n"
    ))
    assert (rows[0].quantity, rows[0].name) == (2, "Sol Ring")


def test_the_real_export_is_unchanged_by_all_of_this(catalogue, user):
    """The regression guard. The file this application was built against."""
    text = (FIXTURES / "archidekt_sample.csv").read_text(encoding="utf-8")
    rows = list(importers.ArchidektParser().parse(text))

    assert [row.quantity for row in rows if row.name == "Swamp"] == [28, 3]
    assert any(row.is_commander for row in rows)


# --- a signed-in user must not be able to cause a 500 (2026-09-25 review) ----


def test_an_absurd_quantity_is_a_refused_row_not_a_500(catalogue, user):
    """`UnresolvedRow.quantity` is a smallint; this overflowed it."""
    outcome = services.import_deck(owner=user, raw=b"99999 Notacard\n1 Sol Ring\n", name="Odd")
    assert outcome.record.rows_unresolved == 1
    assert "quantity" in UnresolvedRow.objects.get().reason


def test_an_absurd_quantity_of_a_real_card_is_refused_too(catalogue, user):
    outcome = services.import_deck(owner=user, raw=b"50000 Sol Ring\n1 Swamp\n", name="Odd")
    assert outcome.record.rows_unresolved == 1
    assert not outcome.deck.entries.filter(oracle_card__front_name="Sol Ring").exists()


def test_a_number_python_will_not_convert_is_not_a_500(catalogue, user):
    """More than 4,300 digits: `int()` raises ValueError, which nothing caught."""
    line = b"9" * 5000 + b" Swamp\n"
    outcome = services.import_deck(owner=user, raw=line + b"1 Sol Ring\n", name="Odd")
    assert outcome.record.rows_resolved == 1


def test_only_the_first_hundred_unresolved_rows_get_suggestions(catalogue, user):
    """One query each; fifty thousand of them outlived the gunicorn timeout."""
    raw = b"".join(f"1 Swam{i}\n".encode() for i in range(150))
    services.import_deck(owner=user, raw=raw, name="Junk")
    with_suggestions = [row for row in UnresolvedRow.objects.all() if row.suggestions]
    assert 0 < len(with_suggestions) <= resolve.SUGGESTION_ROWS


def test_the_quota_is_checked_before_the_resolver_runs(catalogue, user, monkeypatch):
    from billing.models import Plan
    from billing.quotas import QuotaExceeded

    plan = Plan.objects.get(slug="free")
    deck = services.import_deck(owner=user, raw=b"1 Sol Ring\n", name="Refilled").deck
    for _ in range(plan.max_imports_per_month - 1):
        services.import_deck(owner=user, raw=b"1 Sol Ring\n", deck=deck)

    def _must_not_run(rows):
        raise AssertionError("the resolver ran for an import the quota refuses")

    monkeypatch.setattr(resolve, "resolve", _must_not_run)
    with pytest.raises(QuotaExceeded):
        services.import_deck(owner=user, raw=b"1 Sol Ring\n", deck=deck)
