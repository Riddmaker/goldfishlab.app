"""What holds the deck together, as one picture (B-02, pillar 3).

Every card of the deck is a dot, every way two cards work together a line:

* a **pair** of a theme (`cards.synergy.Pair`) is one line,
* an **engine** (`cards.synergy.Engine`) is a chain, step to step,
* a **combo** (`simulations.summary.combos_of` and the catalogue's known
  ones) joins each of its cards to every other.

Two cards joined twice keep one line, drawn as the strongest of its kinds
(combo, then engine, then pair). A dot grows with its lines, up to a cap, so
the hubs stand out; the cards with none are the loners. A loner the cut rule
names (`simulations.cuts`) is marked: that is where the picture points to
"What could go".

The lands are in, as a group of their own: most of them are loners by
nature, but the deck's mana must stay in sight. A land is never a loner to
worry about, and the same land in several printings is one dot with its
copies.

The layout is fixed, not simulated: the commander in the middle, each group
a sector of the circle (sized by its cards), the cards with lines on the
inner ring and the loners on the outer one. The same deck always draws the
same picture, so the page does not jump and a test can say where a card is.
The page draws the SVG (`decks/_graph.html`); static/js/deck-graph.js only
lights a card and its neighbours.

Nothing here touches the database.
"""

import math
from dataclasses import dataclass, field

from django.utils.translation import gettext, gettext_noop

from simulations.report import SEEN_MECHANIC_ROLES, SEEN_ROLES, SEEN_STRATEGY_ROLES

PAIR = "pair"
ENGINE = "engine"
COMBO = "combo"
#: The kinds of line, the weakest first: a line joined twice is drawn as its strongest.
KINDS = (PAIR, ENGINE, COMBO)

LANDS = "lands"
OTHER = "other"
#: The groups that are not a role, in English (translated where shown).
GROUP_LABELS = {
    COMBO: gettext_noop("Combo pieces"),
    ENGINE: gettext_noop("Engines and pairs"),
    LANDS: gettext_noop("Lands"),
    OTHER: gettext_noop("Other"),
}
ROLE_LABELS = dict(SEEN_ROLES + SEEN_STRATEGY_ROLES + SEEN_MECHANIC_ROLES)
#: Around the circle clockwise from the top: what wins, what runs, the
#: roles in the report's order, the rest, and the lands last.
GROUP_ORDER = (COMBO, ENGINE, *ROLE_LABELS, OTHER, LANDS)

#: The picture's size (SVG user units; it scales to the page).
WIDTH = 780
HEIGHT = 580
CENTRE = (WIDTH / 2, HEIGHT / 2)
INNER = 170
OUTER = 218
LABEL = 248
#: A dot's radius: a plain card, what each line adds, and the cap.
DOT = 4.0
DOT_STEP = 1.6
DOT_LINES_MAX = 6
COMMANDER_DOT = 15.0
#: The space between two sectors, in slots of one card.
GAP = 1.2
#: A sector is never narrower than this many cards, so its label fits.
SECTOR_MIN = 2
#: How far a line bends towards the middle (0 straight, 1 through it).
BEND = 0.55
#: The cards named without hovering: the biggest hubs, at most this many.
HUBS_NAMED = 8
HUB_LINES = 3


@dataclass(frozen=True)
class Spot:
    """One card as the picture needs it."""

    name: str
    shown: str
    group: str
    quantity: int = 1
    commander: bool = False
    land: bool = False


@dataclass
class Node:
    key: int
    name: str
    shown: str
    group: str
    quantity: int
    commander: bool
    land: bool
    lines: int = 0
    cut: bool = False
    near: list = field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    r: float = DOT
    #: Where the name goes, and which way it reads.
    label_x: float = 0.0
    label_y: float = 0.0
    anchor: str = "middle"
    named: bool = False

    @property
    def loner(self) -> bool:
        """A card with no line that could still be worth a look; never a
        land, never the commander."""
        return not self.lines and not self.land and not self.commander

    @property
    def kind(self) -> str:
        if self.commander:
            return "commander"
        if self.land:
            return "land"
        return "loner" if self.loner else "card"

    @property
    def caption(self) -> str:
        return f"{self.shown} ×{self.quantity}" if self.quantity > 1 else self.shown

    @property
    def near_keys(self) -> str:
        return " ".join(str(node.key) for node in self.near)


@dataclass
class Edge:
    a: Node
    b: Node
    kind: str
    path: str = ""


