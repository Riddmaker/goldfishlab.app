"""A deck from photos (B-14, PR 2): upload, check, save.

What these tests are for, in order of how much they matter:

1. **Nothing is spent for nothing, and nothing is kept**: the quota is
   checked before the photos are touched and given back when no card was
   read; what waits for the check is card names, never a picture; uploads
   stay in memory; CSRF still holds although it is checked by hand.
2. **The person decides**: every row can be kept, changed or left out, a
   misread gets its neighbours, a missed card is found by search, and only
   "Save the deck" makes a deck - through the ordinary import, so a name kept
   as read lands on the review screen rather than vanishing.
3. **Only the owner** sees or saves a reading.
"""

import io
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from billing import quotas
from billing.models import UsageRecord
from billing.views import refusal
from cards.models import OracleCard
from decks import photos, resolve
from decks.models import Deck, PendingImport, UnresolvedRow
from simulations import mistral

pytestmark = pytest.mark.django_db
PASSWORD = "pw-for-test-only"
METRIC = UsageRecord.Metric.PHOTO_IMPORTS


@pytest.fixture
def owner(catalogue):
    """An account that has agreed to send photos; `test_consent_*` start without."""
    return get_user_model().objects.create_user(
        email="photos@example.com", password=PASSWORD, photos_consent_since=timezone.now())


@pytest.fixture
def signed_in(client, owner, settings):
    settings.MISTRAL_API_KEY = "test-only"
    client.force_login(owner)
    return client


def _photo(name="cards.jpg", size=(800, 600)) -> SimpleUploadedFile:
    out = io.BytesIO()
    Image.new("RGB", size, (40, 90, 40)).save(out, "JPEG")
    return SimpleUploadedFile(name, out.getvalue(), content_type="image/jpeg")


def _card(name):
    return OracleCard.objects.create(
        oracle_id=uuid.uuid4(), name=name, front_name=name, search_name=name.lower(),
        type_line="Instant", oracle_text="", color_identity=[], keywords=[],
        legalities={"commander": "legal"}, mana_cost="", cmc=0)


def _reading(*seen) -> photos.Reading:
    """What `photos.read` would give for these `(name, printed, count)`."""
    return photos.Reading(report=photos._resolve([photos.Seen(*item) for item in seen]),
                          photos=2, failed=[])


@pytest.fixture
def reads(monkeypatch):
    """`photos.read` answering with what the test sets, or raising."""
    answer = {}

    def read(jpegs):
        answer["photos"] = len(jpegs)
        if isinstance(answer.get("give"), Exception):
            raise answer["give"]
        return answer["give"]
    monkeypatch.setattr(photos, "read", read)
    return answer


# --- 1. the upload ------------------------------------------------------------


def test_the_page_is_there_only_with_a_mistral_key(client, owner, settings):
    client.force_login(owner)
    settings.MISTRAL_API_KEY = ""
    assert client.get(reverse("decks:photos")).status_code == 404

    settings.MISTRAL_API_KEY = "test-only"
    body = client.get(reverse("decks:photos")).content.decode()
    assert "data-photo-form" in body and "js/photo-upload.js" in body
    assert "never stored" in body


def test_photos_read_become_a_list_to_check_and_count_once(signed_in, owner, reads):
    reads["give"] = _reading(("Sol Ring", "Sol Ring", 1), ("Gravetrawler", "", 1))

    response = signed_in.post(reverse("decks:photos"), {"photos": [_photo(), _photo("b.png")]})

    pending = PendingImport.objects.get(owner=owner, kind=PendingImport.Kind.PHOTOS)
    assert response.status_code == 302 and response.url == pending.get_absolute_url()
    assert reads["photos"] == 2
    assert quotas.used(owner, METRIC) == 1
    assert "Sol Ring" in pending.text and "base64" not in pending.text
    assert "Gravecrawler" in pending.text, "the misread gets its card as a suggestion"


@pytest.mark.parametrize(("give", "status"), [
    (mistral.MistralError("down"), 503),
    ("nothing", 422),
])
def test_a_run_that_read_no_card_is_not_counted(signed_in, owner, reads, give, status):
    reads["give"] = _reading() if give == "nothing" else give

    response = signed_in.post(reverse("decks:photos"), {"photos": [_photo()]})

    assert response.status_code == status and "data-photos-error" in response.content.decode()
    assert quotas.used(owner, METRIC) == 0
    assert not PendingImport.objects.filter(owner=owner).exists()


def test_a_file_that_is_not_a_photo_is_named_and_nothing_is_read(signed_in, owner, reads):
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, "GIF")

    response = signed_in.post(reverse("decks:photos"), {"photos": [
        _photo(), SimpleUploadedFile("x.gif", gif.getvalue(), content_type="image/gif")]})

    assert response.status_code == 400 and "Photo 2" in response.content.decode()
    assert "photos" not in reads and quotas.used(owner, METRIC) == 0


