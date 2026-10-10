"""The card catalogue, derived from Scryfall bulk data.

Two scopes, and the distinction is the whole reason imports work:
`OracleCard` is one row per distinct **card** - what a deck list names. The
per-printing table (`Printing`, from the 78.8 MB `default_cards` file) went in
phase 9 I: only the collection's prices used it, and production never loaded it.

`DerivedProfile` lives here rather than in `simulations/` on purpose: it is a
statement about a *card*, independent of any deck or run, and Phase 2's engine
adapter reads it without importing anything simulation-specific.
"""

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.translation import gettext_lazy as _

from cards.names import front_face, normalise


class BulkImport(models.Model):
    """One run of the bulk ingestion, successful or not.

    This table is what makes ingestion idempotent and safe to call on boot and
    from a nightly Celery Beat task: a run whose `scryfall_updated_at` already
    has a successful row is skipped without downloading anything.
    """

    class Kind(models.TextChoices):
        ORACLE_CARDS = "oracle_cards", "Oracle cards"
        ORACLE_TAGS = "oracle_tags", "Oracle tags"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        OK = "ok", "Completed"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped (unchanged)"

    kind = models.CharField(max_length=32, choices=Kind.choices)
    scryfall_updated_at = models.DateTimeField(
        help_text="The bulk file's own timestamp, not ours. The idempotency key."
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    rows_seen = models.PositiveIntegerField(default=0)
    rows_written = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)

    # Peak Python allocation during the run, when measured. The cloudlet this
    # deploys to has 128 MiB; an ingestion needing more than ~50 MB has stopped
    # streaming somewhere and is quietly buffering the whole file.
    peak_memory_kb = models.PositiveIntegerField(null=True, blank=True)
    message = models.TextField(blank=True)
    # Scryfall's "sets:printings" when the nightly job last loaded the cards
    # (phase 12 J18, `cards/refresh.py`). Unchanged means nothing new to fetch.
    sets_fingerprint = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["kind", "status", "scryfall_updated_at"])]

    def __str__(self) -> str:
        return f"{self.kind} @ {self.scryfall_updated_at:%Y-%m-%d %H:%M} ({self.status})"


class Tag(models.Model):
    """An Oracle tag from Scryfall's Tagger project.

    Tags form a DAG, not a tree: a tag can have several parents. Edges live in
    `TagEdge`.
    """

    id = models.UUIDField(primary_key=True)
    slug = models.SlugField(max_length=128, unique=True)
    label = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    aliases = ArrayField(models.CharField(max_length=128), default=list, blank=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self) -> str:
        return self.slug


class TagEdge(models.Model):
    """A parent -> child link in the tag DAG.

    An explicit model rather than a self-referential M2M: the rollup bulk-loads
    a few thousand edges in one statement and walks them in Python, and
    `through=` would buy nothing here but indirection.
    """

    parent = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="child_edges")
    child = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="parent_edges")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["parent", "child"], name="uniq_tag_edge"),
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("child")), name="tag_edge_not_self"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.parent_id} -> {self.child_id}"