@dataclass
class Group:
    key: str
    label: str
    copies: int
    x: float = 0.0
    y: float = 0.0
    anchor: str = "middle"


@dataclass
class Graph:
    nodes: list
    edges: list
    groups: list
    width: int = WIDTH
    height: int = HEIGHT

    @property
    def hubs(self) -> list:
        """The cards with lines, the most joined first."""
        return sorted((node for node in self.nodes if node.lines),
                      key=lambda node: (-node.lines, node.shown.lower()))

    @property
    def loners(self) -> list:
        return [node for node in self.nodes if node.loner]

    @property
    def cuts(self) -> list:
        return [node for node in self.nodes if node.cut]

    @property
    def lands(self) -> list:
        return [node for node in self.nodes if node.land]

    @property
    def land_count(self) -> int:
        return sum(node.quantity for node in self.lands)


def group_of(*, land: bool, in_combo: bool, in_engine: bool, primary: str) -> str:
    """A land is a land first: the mana stays counted in one place; then
    what wins, what runs, the card's main role, and the rest."""
    if land:
        return LANDS
    if in_combo:
        return COMBO
    if in_engine:
        return ENGINE
    return primary if primary in ROLE_LABELS else OTHER


def links_of(reading, combos) -> list[tuple[str, str, str]]:
    """(card, card, kind) for each pair, each engine step and each two
    cards of a combo. Dismissed entries are not on the page, so not here."""
    links = []
    for pair in (*reading.pairs, *reading.more_pairs):
        links.append((pair.first, pair.second, PAIR))
    for engine in reading.engines:
        cards = engine.cards
        links += [(cards[i], cards[i + 1], ENGINE) for i in range(len(cards) - 1)]
    for combo in combos or []:
        cards = combo.get("cards", [])
        links += [(cards[i], cards[j], COMBO)
                  for i in range(len(cards)) for j in range(i + 1, len(cards))]
    return links


def from_page(readings, reading, combos, cuts) -> "Graph | None":
    """The picture from what the deck page has read already: the engine's
    readings, the themes' reading, every combo complete in the deck, the cut
    candidates."""
    links = links_of(reading, combos)
    named = {name for a, b, _kind in links for name in (a, b)}
    named |= {name.split(" // ", 1)[0] for name in named}
    combo_cards = {name for a, b, kind in links if kind == COMBO for name in (a, b)}
    combo_cards |= {name.split(" // ", 1)[0] for name in combo_cards}
    spots = []
    for entry in readings:
        oracle = entry.oracle_card
        names = {oracle.name, oracle.front_name}
        spots.append(Spot(
            name=oracle.name, shown=oracle.front_name, quantity=entry.quantity,
            commander=entry.is_commander, land=entry.card.is_land,
            group=group_of(land=entry.card.is_land, in_combo=bool(names & combo_cards),
                           in_engine=bool(names & named), primary=entry.primary)))
    return build(spots, links, {cut.name for cut in cuts})


def build(spots, links, cut_names=frozenset()) -> "Graph | None":
    """The laid-out picture, or None when no two cards are joined: a circle
    of loose dots says nothing the card list does not."""
    nodes, by_name = [], {}
    for spot in spots:
        node = by_name.get(spot.name)
        if node is not None:  # the same card twice, e.g. two printings
            node.quantity += spot.quantity
            continue
        node = Node(key=len(nodes), name=spot.name, shown=spot.shown, group=spot.group,
                    quantity=spot.quantity, commander=spot.commander, land=spot.land,
                    cut=spot.shown in cut_names or spot.name in cut_names)
        nodes.append(node)
        by_name[spot.name] = node
        by_name.setdefault(spot.shown, node)
    edges = _edges(links, by_name)
    if not edges:
        return None
    for edge in edges:
        for one, other in ((edge.a, edge.b), (edge.b, edge.a)):
            one.lines += 1
            one.near.append(other)
    graph = Graph(nodes=nodes, edges=edges, groups=[])
    _lay_out(graph)
    return graph


def _edges(links, by_name) -> list[Edge]:
    found = {}
    for a, b, kind in links:
        one, other = _find(by_name, a), _find(by_name, b)
        if one is None or other is None or one is other:
            continue  # a card that left the deck, or a card with itself
        key = (min(one.key, other.key), max(one.key, other.key))
        edge = found.get(key)
        if edge is None:
            first, second = sorted((one, other), key=lambda node: node.key)
            found[key] = Edge(a=first, b=second, kind=kind)
        elif KINDS.index(kind) > KINDS.index(edge.kind):
            edge.kind = kind
    return [found[key] for key in sorted(found)]


