"""What holds the deck together (B-02): the network picture on the deck page.

What these tests are for, in order of how much they matter:

1. **Every line is a real link.** A pair is a line, an engine a chain, a
   combo joins each of its cards to every other; two cards joined twice keep
   one line, drawn as the strongest kind.
2. **What should stand out does.** Hubs grow (up to a cap), loners are
   marked and a cut candidate among them points to "What could go"; lands
   stay in sight as their own group but are never loners to worry about.
3. **The same deck draws the same picture**, inside its frame.
4. **Names are escaped**, and the page shows the picture only when two
   cards are joined.
"""

import math
from types import SimpleNamespace

import pytest

from decks import graph
from decks.graph import COMBO, ENGINE, LANDS, OTHER, PAIR, Spot


def _spots(*names, group="ramp", **extra):
    return [Spot(name=name, shown=name, group=group, **extra) for name in names]


def _deck():
    return [
        Spot(name="Chainer", shown="Chainer", group=OTHER, commander=True),
        *_spots("Viscera Seer", "Blood Artist", "Zulaport Cutthroat", group=ENGINE),
        *_spots("Sol Ring", "Arcane Signet"),
        Spot(name="Swamp", shown="Swamp", group=LANDS, quantity=30, land=True),
        Spot(name="Phyrexian Tower", shown="Phyrexian Tower", group=LANDS, land=True),
    ]


def _edge(drawn, a, b):
    return next((edge for edge in drawn.edges if {edge.a.name, edge.b.name} == {a, b}), None)


def _node(drawn, name):
    return next(node for node in drawn.nodes if node.name == name)


# --- 1. the lines -----------------------------------------------------------


def test_a_pair_is_one_line():
    drawn = graph.build(_deck(), [("Viscera Seer", "Blood Artist", PAIR)])

    assert len(drawn.edges) == 1
    assert _edge(drawn, "Viscera Seer", "Blood Artist").kind == PAIR


def test_an_engine_is_a_chain_and_a_combo_joins_every_two():
    reading = SimpleNamespace(
        pairs=[], more_pairs=[],
        engines=[SimpleNamespace(cards=("Viscera Seer", "Blood Artist", "Zulaport Cutthroat"))])
    combos = [{"cards": ["Chainer", "Viscera Seer", "Phyrexian Tower"]}]

    links = graph.links_of(reading, combos)

    assert ("Viscera Seer", "Blood Artist", ENGINE) in links
    assert ("Blood Artist", "Zulaport Cutthroat", ENGINE) in links
    assert ("Viscera Seer", "Zulaport Cutthroat", ENGINE) not in links
    assert {(a, b) for a, b, kind in links if kind == COMBO} == {
        ("Chainer", "Viscera Seer"), ("Chainer", "Phyrexian Tower"),
        ("Viscera Seer", "Phyrexian Tower")}


def test_two_cards_joined_twice_keep_one_line_of_the_strongest_kind():
    drawn = graph.build(_deck(), [("Viscera Seer", "Blood Artist", PAIR),
                                  ("Blood Artist", "Viscera Seer", COMBO),
                                  ("Viscera Seer", "Blood Artist", ENGINE)])

    assert len(drawn.edges) == 1
    assert drawn.edges[0].kind == COMBO
    assert _node(drawn, "Viscera Seer").lines == 1


def test_a_card_that_left_the_deck_or_with_itself_draws_no_line():
    drawn = graph.build(_deck(), [("Viscera Seer", "Blood Artist", PAIR),
                                  ("Viscera Seer", "Gone Card", PAIR),
                                  ("Sol Ring", "Sol Ring", PAIR)])

    assert len(drawn.edges) == 1


def test_a_double_faced_card_is_found_by_its_front():
    spots = [Spot(name="Front // Back", shown="Front", group=OTHER),
             *_spots("Viscera Seer", group=ENGINE)]

    drawn = graph.build(spots, [("Front", "Viscera Seer", PAIR)])

    assert _edge(drawn, "Front // Back", "Viscera Seer") is not None
    assert _node(drawn, "Front // Back").caption == "Front"


# --- 2. what stands out -----------------------------------------------------


def test_hubs_grow_with_their_lines_up_to_a_cap():
    spots = _spots("Hub", *(f"Card {i}" for i in range(10)), group=ENGINE)
    links = [("Hub", f"Card {i}", PAIR) for i in range(10)]

    drawn = graph.build(spots, links)

    hub, leaf = _node(drawn, "Hub"), _node(drawn, "Card 0")
    assert hub.lines == 10 and leaf.lines == 1
    assert hub.r > leaf.r
    assert hub.r == graph.DOT + graph.DOT_STEP * graph.DOT_LINES_MAX
    assert drawn.hubs[0] is hub
    assert hub.named and not leaf.named


def test_loners_are_marked_and_a_cut_candidate_among_them_too():
    drawn = graph.build(_deck(), [("Viscera Seer", "Blood Artist", PAIR)],
                        cut_names={"Arcane Signet"})

    loners = {node.name for node in drawn.loners}
    assert loners == {"Zulaport Cutthroat", "Sol Ring", "Arcane Signet"}
    assert [node.name for node in drawn.cuts] == ["Arcane Signet"]
    assert _node(drawn, "Sol Ring").kind == "loner"


def test_lands_stay_in_sight_but_are_never_loners():
    drawn = graph.build(_deck(), [("Viscera Seer", "Phyrexian Tower", PAIR)])

    assert {node.name for node in drawn.lands} == {"Swamp", "Phyrexian Tower"}
    assert drawn.land_count == 31
    assert not any(node.land for node in drawn.loners)
    lands = next(group for group in drawn.groups if group.key == LANDS)
    assert lands.copies == 31 and lands.label == "Lands"


