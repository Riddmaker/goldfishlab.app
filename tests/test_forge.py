"""Card model phase 0.5: reading card scripts in Forge's format.

The scripts below are made up for these tests, in the format's shape; none is
copied from Forge's card data.
"""

from cards import forge

ROCK = """Name:Test Rock
ManaCost:2
Types:Artifact
A:AB$ Mana | Cost$ T | Produced$ Combo G U | SpellDescription$ Add {G} or {U}.
Oracle:{T}: Add {G} or {U}.
"""

SORCERY = """Name:Test Growth
ManaCost:1 G
Types:Sorcery
A:SP$ ChangeZone | Origin$ Library | Destination$ Battlefield | ChangeType$ Land.Basic \
| Tapped$ True | SubAbility$ DBDraw
SVar:DBDraw:DB$ Draw | NumCards$ 2 | SubAbility$ DBToken
SVar:DBToken:DB$ Token | TokenAmount$ 1 | TokenScript$ c_a_treasure_sac
Oracle:Search for a basic land, then draw two cards and create a Treasure.
"""

TAPLAND = """Name:Test Gate
ManaCost:no cost
Types:Land Forest Island
R:Event$ Moved | ValidCard$ Card.Self | Destination$ Battlefield | ReplaceWith$ ETBTapped
SVar:ETBTapped:DB$ Tap | Defined$ Self | ETB$ True
K:Hexproof
Oracle:Test Gate enters tapped.
"""

MODAL = """Name:Test Spell
ManaCost:1 B
Types:Instant
A:SP$ LoseLife | Defined$ Opponent | LifeAmount$ 2
AlternateMode:Modal
Oracle:Each opponent loses 2 life.

ALTERNATE

Name:Test Vale
ManaCost:no cost
Types:Land
A:AB$ Mana | Cost$ T | Produced$ B
Oracle:{T}: Add {B}.
"""

LOOP = """Name:Test Loop
ManaCost:1
Types:Artifact
A:AB$ Draw | Cost$ 1 T Sac<1/Creature> PayLife<2> | NumCards$ 1 | SubAbility$ DBA
SVar:DBA:DB$ Draw | NumCards$ 1 | SubAbility$ DBA
Oracle:A chain that names itself.
"""


def test_a_script_has_faces_and_abilities():
    script = forge.parse(MODAL)
    assert [face.name for face in script.faces] == ["Test Spell", "Test Vale"]
    assert script.mode == "Modal"
    assert script.faces[0].abilities[0].spell
    assert script.faces[1].abilities[0].head == "Mana"


def test_sub_abilities_are_chained():
    ability = forge.parse(SORCERY).faces[0].abilities[0]
    assert ability.head == "ChangeZone"
    assert ability.chain[0].head == "Draw"
    assert ability.chain[0].chain[0].head == "Token"


def test_a_chain_that_loops_is_cut():
    assert forge.constructs(forge.parse(LOOP))["DB:Draw"] == 12


def test_cost_atoms():
    assert forge.cost_atoms("1 T Sac<1/Creature> PayLife<2>") == ["PayLife", "Sac", "mana", "tap"]
    assert forge.cost_atoms("W/U 2 Q") == ["mana", "untap"]


def test_constructs_name_kind_head_cost_and_keyword():
    found = forge.constructs(forge.parse(TAPLAND))
    assert found == {"R:Moved": 1, "DB:Tap": 1, "K:Hexproof": 1}
    assert forge.constructs(forge.parse(ROCK)) == {"AB:Mana": 1, "cost:tap": 1}


def test_coverage_counts_cards_that_use_only_the_vocabulary():
    scripts = [forge.parse(text) for text in (ROCK, SORCERY, TAPLAND)]
    words = forge.vocabulary(scripts, 2)
    assert forge.coverage(scripts, words).covered < 3
    assert forge.coverage(scripts, forge.vocabulary(scripts, 50)).share == 1.0
