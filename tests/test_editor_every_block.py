"""Card model phase 2, PR 7: the editor over every block.

The four blocks that could only be switched off get a form: when a land
enters untapped, mana kept as counters, +1/+1 counters, and a mana rule
where the reader read one. A form whose boxes depend on a variant (a
condition's kind, a rule) has that variant picked first and never changes it
itself. Saving a form without touching it gives back what it showed, so it
cannot move an engine card. The tutors' count leaves the judgement form for
the Search block, and a player's row that said it the old way is said the
new way where the engine plays the card the same (migration 0016).
"""

import uuid
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.urls import reverse

from cards import profiles
from cards.models import OracleCard
from decks.models import Deck, DeckCard
from simulations import annotations, editor, primitives, services, unread
from simulations.engine import adapter
from simulations.engine.adapter import DECK_SCOPE
from simulations.forms import AnnotationForm
from simulations.models import CardAnnotation


def _post(key, boxes) -> QueryDict:
    """A form's boxes as a browser posts them, untouched."""
    data = QueryDict(mutable=True)
    for name, item in boxes.items():
        field = f"{key}-{name}"
        if isinstance(item, bool):
            if item:
                data[field] = "on"
        elif isinstance(item, list | tuple):
            data.setlist(field, list(item))
        else:
            data[field] = "" if item is None else str(item)
    return data


def _saved_untouched(key, value):
    form = editor.form_class(editor.specs_of(key, False, value))(
        _post(key, editor.initial(key, value)), prefix=key)
    assert form.is_valid(), form.errors
    return editor.value_from(key, value, form.cleaned_data)


# --- the registry ---------------------------------------------------------------------


def test_every_block_but_the_lists_has_a_form():
    assert primitives.EDITABLE | editor.LISTS == primitives.KEYS


def test_every_variant_starts_from_a_value_the_engine_stores():
    for key, variants in primitives.VARIANTS.items():
        assert set(variants.labels) <= set(variants.specs), key
        for name, value in variants.defaults.items():
            assert variants.of(value) == name
            assert primitives.validate(key, value) == value
            for spec in variants.specs[name]:
                assert spec.name in primitives.BY_KEY[key].keys, (key, spec.name)


def test_a_mana_rule_is_edited_where_it_was_read_never_added():
    assert not editor.can_add("mana_rule")
    assert editor.variant_choices("mana_rule") == []
    assert editor.can_edit("mana_rule", {"rule": "counts", "subtype": "elf", "color": "G",
                                         "activation": 0})
    # Granted and added mana keep "Off" and "Back to the card data" only.
    assert not editor.can_edit("mana_rule", {"rule": "extra", "subtype": "enchanted"})
    assert editor.starting_value("mana_rule", {"rule": "extra"}) is None
    assert editor.can_add("tapped_unless") and editor.can_add("growth")


def test_the_reader_s_own_compounds_are_values_the_engine_stores():
    """Until PR 7 these readings could not even be kept as they were."""
    for key, value in (
        ("mana_rule", {"rule": "counts", "subtype": "basic:swamp", "color": "B",
                       "activation": 0}),
        ("mana_rule", {"rule": "counts", "subtype": "names:Urza's Mine|Urza's Tower",
                       "color": "C", "activation": 0, "produces": {"C": 2}}),
        ("mana_rule", {"rule": "counts", "subtype": "power", "color": "G", "activation": 1,
                       "pays": "G"}),
        ("growth", {"enters": 1, "grows": ["enters:elf", "cast:noncreature"]}),
        ("tap_mana", {"types": ["creature"], "amount": 2, "produces": {"C": 2},
                      "spend_only": [{"types": ["artifact"], "subtypes": []}]}),
    ):
        assert primitives.validate(key, value) == value
    with pytest.raises(primitives.PrimitiveError):
        primitives.validate("mana_rule", {"rule": "counts", "subtype": "<b>elf</b>"})


# --- an untouched form says what it was shown -----------------------------------------


