"""Every backlog card answers the project's test question (B-23).

The backlog lives in docs/SEQUENCE-SUPPORT-KPI.html, which is gitignored (HABIT 5):
on a machine without it, such as CI, these tests skip. Where it exists, each card
names the pillar it serves and the abilities it speeds up, so no idea enters the
project without saying how it makes you know your deck faster.
"""

import re
from pathlib import Path

import pytest

BACKLOG = Path(__file__).resolve().parent.parent / "docs" / "SEQUENCE-SUPPORT-KPI.html"
PILLARS = {
    "1 Tausendmal gespielt",
    "2 Selbst in der Hand",
    "3 Durchschauen",
    "4 Formen",
    "5 Verschmelzen",
    "Fundament",
}
CARD = re.compile(r'<div class="card"><h3>(B-\d{2}) .*?</div>', re.S)


def cards():
    if not BACKLOG.is_file():
        pytest.skip("the backlog is a local, gitignored doc")
    return {m.group(1): m.group(0) for m in CARD.finditer(BACKLOG.read_text(encoding="utf-8"))}


def test_the_backlog_has_cards():
    assert len(cards()) >= 20


def test_every_card_names_one_known_pillar():
    for card_id, card in cards().items():
        pillars = re.findall(r'<span class="tag">Säule: ([^<]+)</span>', card)
        assert len(pillars) == 1, f"{card_id} needs exactly one pillar"
        assert pillars[0] in PILLARS, f"{card_id}: unknown pillar {pillars[0]!r}"


def test_every_card_names_its_abilities():
    for card_id, card in cards().items():
        abilities = re.findall(r'<span class="tag">Fähigkeit: ([^<]+)</span>', card)
        assert len(abilities) == 1, f"{card_id} needs its abilities (1-5) or what it carries"


def test_card_ids_are_never_reused():
    ids = CARD.findall(BACKLOG.read_text(encoding="utf-8")) if BACKLOG.is_file() else []
    assert len(ids) == len(set(ids))