class OracleCard(models.Model):
    """One distinct card, keyed by Scryfall's `oracle_id`."""

    oracle_id = models.UUIDField(primary_key=True)

    name = models.CharField(max_length=256, db_index=True)
    # Front face only, and the normalised form of it. Both are stored rather
    # than computed per query because they are rungs 4 and 5 of the import
    # resolution ladder, and a function call in a WHERE cannot use an index.
    front_name = models.CharField(max_length=256, db_index=True)
    search_name = models.CharField(max_length=256, db_index=True)

    mana_cost = models.CharField(max_length=128, blank=True)
    cmc = models.FloatField(default=0)
    type_line = models.CharField(max_length=256, blank=True)
    oracle_text = models.TextField(blank=True)

    colors = ArrayField(models.CharField(max_length=1), default=list, blank=True)
    color_identity = ArrayField(models.CharField(max_length=1), default=list, blank=True)
    # Colour only, never quantity. `Sol Ring` is ['C'], not 2 - see DerivedProfile.
    produced_mana = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    keywords = ArrayField(models.CharField(max_length=64), default=list, blank=True)

    layout = models.CharField(max_length=32, blank=True)
    # Text, not numeric: '*', '1+*' and 'inf' are all real printed values.
    power = models.CharField(max_length=8, blank=True)
    toughness = models.CharField(max_length=8, blank=True)
    loyalty = models.CharField(max_length=8, blank=True)

    legalities = models.JSONField(default=dict, blank=True)
    game_changer = models.BooleanField(default=False)
    reserved = models.BooleanField(default=False)
    edhrec_rank = models.PositiveIntegerField(null=True, blank=True)

    released_at = models.DateField(null=True, blank=True)
    # The id of the ONE printing the oracle_cards file happens to ship. Rung 1
    # of the import ladder matches an export's "Scryfall ID" against it, which
    # finds the card whenever the export names that printing; every other row
    # falls through to the name.
    scryfall_id = models.UUIDField(null=True, blank=True, db_index=True)
    scryfall_uri = models.URLField(max_length=512, blank=True)
    image_uri = models.URLField(max_length=512, blank=True)
    #: Scryfall's `art_crop`: the card's art alone (phase 10, the "Keep this
    #: deck" card). Taken from `image_uris` like `image_uri`, never built from
    #: it - Scryfall documents the links in `image_uris`, not a URL pattern.
    #: Empty until the next card ingest; the page then shows the flask.
    art_uri = models.URLField(max_length=512, blank=True)

    tags = models.ManyToManyField(Tag, through="OracleCardTag", related_name="cards")
    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["cmc"]),
            models.Index(fields=["game_changer"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_land(self) -> bool:
        return "Land" in self.type_line

    @property
    def is_commander_legal(self) -> bool:
        return self.legalities.get("commander") == "legal"

    @classmethod
    def from_scryfall(cls, data: dict) -> "OracleCard":
        """Build (not save) an instance from one bulk-data row.

        Double-faced cards keep their cost, text and stats on `card_faces`, and
        leave the top-level fields empty. Reading only the top level makes
        `Tergrid, God of Fright` look like a free 5-drop with no rules text -
        which it is not, and which no error would have reported.
        """
        name = data.get("name", "")
        faces = data.get("card_faces") or []
        front = faces[0] if faces else {}
        return cls(
            oracle_id=data["oracle_id"],
            name=name[:256],
            front_name=front_face(name)[:256],
            search_name=normalise(name)[:256],
            mana_cost=(data.get("mana_cost") or front.get("mana_cost") or "")[:128],
            cmc=data.get("cmc") or 0,
            type_line=(data.get("type_line") or front.get("type_line") or "")[:256],
            oracle_text=data.get("oracle_text") or cls._face_text(faces),
            colors=data.get("colors") or [],
            color_identity=data.get("color_identity") or [],
            produced_mana=data.get("produced_mana") or [],
            keywords=data.get("keywords") or [],
            layout=(data.get("layout") or "")[:32],
            power=str(data.get("power") or front.get("power") or "")[:8],
            toughness=str(data.get("toughness") or front.get("toughness") or "")[:8],
            loyalty=str(data.get("loyalty") or front.get("loyalty") or "")[:8],
            legalities=data.get("legalities") or {},
            game_changer=bool(data.get("game_changer")),
            reserved=bool(data.get("reserved")),
            edhrec_rank=data.get("edhrec_rank"),
            released_at=data.get("released_at") or None,
            scryfall_id=data.get("id") or None,
            scryfall_uri=(data.get("scryfall_uri") or "")[:512],
            image_uri=image_uri(data)[:512],
            art_uri=art_uri(data)[:512],
        )

    @staticmethod
    def _face_text(faces: list[dict]) -> str:
        """Both faces' rules text, separated the way Scryfall prints it."""
        return "\n//\n".join(face.get("oracle_text") or "" for face in faces)


def _images(data: dict) -> dict:
    """The image links of the card itself, or of its front face."""
    images = data.get("image_uris") or {}
    if not images:
        faces = data.get("card_faces") or []
        images = (faces[0].get("image_uris") if faces else {}) or {}
    return images


def image_uri(data: dict) -> str:
    """A representative image, from the card itself or its front face.
    """
    images = _images(data)
    return images.get("normal") or images.get("large") or ""


def art_uri(data: dict) -> str:
    """The art alone, from the card itself or its front face."""
    return _images(data).get("art_crop") or ""


class OracleCardTag(models.Model):
    """A card wearing a tag, either directly or by inheritance.

    `is_direct=False` means the tag was rolled up from a descendant: the card is
    tagged `tutor-creature-giant`, so it is also a `tutor`. Phase 4's provenance
    panel shows the difference, because "tagged as a tutor" and "tagged as
    something that is a kind of tutor" are not equally strong claims.
    """

    oracle_card = models.ForeignKey(OracleCard, on_delete=models.CASCADE, related_name="card_tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="card_tags")
    is_direct = models.BooleanField(default=True)
    # Scryfall's own confidence on a direct tagging ('median', 'high', ...).
    # Empty on rolled-up rows, where a weight would be a fabrication.
    weight = models.CharField(max_length=16, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["oracle_card", "tag"], name="uniq_card_tag")
        ]
        indexes = [models.Index(fields=["tag", "is_direct"])]

    def __str__(self) -> str:
        return f"{self.oracle_card_id} :: {self.tag_id}"


class DerivedProfile(models.Model):
    """What the simulator believes about a card, and where each belief came from.

    Three sources, in descending confidence, recorded per field in `source_map`
    so Phase 4 can render a provenance badge instead of a bare number:

    * `scryfall` - structured fields. Mana cost, type line, colour identity,
      `game_changer`. As close to ground truth as this application gets.
    * `tags` - the rolled-up Oracle tag DAG. Community-maintained, broad, and
      occasionally opinionated.
    * `regex` - a deliberately small set of patterns over oracle text. Always
      the weakest claim, always eligible for `needs_review`.

    The honest limit, and the likeliest source of quietly wrong numbers:
    Scryfall's `produced_mana` gives colour but never quantity or activation
    cost. `Sol Ring` is `['C']`, not 2. `Cabal Coffers` is `['B']` with no hint
    of its `{2}` cost or its per-Swamp scaling. Where the regexes cannot resolve
    an amount, `mana_amount` stays null and `needs_review` is set - never
    guessed. A simulator that silently assumes 1 is worse than one that admits
    it does not know.
    """

    class Kind(models.TextChoices):
        LAND = "land", _("Land")
        ROCK = "rock", _("Mana rock")
        RITUAL = "ritual", _("Ritual")
        CREATURE = "creature", _("Creature")
        ENCHANTMENT = "enchantment", _("Enchantment")
        ARTIFACT = "artifact", _("Artifact")
        SORCERY = "sorcery", _("Sorcery")
        INSTANT = "instant", _("Instant")
        PLANESWALKER = "planeswalker", _("Planeswalker")

    oracle_card = models.OneToOneField(
        OracleCard, on_delete=models.CASCADE, primary_key=True, related_name="profile"
    )

    # --- cost, from the mana cost string (source: scryfall) ---
    # Not a small integer: `Gleemax` has a mana value of 1,000,000. Un-set
    # cards are legal nowhere, but they are in the bulk file, and an ingestion
    # that dies on one row is worse than a column four bytes wider.
    mv = models.PositiveIntegerField(default=0)
    # Coloured pips per colour: {"B": 2}. A dict rather than one integer
    # because the engine's mono-black assumption is a property of one deck,
    # not of the card database.
    pips = models.JSONField(default=dict, blank=True)
    # Wide for the same reason as `mv`: Gleemax costs literally {1000000}.
    generic = models.PositiveIntegerField(default=0)
    colorless = models.PositiveSmallIntegerField(
        default=0, help_text="{C} pips, which generic mana cannot pay."
    )
    has_x = models.BooleanField(default=False)

    kind = models.CharField(max_length=16, choices=Kind.choices)
    is_basic_swamp = models.BooleanField(default=False)
    role_tags = ArrayField(models.CharField(max_length=32), default=list, blank=True)

    # --- behaviour (source: regex, mostly) ---
    enters_tapped = models.BooleanField(default=False)
    produces_mana = models.BooleanField(default=False)
    mana_colors = ArrayField(models.CharField(max_length=2), default=list, blank=True)
    mana_amount = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Null means unresolved, never 'none'."
    )
    #: Exactly what one use of the mana ability adds, when the text names every
    #: symbol: a Signet's `{U}{B}` is `{"U": 1, "B": 1}` - both colours, not a
    #: choice between them. Null when it IS a choice ("{U} or {B}", "one mana
    #: of any color"), which the deck's colours settle in the adapter.
    mana_produces = models.JSONField(null=True, blank=True)
    #: Generic mana the ability costs beside `{T}`: 1 for a Signet. Null means
    #: none. Until the 2026-09-25 review this was not read at all, so every
    #: Signet tapped for two at no cost (trap 49).
    mana_activation = models.PositiveSmallIntegerField(null=True, blank=True)
    #: False for "This artifact doesn't untap during your untap step" - Mana
    #: Vault and the Monoliths make their mana once, not every turn.
    mana_untaps = models.BooleanField(default=True)
    #: P19 R4: "Activate only if you control ..." on the ability above -
    #: {"if": {"kind": control_type | lands | artifacts, ...}, "otherwise":
    #: {"amount", "produces", "activation"} | null}. `otherwise` is what the
    #: card taps for while the condition does not hold: a Tainted land's {C},
    #: nothing for Temple of the False God. See `cards.profiles._mana_production`.
    mana_condition = models.JSONField(null=True, blank=True)
    #: P19 R4: a mana rule the engine already plays, read off the text -
    #: {"rule": type_adding | double_subtype | per_controlled, "subtype",
    #: "activation", "color"}. Urborg, Crypt Ghast, Cabal Coffers. Until R4
    #: only a built-in annotation could set one. See `cards.profiles._mana_rule`.
    mana_rule = models.JSONField(null=True, blank=True)
    #: P19 R6: a filter or converter beside the plain ability - {"pays_with":
    #: "WB" for {W/B} or "" for {1}, "amount", "offers": the colours of a
    #: choice ("" = any), "produces": exact mana or null}. Fetid Heath, Study
    #: Hall. See `cards.profiles._mana_production`.
    mana_filter = models.JSONField(null=True, blank=True)
    #: P19 R17: "Spend this mana only to cast ..." on the ability above -
    #: {"spells": [{"types", "subtypes", "legendary", "colorless",
    #: "multicolored", "noncreature", "chosen_type"}, ...] (any one of them),
    #: "otherwise": {"amount", "produces", "activation"} | null}. Cavern of
    #: Souls; `otherwise` is its {C}. See `cards.profiles._spend_only`.
    mana_spend_only = models.JSONField(null=True, blank=True)
    #: P19 R7: Treasure tokens it makes as it resolves (a spell's sentence, a
    #: permanent's "When this creature enters"). See `cards.profiles._treasures`.
    treasures = models.PositiveSmallIntegerField(default=0)
    #: P19 R14: Lander tokens it makes the same way. See `cards.profiles._landers`.
    landers = models.PositiveSmallIntegerField(default=0)
    #: P19 R7: "As an additional cost to cast this spell, discard a card."
    discard_cost = models.PositiveSmallIntegerField(default=0)
    #: P19 R15: every other additional cost of the card's own, as the ways it
    #: can be paid: [{"sacrifice": ["creature"], "filter": "", "life": 0,
    #: "life_x": false, "discard": 0, "mana": "", "exile_from_graveyard": ""}].
    #: Null: none, or none read. See `cards.profiles._additional_cost`.
    additional_cost = models.JSONField(null=True, blank=True, default=None)
    #: P19 R15: a mana ability whose cost sacrifices another permanent -
    #: Ashnod's Altar, Phyrexian Tower: {"sacrifice": ["creature"], "filter": "",
    #: "amount": 2, "produces": {"C": 2} or null for a choice, "taps": false}.
    sacrifice_mana = models.JSONField(null=True, blank=True, default=None)
    #: P19 R15: "Exile this card from your hand: Add {G}" - played as a ritual
    #: that costs nothing and goes to exile (Elvish Spirit Guide).
    mana_from_hand = models.BooleanField(default=False)
    #: P19 R16: the triggers that make Treasure, mana or cards - Lotus Cobra,
    #: Storm-Kiln Artist, Smothering Tithe: a list of {"event", "filter",
    #: "treasures", "spawn", "mana", "color", "draw", "life", "tax"}.
    triggers = models.JSONField(default=list, blank=True)
    cost_reduction = models.PositiveSmallIntegerField(null=True, blank=True)
    draws_cards = models.PositiveSmallIntegerField(null=True, blank=True)
    #: P19 R8: cards discarded right after that draw - "Draw two cards, then
    #: discard two cards" (Faithless Looting).
    discards_after = models.PositiveSmallIntegerField(default=0)
    #: P19 R8: cards put back on top of the library after that draw -
    #: Brainstorm draws three, then puts two back.
    puts_back = models.PositiveSmallIntegerField(default=0)
    #: P19 R9: "Draw X cards" - Stroke of Genius, Blue Sun's Zenith.
    draws_x = models.BooleanField(default=False)
    #: P19 R8: the spree mode the draw sits in, paid on top of the printed
    #: cost: "{B}{B}" on Insatiable Avarice. Empty: none.
    extra_cost = models.CharField(max_length=32, blank=True, default="")

    # --- tutors: destination from the tags, amount from the printed text ---
    #
    # Split deliberately. The community DAG knows which zone a tutor searches
    # to - `tutor-to-hand` covers 633 cards, `tutor-to-graveyard` 35 - and
    # never how many cards it finds. So the tag establishes the zone and the
    # regex establishes the number, and where the regex cannot, `tutor_count`
    # stays null and the card is a gap. Guessing 1 here would have been the
    # single most tempting wrong answer in the whole module: it is right often
    # enough to look fine and silently wrong on every Buried Alive.
    #
    # `battlefield` is stored even though the engine has no such move, because
    # the honest report is "this tutors onto the battlefield and we cannot do
    # that", not a tutor quietly redirected to the graveyard.
    tutor_to = models.CharField(
        max_length=16, blank=True,
        help_text="hand, graveyard, battlefield or top. Blank means not a tutor.",
    )
    tutor_count = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Null means unresolved, never 'one'."
    )
    tutor_kind = models.CharField(
        max_length=16, blank=True,
        help_text="Engine card kind the search is restricted to. Blank means any.",
    )
    #: P19 R12: for `tutor_to` "top" (Vampiric Tutor), {"types": [...]}: the card
    #: types it may find, any of them; empty for any card.
    #: P19 R10: what else a search onto the battlefield is limited to - {"color":
    #: "G" for "a green creature card", "max_mv": "X" or a number for "with mana
    #: value X or less"}. Green Sun's Zenith, Chord of Calling. Null: nothing.
    tutor_filter = models.JSONField(null=True, blank=True)
    #: P19 R2: a search for lands that puts them onto the battlefield - a ramp
    #: spell, a fetch land, Wood Elves - read whole off the text, or null.
    #: Keys: battlefield, hand (how many go where), tapped, basic, types (land
    #: subtypes it may find; empty = any), life, when (cast | enters | play),
    #: sacrifice (the card itself goes), untap_at (Fabled Passage: untapped
    #: once you control that many lands). See `cards.profiles._land_search`.
    land_search = models.JSONField(null=True, blank=True)
    #: P19 R3: when `enters_tapped`, the condition under which it does not -
    #: {"kind": control_type | lands | opponents | reveal | pay_life | permanent |
    #: turn | opponent_lands | life_at_most, ...} (the last four since P19 R12).
    #: See `cards.profiles._tapped_unless`. Null: none, or none it could read.
    tapped_unless = models.JSONField(null=True, blank=True)

    #: Necropotence. A static "Skip your draw step." on the card itself - not
    #: Fatigue, which makes somebody *else* skip one, and not Ivory Gargoyle,
    #: which skips exactly one. The distinction is why this is read off the
    #: printed sentence rather than off the `skip-draw-step` tag, which files
    #: all three together.
    skips_draw_step = models.BooleanField(default=False)
    # The pronoun check: a cost you pay, versus a payoff you inflict.
    self_life_loss = models.PositiveSmallIntegerField(null=True, blank=True)
    opponent_life_loss = models.PositiveSmallIntegerField(null=True, blank=True)

    # --- honesty ---
    needs_review = models.BooleanField(default=False, db_index=True)
    review_reasons = ArrayField(models.CharField(max_length=120), default=list, blank=True)
    source_map = models.JSONField(default=dict, blank=True)

    derived_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["kind"])]

    def __str__(self) -> str:
        return f"profile of {self.oracle_card_id}"

    @property
    def black_pips(self) -> int:
        """The mono-black shorthand, for `Card.pips`.

        The engine handles all five colours now; this stays because `Card` keeps
        a single-integer `pips` alongside its full `cost`, and the reference
        deck is described that way.
        """
        return int(self.pips.get("B", 0))