@pytest.mark.parametrize(("key", "value"), [
    ("tapped_unless", {"kind": "pay_life", "life": 2}),
    ("tapped_unless", {"kind": "control_type", "types": ["island", "swamp"]}),
    ("tapped_unless", {"kind": "reveal", "types": ["giant"]}),
    ("tapped_unless", {"kind": "lands", "count": 2, "at_least": False, "other": True,
                       "basic": False, "type": ""}),
    ("tapped_unless", {"kind": "permanent", "types": ["creature"], "legendary": True}),
    ("mana_store", {"use": "tap", "kind": "charge", "per_use": 1, "amount": 1,
                    "produces": {"G": 1}, "start": 2, "start_from": "", "kick": 0,
                    "grows": "", "empty": "sacrifice", "target": False, "once": False}),
    ("growth", {"enters": 2}),
    ("growth", {"enters_x": True, "grows": ["evolve"], "adapt": {"n": 2, "cost": "{2}{G}"}}),
    ("mana_rule", {"rule": "counts", "subtype": "elf", "color": "", "activation": 0}),
    ("mana_rule", {"rule": "per_controlled", "subtype": "swamp", "activation": 2,
                   "color": "B"}),
    ("mana_rule", {"rule": "lands_could_produce", "subtype": "", "activation": 0,
                   "color": "", "with_counter": 3}),
    # The searches an untouched form used to change: a filter's empty boxes,
    # and Chord of Calling's X, which no box can hold.
    ("tutor", {"to": "graveyard", "count": 3, "kind": "creature"}),
    ("tutor", {"to": "battlefield", "count": 1, "kind": "creature", "max_mv": "X"}),
])
def test_an_untouched_form_gives_back_what_it_showed(key, value):
    assert _saved_untouched(key, value) == value


def test_a_lands_condition_without_a_direction_is_more_or_more():
    """The reader writes `{"kind": "lands", "count": 1, "basic": True}` for "a
    basic land"; the adapter reads a missing `at_least` as true."""
    value = {"kind": "lands", "count": 1, "basic": True}
    assert editor.initial("tapped_unless", value)["at_least"] is True
    assert _saved_untouched("tapped_unless", value) == value


def test_a_store_makes_its_colour_as_much_as_one_use_adds():
    value = primitives.DEFAULTS["mana_store"]
    form = editor.form_class(editor.specs_of("mana_store", False, value))(
        _post("mana_store", editor.initial("mana_store", value) | {"produces": "G",
                                                                    "amount": 2}),
        prefix="mana_store")
    assert form.is_valid(), form.errors
    said = editor.value_from("mana_store", value, form.cleaned_data)
    assert said["produces"] == {"G": 2} and said["kind"] == "charge"


def test_a_one_colour_rule_offers_one_colour():
    """Cabal Coffers' colour, Crypt Ghast's, a counting ritual's: one letter,
    never a set the engine would play as a colour it does not know."""
    coffers = {"rule": "per_controlled", "subtype": "swamp", "activation": 2, "color": ""}
    assert _saved_untouched("mana_rule", coffers) == coffers
    ritual = {"rule": "ritual", "subtype": "creature", "color": "G", "activation": 0}
    specs = editor.specs_of("mana_rule", False, ritual)
    for typed, stored in (("R", "R"), ("", "G")):
        form = editor.form_class(specs)(_post("mana_rule", {"color": typed}), prefix="mana_rule")
        assert form.is_valid()
        assert editor.value_from("mana_rule", ritual, form.cleaned_data)["color"] == stored
    form = editor.form_class(specs)(_post("mana_rule", {"color": "WU"}), prefix="mana_rule")
    assert not form.is_valid()


def test_a_rule_counts_some_type():
    coffers = {"rule": "per_controlled", "subtype": "swamp", "activation": 2, "color": "B"}
    form = editor.form_class(editor.specs_of("mana_rule", False, coffers))(
        _post("mana_rule", editor.initial("mana_rule", coffers) | {"subtype": ""}),
        prefix="mana_rule")
    assert form.is_valid()
    with pytest.raises(editor.forms.ValidationError):
        editor.value_from("mana_rule", coffers, form.cleaned_data)


def test_a_condition_reads_as_a_sentence():
    def said(**fields):
        return editor._unless_text(SimpleNamespace(**({"count": 0, "types": frozenset(),
                                                     "legendary": False} | fields)))

    assert said(kind="control_type", types={"island", "swamp"}) == (
        "untapped if you control a land of type Island / Swamp")
    assert said(kind="permanent", types={"creature"}, legendary=True) == (
        "untapped if you control a legendary permanent of type Creature")
    assert said(kind="lands", count=2, at_least=False, other=True, basic=False, type="") == (
        "untapped if you control 2 or fewer lands (not counting this one)")