def _find(by_name, name: str):
    return by_name.get(name) or by_name.get(name.split(" // ", 1)[0])


def _lay_out(graph: Graph) -> None:
    cx, cy = CENTRE
    commanders = [node for node in graph.nodes if node.commander]
    for i, node in enumerate(commanders):
        node.x = cx + (i - (len(commanders) - 1) / 2) * COMMANDER_DOT * 2.4
        node.y = cy
        node.r = COMMANDER_DOT
        node.named = True
        node.label_x, node.label_y = node.x, node.y + COMMANDER_DOT + 16
    members = {}
    for node in graph.nodes:
        if not node.commander:
            members.setdefault(node.group, []).append(node)
    order = [key for key in GROUP_ORDER if key in members]
    order += sorted(set(members) - set(order))
    rings = {key: _rings(members[key]) for key in order}
    weights = {key: max(SECTOR_MIN, len(rings[key][0]), len(rings[key][1])) for key in order}
    total = sum(weights.values()) + GAP * len(order)
    step = 2 * math.pi / total
    angle = -math.pi / 2 - step * weights[order[0]] / 2 if order else 0
    hubs = {node.key for node in sorted((n for n in graph.nodes if n.lines >= HUB_LINES
                                         and not n.commander),
                                        key=lambda n: (-n.lines, n.shown.lower()))[:HUBS_NAMED]}
    for key in order:
        width = weights[key] * step
        inner, outer = rings[key]
        for ring, radius in ((inner, INNER), (outer, OUTER)):
            for i, node in enumerate(ring):
                theta = angle + width * (i + 0.5) / len(ring)
                _place(node, theta, radius, named=node.key in hubs)
        middle = angle + width / 2
        group_label = (gettext(ROLE_LABELS[key]) if key in ROLE_LABELS
                       else gettext(GROUP_LABELS.get(key, GROUP_LABELS[OTHER])))
        group = Group(key=key, label=group_label,
                      copies=sum(node.quantity for node in members[key]))
        group.x = cx + LABEL * math.cos(middle)
        group.y = cy + LABEL * math.sin(middle) + 4
        group.anchor = _anchor(math.cos(middle))
        graph.groups.append(group)
        angle += width + GAP * step
    for edge in graph.edges:
        edge.path = _path(edge.a, edge.b)


def _rings(group: list) -> tuple[list, list]:
    """The cards with lines (the most joined in the middle of the sector)
    and the ones without, each by name."""
    inner = sorted((node for node in group if node.lines),
                   key=lambda node: (-node.lines, node.shown.lower()))
    inner = inner[1::2][::-1] + inner[0::2]  # biggest in the middle
    outer = sorted((node for node in group if not node.lines),
                   key=lambda node: node.shown.lower())
    return inner, outer


def _place(node: Node, theta: float, radius: float, *, named: bool) -> None:
    cx, cy = CENTRE
    cos, sin = math.cos(theta), math.sin(theta)
    node.x, node.y = cx + radius * cos, cy + radius * sin
    node.r = DOT + DOT_STEP * min(node.lines, DOT_LINES_MAX)
    node.named = named
    # The inner ring names inwards, the outer one outwards: neither runs
    # into the other ring.
    reach = -(node.r + 5) if node.lines else node.r + 5
    node.label_x = node.x + reach * cos
    node.label_y = node.y + reach * sin + 4
    node.anchor = _anchor(cos if not node.lines else -cos)


def _anchor(cos: float) -> str:
    if cos > 0.25:
        return "start"
    if cos < -0.25:
        return "end"
    return "middle"


def _path(a: Node, b: Node) -> str:
    """A straight line from the commander, else a curve bent towards the
    middle, so lines across the circle do not run over the ring."""
    if a.commander or b.commander:
        return f"M{a.x:.1f} {a.y:.1f}L{b.x:.1f} {b.y:.1f}"
    cx, cy = CENTRE
    mx, my = (a.x + b.x) / 2, (a.y + b.y) / 2
    qx, qy = mx + (cx - mx) * BEND, my + (cy - my) * BEND
    return f"M{a.x:.1f} {a.y:.1f}Q{qx:.1f} {qy:.1f} {b.x:.1f} {b.y:.1f}"