def test_the_same_land_twice_is_one_dot_with_its_copies():
    spots = [*_deck(), Spot(name="Swamp", shown="Swamp", group=LANDS, quantity=3, land=True)]

    drawn = graph.build(spots, [("Viscera Seer", "Blood Artist", PAIR)])

    swamps = [node for node in drawn.nodes if node.name == "Swamp"]
    assert len(swamps) == 1 and swamps[0].quantity == 33
    assert swamps[0].caption == "Swamp ×33"


@pytest.mark.parametrize(("land", "combo", "engine", "primary", "group"), [
    (True, True, True, "ramp", LANDS),
    (False, True, True, "ramp", COMBO),
    (False, False, True, "ramp", ENGINE),
    (False, False, False, "ramp", "ramp"),
    (False, False, False, "", OTHER),
    (False, False, False, "not-a-role", OTHER),
])
def test_a_land_is_a_land_first_then_what_wins_what_runs_and_its_role(
        land, combo, engine, primary, group):
    assert graph.group_of(land=land, in_combo=combo, in_engine=engine,
                          primary=primary) == group


# --- 3. the layout ----------------------------------------------------------


def test_the_same_deck_draws_the_same_picture():
    links = [("Viscera Seer", "Blood Artist", PAIR), ("Chainer", "Sol Ring", COMBO)]

    first, second = graph.build(_deck(), links), graph.build(_deck(), links)

    assert [(n.name, n.x, n.y, n.r) for n in first.nodes] == \
           [(n.name, n.x, n.y, n.r) for n in second.nodes]
    assert [edge.path for edge in first.edges] == [edge.path for edge in second.edges]


def test_the_commander_is_in_the_middle_lines_inside_loners_outside():
    drawn = graph.build(_deck(), [("Viscera Seer", "Blood Artist", PAIR),
                                  ("Chainer", "Viscera Seer", COMBO)])
    cx, cy = graph.CENTRE

    def radius(name):
        node = _node(drawn, name)
        return math.hypot(node.x - cx, node.y - cy)

    assert radius("Chainer") == pytest.approx(0)
    assert radius("Blood Artist") == pytest.approx(graph.INNER)
    assert radius("Sol Ring") == pytest.approx(graph.OUTER)
    assert _edge(drawn, "Chainer", "Viscera Seer").path.count("L") == 1  # straight
    assert "Q" in _edge(drawn, "Viscera Seer", "Blood Artist").path


def test_every_dot_and_group_name_fits_the_frame():
    spots = [*_deck(), *_spots(*(f"Filler {i}" for i in range(60)), group="draw")]

    drawn = graph.build(spots, [("Viscera Seer", "Blood Artist", PAIR)])

    for node in drawn.nodes:
        assert node.r <= node.x <= graph.WIDTH - node.r
        assert node.r <= node.y <= graph.HEIGHT - node.r
    for group in drawn.groups:
        assert 0 < group.x < graph.WIDTH and 0 < group.y < graph.HEIGHT
    keys = [group.key for group in drawn.groups]
    assert keys == sorted(keys, key=graph.GROUP_ORDER.index)


def test_no_line_no_picture():
    assert graph.build(_deck(), []) is None
    assert graph.build(_deck(), [("Gone", "Also gone", PAIR)]) is None


# --- 4. on the page ---------------------------------------------------------


@pytest.mark.django_db
class TestOnThePage:
    @pytest.fixture
    def page(self, client, catalogue, monkeypatch):
        from pathlib import Path

        from django.contrib.auth import get_user_model

        from decks import services as deck_services

        owner = get_user_model().objects.create_user(email="graph@example.com",
                                                     password="pw-for-test-only")
        csv = Path(__file__).resolve().parent / "fixtures" / "archidekt_sample.csv"
        deck = deck_services.import_deck(owner=owner, raw=csv.read_bytes(), name="Chainer",
                                         filename="sample.csv").deck
        client.force_login(owner)

        def get(combos=None):
            monkeypatch.setattr("simulations.summary.combos_of", lambda _deck: combos)
            return client.get(deck.get_absolute_url()).content.decode()
        return get

    def test_a_combo_in_the_deck_draws_the_picture(self, page):
        body = page([{"cards": ["Carrion Feeder", "Reassembling Skeleton"], "produces": []}])

        figure = body[body.index("data-graph"):body.index("</figure>")]
        assert "What holds the deck together" in figure
        assert figure.count("graph-edge-combo") == 2  # the line and the legend
        assert "Carrion Feeder" in figure and "Reassembling Skeleton" in figure
        assert "Swamp ×31" in figure  # the lands stay in sight, one dot
        assert 'src="/static/js/deck-graph.js"' in body

    def test_no_link_no_picture(self, page):
        body = page(None)

        assert "data-graph-svg" not in body


def test_every_name_is_escaped():
    from django.template.loader import render_to_string

    spots = [Spot(name="<script>alert(1)</script>", shown="<script>alert(1)</script>",
                  group=ENGINE),
             *_spots("Blood Artist", group=ENGINE)]
    drawn = graph.build(spots, [("<script>alert(1)</script>", "Blood Artist", PAIR)])

    html = render_to_string("decks/_graph.html", {"graph": drawn})

    assert "<script>alert" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_a_cut_candidate_points_to_what_could_go():
    from django.template.loader import render_to_string

    drawn = graph.build(_deck(), [("Viscera Seer", "Blood Artist", PAIR)],
                        cut_names={"Sol Ring"})

    html = render_to_string("decks/_graph.html", {"graph": drawn})

    assert 'href="#strategies"' in html
    assert "graph-node-cut" in html
    assert "1 loner is a cut candidate" in html
