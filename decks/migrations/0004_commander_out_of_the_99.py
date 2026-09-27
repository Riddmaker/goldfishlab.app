"""Take the commander out of the 99 on every deck that has it in both places.

Until the 2026-09-25 review an import wrote the commander's row as a `DeckCard`
*and* set `Deck.commander` from it, so every imported deck that marked its
commander held the card twice: "101 cards", the commander shuffled into the
library, a collection check asking for two copies. Trap 46.

Idempotent: a deck whose commander is not among its entries is untouched, so
running this on a database that never had the bug (a fresh production one)
changes nothing. Not reversible, because nothing records which decks were
wrong - and putting a known bug back is not a rollback anybody wants.
"""

from django.db import migrations


def take_commander_out(apps, schema_editor):
    Deck = apps.get_model("decks", "Deck")
    DeckCard = apps.get_model("decks", "DeckCard")

    for deck in Deck.objects.exclude(commander=None).only("id", "commander_id"):
        entry = DeckCard.objects.filter(
            deck_id=deck.id, oracle_card_id=deck.commander_id
        ).first()
        if entry is None:
            continue
        if entry.quantity > 1:
            entry.quantity -= 1
            entry.save(update_fields=["quantity"])
        else:
            entry.delete()


class Migration(migrations.Migration):
    dependencies = [("decks", "0003_column_mapping")]

    operations = [
        migrations.RunPython(take_commander_out, migrations.RunPython.noop),
    ]