def test_more_photos_than_allowed_are_refused(signed_in, reads):
    many = [_photo(f"{i}.jpg", (20, 20)) for i in range(photos.MAX_PHOTOS + 1)]

    response = signed_in.post(reverse("decks:photos"), {"photos": many})

    assert response.status_code == 400 and "photos" not in reads


def test_the_month_spent_is_refused_before_the_photos_are_read(signed_in, owner, reads):
    quotas.consume(owner, METRIC, 5)

    response = signed_in.post(reverse("decks:photos"), {"photos": [_photo()]})

    assert response.status_code == 402 and "photos" not in reads
    page = signed_in.get(reverse("decks:photos")).content.decode()
    assert "data-photos-blocked" in page and "data-photo-form" not in page


def test_three_runs_a_day(signed_in, reads, pinned_window):
    reads["give"] = _reading()  # the model answered, nothing found: refunded, but a run
    url = reverse("decks:photos")

    codes = [signed_in.post(url, {"photos": [_photo()]}).status_code for _ in range(4)]

    assert codes == [422, 422, 422, 429]
    assert reads["photos"] == 1


def test_a_model_that_is_down_costs_no_run_of_the_day(signed_in, reads, pinned_window):
    reads["give"] = mistral.MistralError("down")
    url = reverse("decks:photos")

    codes = [signed_in.post(url, {"photos": [_photo()]}).status_code for _ in range(4)]

    assert codes == [503, 503, 503, 503]


def test_a_refused_photo_costs_no_run_of_the_day(signed_in, reads, pinned_window):
    url = reverse("decks:photos")
    bad = SimpleUploadedFile("x.jpg", b"not a picture", content_type="image/jpeg")

    codes = [signed_in.post(url, {"photos": [bad]}).status_code for _ in range(4)]

    assert codes == [400] * 4 and "photos" not in reads


def test_more_than_the_ceiling_is_said_not_crashed(signed_in, monkeypatch, reads):
    monkeypatch.setattr(photos, "MAX_TOTAL_BYTES", 2_000)
    big = [_photo(f"{n}.jpg", size=(400, 400)) for n in range(3)]

    response = signed_in.post(reverse("decks:photos"), {"photos": big, "consent": "yes"})

    assert response.status_code == 413 and "photos" not in reads
    assert "30 MB" in response.content.decode()


def test_a_declared_length_past_the_ceiling_is_said_before_reading(signed_in, monkeypatch,
                                                                    reads):
    monkeypatch.setattr(photos, "too_large", lambda request: True)

    response = signed_in.post(reverse("decks:photos"), {"photos": [_photo()]})

    assert response.status_code == 413 and "photos" not in reads


def test_no_run_is_spent_on_a_deck_that_could_not_be_kept(signed_in, owner, reads,
                                                           monkeypatch):
    real = quotas.check

    def check(user, metric, *args, **kwargs):
        if metric == quotas.DECKS_OWNED:
            metric, kwargs["amount"] = UsageRecord.Metric.IMPORTS, 10_000
        return real(user, metric, *args, **kwargs)
    monkeypatch.setattr(quotas, "check", check)

    response = signed_in.post(reverse("decks:photos"), {"photos": [_photo()]})

    assert response.status_code == 402 and "photos" not in reads
    assert quotas.check(owner, METRIC, raise_on_fail=False).used == 0


def test_a_failure_after_the_reading_gives_the_run_back(signed_in, owner, reads, monkeypatch):
    reads["give"] = _reading(("Blood Artist", "Blood Artist", 1))

    def broken(*args):
        raise RuntimeError("database gone")
    monkeypatch.setattr(photos, "hold", broken)

    with pytest.raises(RuntimeError):
        signed_in.post(reverse("decks:photos"), {"photos": [_photo()]})
    assert quotas.check(owner, METRIC, raise_on_fail=False).used == 0


def test_a_photo_reading_is_not_a_file_to_map(signed_in, owner):
    pending = PendingImport.objects.create(owner=owner, kind=PendingImport.Kind.PHOTOS,
                                           text="{}", parser="photos")

    response = signed_in.get(reverse("decks:map", args=[pending.pk]))

    assert response.status_code == 404


def test_the_upload_is_held_in_memory_however_large(signed_in, settings, monkeypatch, reads):
    reads["give"] = _reading()
    kept = []

    def prepare(upload):
        kept.append(upload)
        return b"jpeg"
    monkeypatch.setattr(photos, "prepare", prepare)
    big = SimpleUploadedFile("big.jpg", b"x" * (settings.FILE_UPLOAD_MAX_MEMORY_SIZE + 10),
                             content_type="image/jpeg")

    signed_in.post(reverse("decks:photos"), {"photos": [big]})

    assert kept and not hasattr(kept[0], "temporary_file_path")