def test_colours_are_letters_each_once():
    value = {"rule": "counts", "subtype": "elf", "color": "G", "activation": 0}
    boxes = editor.initial("mana_rule", value)
    form_class = editor.form_class(editor.specs_of("mana_rule", False, value))
    for typed, stored in (("gw", "WG"), ("WUBRG", "WUBRG"), ("", "")):
        form = form_class(_post("mana_rule", boxes | {"color": typed}), prefix="mana_rule")
        assert form.is_valid()
        assert editor.value_from("mana_rule", value, form.cleaned_data)["color"] == stored
    for typed in ("GG", "X", "<b>"):
        form = form_class(_post("mana_rule", boxes | {"color": typed}), prefix="mana_rule")
        assert form.is_valid()
        with pytest.raises(editor.forms.ValidationError):
            editor.value_from("mana_rule", value, form.cleaned_data)


def test_a_condition_needs_a_type_to_look_for():
    value = {"kind": "permanent", "types": ["creature"], "legendary": False}
    form = editor.form_class(editor.specs_of("tapped_unless", False, value))(
        _post("tapped_unless", {"types": "", "legendary": False}), prefix="tapped_unless")
    assert form.is_valid()
    with pytest.raises(editor.forms.ValidationError):
        editor.value_from("tapped_unless", value, form.cleaned_data)


def test_another_variant_starts_from_its_own_defaults():
    shock = {"kind": "pay_life", "life": 2}
    assert editor.starting_value("tapped_unless", shock) == shock
    assert editor.starting_value("tapped_unless", shock, "turn") == {"kind": "turn",
                                                                     "count": 3}
    assert editor.starting_value("tapped_unless", None) is None
    assert editor.starting_value("tapped_unless", shock, "nonsense") == shock


# --- on the card page --------------------------------------------------------------------


@pytest.fixture
def deck(db):
    owner = get_user_model().objects.create_user(email="blocks@example.com",
                                                 password="pw-for-test-only")

    def made(name, type_line, text, cost="", produced=()):
        found = OracleCard.objects.create(
            oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
            type_line=type_line, oracle_text=text, produced_mana=list(produced),
            mana_cost=cost, keywords=[])
        profiles.rebuild(OracleCard.objects.filter(pk=found.pk))
        return OracleCard.objects.select_related("profile").get(pk=found.pk)

    cards = SimpleNamespace(
        crypt=made("Test Crypt", "Land — Swamp Mountain",
                   "({T}: Add {B} or {R}.)\nAs this land enters, you may pay 2 life. If you "
                   "don't, it enters tapped.", produced=("B", "R")),
        wild=made("Test Growth", "Enchantment — Aura",
                  "Enchant land\nWhenever enchanted land is tapped for mana, its controller "
                  "adds an additional {G}.", "{G}"),
        seer=made("Test Seer", "Creature — Human Wizard", "Draw a card.", "{1}{U}"),
        gate=made("Test Gate", "Land", "{T}: Add {C}.", produced=("C",)),
    )
    deck = Deck.objects.create(owner=owner, name="Every block")
    for found in vars(cards).values():
        DeckCard.objects.create(deck=deck, oracle_card=found, quantity=1)
    return SimpleNamespace(deck=deck, owner=owner, **vars(cards))


def _page(client, deck, card, query=""):
    url = reverse("simulations:annotate", args=[deck.deck.pk, card.pk])
    return client.get(url + query).content.decode()


def _save(client, deck, card, key, boxes, variant=""):
    url = reverse("simulations:primitive", args=[deck.deck.pk, card.pk, key])
    data = {"scope": DECK_SCOPE, "action": "save", **({"variant": variant} if variant else {}),
            **{f"{key}-{name}": item for name, item in boxes.items()}}
    return client.post(url, data)


def _played(deck, card):
    return next(reading.card for reading in adapter.readings(deck.deck)
                if reading.oracle_card.pk == card.pk)


def test_the_shock_land_s_life_is_changed_on_its_page(deck, client):
    assert deck.crypt.profile.tapped_unless == {"kind": "pay_life", "life": 2}
    client.force_login(deck.owner)
    text = _page(client, deck, deck.crypt, "?edit=tapped_unless")
    assert 'name="tapped_unless-life"' in text and 'name="variant" value="pay_life"' in text
    assert "untapped if you pay 2 life" in _page(client, deck, deck.crypt)

    _save(client, deck, deck.crypt, "tapped_unless", {"life": "3"}, variant="pay_life")

    row = CardAnnotation.objects.get(oracle_card=deck.crypt)
    assert row.overrides == {"tapped_unless": {"kind": "pay_life", "life": 3}}
    assert _played(deck, deck.crypt).tapped_unless.count == 3