def test_csrf_still_holds(owner, settings, reads):
    from django.test import Client

    settings.MISTRAL_API_KEY = "test-only"
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)

    response = client.post(reverse("decks:photos"), {"photos": [_photo()]})

    assert response.status_code == 403 and "photos" not in reads


def test_the_import_page_leads_to_photos(signed_in):
    assert "data-photos-link" in signed_in.get(reverse("decks:import")).content.decode()


# --- 2. the check --------------------------------------------------------------


@pytest.fixture
def held(owner):
    _card("Lightning Strike")
    reading = _reading(("Sol Ring", "Sol Ring", 1),
                       ("Lightning Strike", "Blitzschlag", 1),  # read as the wrong card
                       ("Swamp", "Sumpf", 4),
                       ("Gravetrawler", "", 1),
                       ("Totally Made Up", "", 1))
    return photos.hold(owner, reading)


def _rows(pending):
    return {(row.card.name if row.card else row.name): row
            for row in photos.load(pending).rows}


def test_what_was_not_found_comes_first_with_its_suggestions(held):
    rows = photos.load(held).rows

    assert [row.name for row in rows[:2]] == ["Gravetrawler", "Totally Made Up"]
    assert rows[0].suggestions[0].name == "Gravecrawler"
    assert rows[0].chosen == photos.KEEP_AS_READ
    assert {row.card.name for row in rows[2:]} == {"Sol Ring", "Lightning Strike", "Swamp"}


def test_the_check_page_shows_every_row(signed_in, held):
    body = signed_in.get(held.get_absolute_url()).content.decode()

    for name in ("Gravetrawler", "Gravecrawler", "Totally Made Up", "Sol Ring", "Swamp"):
        assert name in body
    assert body.count("data-wrong") == 3


def test_not_this_card_offers_its_neighbours(signed_in, held):
    strike = _rows(held)["Lightning Strike"]

    body = signed_in.post(held.get_absolute_url(), {
        "action": f"wrong-{strike.index}",
        f"card-{strike.index}": str(strike.card.pk)}).content.decode()

    assert "data-neighbours" in body and "Lightning Bolt" in body


def test_saving_makes_the_deck_from_what_was_chosen(signed_in, owner, held):
    rows = _rows(held)
    bolt = OracleCard.objects.get(name="Lightning Bolt")
    counterspell = OracleCard.objects.get(name="Counterspell")
    post = {"action": "save", "name": "From the table", "add": [str(counterspell.pk)],
            "open": [str(rows["Lightning Strike"].index)]}
    post[f"card-{rows['Sol Ring'].index}"] = str(rows["Sol Ring"].card.pk)
    post[f"card-{rows['Lightning Strike'].index}"] = str(bolt.pk)
    post[f"card-{rows['Swamp'].index}"] = str(rows["Swamp"].card.pk)
    post[f"qty-{rows['Swamp'].index}"] = "6"
    post[f"card-{rows['Gravetrawler'].index}"] = str(rows["Gravetrawler"].suggestions[0].pk)
    post[f"card-{rows['Totally Made Up'].index}"] = photos.KEEP_AS_READ

    response = signed_in.post(held.get_absolute_url(), post)

    deck = Deck.objects.get(owner=owner)
    assert deck.name == "From the table"
    cards = {entry.oracle_card.name: entry.quantity for entry in deck.entries.all()}
    assert cards == {"Sol Ring": 1, "Lightning Bolt": 1, "Swamp": 6, "Gravecrawler": 1,
                     "Counterspell": 1}
    assert UnresolvedRow.objects.filter(raw_name="Totally Made Up").exists()
    assert response.status_code == 302 and "/imports/" in response.url
    assert not PendingImport.objects.filter(pk=held.pk).exists()


def test_a_row_unticked_or_tampered_with_is_left_out(signed_in, owner, held):
    rows = _rows(held)
    post = {"action": "save",
            f"card-{rows['Sol Ring'].index}": str(rows["Sol Ring"].card.pk),
            f"card-{rows['Swamp'].index}": "'; drop table",
            f"card-{rows['Gravetrawler'].index}": "",
            f"card-{rows['Totally Made Up'].index}": ""}

    signed_in.post(held.get_absolute_url(), post)

    deck = Deck.objects.get(owner=owner)
    assert [entry.oracle_card.name for entry in deck.entries.all()] == ["Sol Ring"]


def test_nothing_kept_saves_nothing(signed_in, owner, held):
    response = signed_in.post(held.get_absolute_url(), {"action": "save"})

    assert response.status_code == 400
    assert not Deck.objects.filter(owner=owner).exists()
    assert PendingImport.objects.filter(pk=held.pk).exists()


def test_a_missing_card_is_found_and_stays_ticked(signed_in, held):
    counterspell = OracleCard.objects.get(name="Counterspell")

    found = signed_in.post(held.get_absolute_url(), {"action": "search", "q": "counters"})
    kept = signed_in.post(held.get_absolute_url(), {"action": "search", "q": "sol",
                                                    "add": [str(counterspell.pk)]})

    assert "data-found" in found.content.decode() and "Counterspell" in found.content.decode()
    body = kept.content.decode()
    assert f'name="add" value="{counterspell.pk}" checked' in body


def test_a_stranger_finds_no_reading(client, held):
    stranger = get_user_model().objects.create_user(email="s@example.com", password=PASSWORD)
    client.force_login(stranger)

    assert client.get(held.get_absolute_url()).status_code == 404
    assert client.post(held.get_absolute_url(), {"action": "save"}).status_code == 404


def test_a_deck_upload_waiting_is_not_a_reading(signed_in, owner):
    pending = PendingImport.objects.create(owner=owner, kind=PendingImport.Kind.DECK,
                                           text="a,b\n")

    assert signed_in.get(reverse("decks:photos-check", args=[pending.pk])).status_code == 404


# --- the pieces ------------------------------------------------------------------


def test_a_misread_in_the_middle_is_suggested(catalogue):
    assert resolve.suggest("Gravetrawler")[0] == "Gravecrawler"


def test_a_misread_at_the_start_is_suggested(catalogue):
    assert "Blood Artist" in resolve.suggest("Bload Artist")


def test_the_list_text_keeps_quantities_and_names_as_read(catalogue):
    sol = OracleCard.objects.get(name="Sol Ring")
    rows = [photos.Row(index=0, name="Sol Ring", printed="", card=sol, suggestions=[],
                       chosen=str(sol.pk)),
            photos.Row(index=1, name="Mystery", printed="", card=None, suggestions=[],
                       chosen=photos.KEEP_AS_READ, quantity=2),
            photos.Row(index=2, name="Gone", printed="", card=None, suggestions=[], chosen="")]

    assert photos.deck_text(rows, [OracleCard.objects.get(name="Counterspell")]) == (
        "1 Sol Ring\n2 Mystery\n1 Counterspell")


def test_the_refusal_says_what_ran_out():
    exc = quotas.QuotaExceeded("", metric=METRIC, limit=5, used=5)
    guest = quotas.QuotaExceeded("", metric=METRIC, limit=0, used=0)

    assert refusal(exc) == "You have read 5 of 5 decks from photos this month."
    assert refusal(guest) == "Reading a deck from photos needs an account."


# --- PR 3: consent --------------------------------------------------------------


@pytest.fixture
def unasked(owner):
    owner.photos_consent_since = None
    owner.save(update_fields=["photos_consent_since"])
    return owner


def test_consent_is_asked_until_given(signed_in, unasked):
    page = signed_in.get(reverse("decks:photos")).content.decode()
    assert "data-photos-consent" in page
    assert 'name="consent"' in page and "required" in page


def test_consent_missing_sends_nothing_and_counts_nothing(signed_in, unasked, reads):
    reads["give"] = _reading(("Sol Ring", "", 1))
    response = signed_in.post(reverse("decks:photos"), {"photos": [_photo()]})
    assert response.status_code == 400
    assert "Tick the box first" in response.content.decode()
    assert "photos" not in reads  # the model was never asked
    assert quotas.used(unasked, METRIC) == 0
    unasked.refresh_from_db()
    assert unasked.photos_consent_since is None


def test_consent_given_is_kept_and_not_asked_again(signed_in, unasked, reads):
    reads["give"] = _reading(("Sol Ring", "", 1))
    response = signed_in.post(reverse("decks:photos"), {"photos": [_photo()], "consent": "yes"})
    assert response.status_code == 302
    unasked.refresh_from_db()
    assert unasked.photos_consent_since is not None
    assert "data-photos-consent" not in signed_in.get(reverse("decks:photos")).content.decode()


def test_consent_is_not_kept_for_a_photo_refused(signed_in, unasked, reads):
    bad = SimpleUploadedFile("notes.jpg", b"not a picture", content_type="image/jpeg")
    response = signed_in.post(reverse("decks:photos"), {"photos": [bad], "consent": "yes"})
    assert response.status_code == 400
    unasked.refresh_from_db()
    assert unasked.photos_consent_since is None


def test_consent_is_in_the_data_export(signed_in, owner):
    from accounts.privacy import export
    assert export(owner)["account"]["photos_consent_since"] == owner.photos_consent_since


def test_privacy_policy_names_photos_and_the_card_ai(client):
    page = client.get(reverse("privacy")).content.decode()
    assert 'id="mistral"' in page
    assert "Ask the AI" in page and "read a deck from photos" in page
    assert "Photos of your cards" in page