def test_a_condition_is_picked_first_then_filled_in(deck, client):
    client.force_login(deck.owner)
    text = _page(client, deck, deck.gate, "?edit=tapped_unless")
    assert "data-variants" in text and "Which one is it?" in text
    assert 'name="tapped_unless-count"' not in text

    text = _page(client, deck, deck.gate, "?edit=tapped_unless&variant=lands")
    assert 'name="tapped_unless-count"' in text and 'value="lands"' in text

    _save(client, deck, deck.gate, "tapped_unless",
          {"count": "2", "at_least": "on", "other": "on", "type": ""}, variant="lands")

    assert CardAnnotation.objects.get(oracle_card=deck.gate).overrides["tapped_unless"] == {
        "kind": "lands", "count": 2, "at_least": True, "other": True, "basic": False,
        "type": ""}


def test_a_form_may_turn_a_condition_into_another_kind(deck, client):
    client.force_login(deck.owner)
    text = _page(client, deck, deck.crypt, "?edit=tapped_unless")
    assert "It is another kind" in text and "variant=turn" in text

    _save(client, deck, deck.crypt, "tapped_unless", {"count": "2"}, variant="turn")

    assert CardAnnotation.objects.get(oracle_card=deck.crypt).overrides == {
        "tapped_unless": {"kind": "turn", "count": 2}}


def test_granted_mana_has_no_form_and_no_save(deck, client):
    assert deck.wild.profile.mana_rule["rule"] == "extra"
    client.force_login(deck.owner)
    text = _page(client, deck, deck.wild, "?edit=mana_rule")
    assert 'id="editing"' not in text
    assert _save(client, deck, deck.wild, "mana_rule", {"color": "G"}).status_code == 404
    assert not CardAnnotation.objects.filter(oracle_card=deck.wild).exists()


def test_counters_are_added_to_a_creature_by_hand(deck, client):
    client.force_login(deck.owner)
    _save(client, deck, deck.seer, "growth", {"enters": "2"})

    assert CardAnnotation.objects.get(oracle_card=deck.seer).overrides == {
        "growth": {"enters": 2}}
    assert _played(deck, deck.seer).growth.enters == 2
    assert "enters with 2 +1/+1 counters" in _page(client, deck, deck.seer)


def test_a_wrong_box_is_shown_again_not_stored(deck, client):
    client.force_login(deck.owner)
    answer = _save(client, deck, deck.gate, "tapped_unless", {"types": ""},
                   variant="permanent")
    assert answer.status_code == 400
    assert "Name at least one type." in answer.content.decode()
    assert not CardAnnotation.objects.filter(oracle_card=deck.gate).exists()


# --- the tutors' count -------------------------------------------------------------------


def test_the_judgement_form_no_longer_asks_for_a_count():
    assert "tutor_count" not in AnnotationForm().fields
    assert "tutor_count" not in annotations.EDITABLE_KEYS


def test_a_stored_count_survives_a_judgement_save(deck):
    CardAnnotation.objects.create(owner=deck.owner, deck=deck.deck, oracle_card=deck.seer,
                                  overrides={"tutor_count": 2})
    services.save_annotation(deck=deck.deck, oracle_card=deck.seer, scope=DECK_SCOPE,
                             judgements=dict.fromkeys(annotations.EDITABLE_KEYS)
                             | {"priority": 50})
    assert CardAnnotation.objects.get(oracle_card=deck.seer).overrides == {
        "tutor_count": 2, "priority": 50}


def test_a_count_is_said_as_the_search_block():
    assert annotations.tutor_as_block({"priority": 5}) is None
    assert annotations.tutor_as_block({"tutor_count": 0, "tutor_life": 2}) == {
        "tutor": None, "tutor_life": 2}
    assert annotations.tutor_as_block({"tutor_count": 2, "tutor_to_hand": False,
                                       "tutor_kind": "creature"}) == {
        "tutor": {"to": "graveyard", "count": 2}, "tutor_kind": "creature"}


# --- what is read from patches -----------------------------------------------------------


def test_players_answers_are_counted_by_what_they_say_today(deck):
    other = get_user_model().objects.create_user(email="other@example.com",
                                                 password="pw-for-test-only")
    for owner in (deck.owner, other):
        CardAnnotation.objects.create(owner=owner, oracle_card=deck.crypt,
                                      overrides={"tapped_unless": {"kind": "pay_life",
                                                                   "life": 3}})
    players, common = unread.answers(deck.crypt)
    assert players == 2 and common[0][1] == 2


