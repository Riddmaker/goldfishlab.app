"""A deck from photos (B-14, PR 1): reading the titles.

What these tests are for, in order of how much they matter:

1. **A photo leaves nothing behind**: what is sent is a new, small JPEG
   without EXIF; uploads stay in memory and stop at a ceiling; nothing about a
   photo's content is logged.
2. **The model is never believed**: every name goes through the import's own
   ladder, a printed title is the second try, and what is not a card stays an
   open row.
3. **A deck counts as a Commander deck does**: one of each card, basics as
   many as were seen, across all photos.
"""

import io
import json
import logging

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import StopFutureHandlers, StopUpload
from PIL import Image

from billing import quotas
from billing.models import Plan, UsageRecord
from decks import photos
from simulations import mistral

# --- 1. what is sent, and what is kept ---------------------------------------


def _jpeg(size=(3200, 2400), color=(120, 30, 30), exif=None) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG", **({"exif": exif} if exif else {}))
    return out.getvalue()


def _upload(data: bytes, name="card.jpg") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type="image/jpeg")


def _exif_with_gps_turned() -> bytes:
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: turned 90 degrees, as a phone held upright
    exif[0x010F] = "PhoneMaker"
    exif.get_ifd(0x8825)[2] = (47.0, 22.0, 0.0)  # GPS latitude
    return exif.tobytes()


def test_a_photo_is_redrawn_small_upright_and_without_exif():
    sent = photos.prepare(_upload(_jpeg(exif=_exif_with_gps_turned())))

    with Image.open(io.BytesIO(sent)) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= photos.EDGE
        assert image.size[1] > image.size[0], "turned upright, as the phone meant it"
        assert not image.getexif()
    assert b"PhoneMaker" not in sent


@pytest.mark.parametrize(("data", "name"), [
    (b"GIF89a" + b"\x00" * 64, "card.gif"),
    (b"not a picture at all", "card.jpg"),
    (b"%PDF-1.7\n", "card.pdf"),
])
def test_what_is_not_a_photo_is_refused_by_its_content(data, name):
    if name == "card.gif":
        out = io.BytesIO()
        Image.new("RGB", (10, 10)).save(out, "GIF")
        data = out.getvalue()

    with pytest.raises(photos.PhotoRefused):
        photos.prepare(_upload(data, name))


def test_a_decompression_bomb_is_refused_before_it_is_decoded():
    out = io.BytesIO()
    Image.new("1", (10_000, 7_000)).save(out, "PNG")  # 70 million pixels, a few kB

    with pytest.raises(photos.PhotoRefused, match="too many pixels"):
        photos.prepare(_upload(out.getvalue(), "bomb.png"))


def test_a_photo_too_large_is_refused_before_it_is_opened():
    upload = _upload(b"x")
    upload.size = photos.MAX_PHOTO_BYTES + 1

    with pytest.raises(photos.PhotoRefused, match="10 MB"):
        photos.prepare(upload)


def test_uploads_stay_in_memory_past_djangos_spool_size(settings):
    handler = photos.MemoryOnlyUploadHandler()
    chunk = b"x" * (settings.FILE_UPLOAD_MAX_MEMORY_SIZE + 1)
    handler.handle_raw_input(None, {}, len(chunk) + 500, b"--b")
    with pytest.raises(StopFutureHandlers):  # it takes the file; no temp-file handler after it
        handler.new_file("photo", "card.jpg", "image/jpeg", len(chunk))
    handler.receive_data_chunk(chunk, 0)

    kept = handler.file_complete(len(chunk))

    assert kept is not None and not hasattr(kept, "temporary_file_path")


def test_uploads_stop_at_the_ceiling_declared_or_not():
    handler = photos.MemoryOnlyUploadHandler()
    with pytest.raises(StopUpload):
        handler.handle_raw_input(None, {}, photos.MAX_TOTAL_BYTES * 2, b"--b")

    handler.handle_raw_input(None, {}, None, b"--b")  # no length given
    with pytest.raises(StopFutureHandlers):
        handler.new_file("photo", "card.jpg", "image/jpeg", None)
    handler.receive_data_chunk(b"x" * photos.MAX_TOTAL_BYTES, 0)
    with pytest.raises(StopUpload):
        handler.receive_data_chunk(b"x", photos.MAX_TOTAL_BYTES)


def test_the_model_gets_the_instruction_and_the_picture_only():
    (message,) = photos.messages(b"\xff\xd8jpeg")

    text, picture = message["content"]
    assert text == {"type": "text", "text": photos.PROMPT}
    assert picture == {"type": "image_url", "image_url": "data:image/jpeg;base64,/9hqcGVn"}


def test_one_photo_is_one_quick_call_on_the_photo_model(settings, monkeypatch):
    settings.MISTRAL_PHOTO_MODEL = "a-vision-model"
    seen = {}

    def complete(messages, **kwargs):
        seen.update(kwargs)
        return mistral.Completion('{"cards": []}', "a-vision-model", 1, 1)
    monkeypatch.setattr(mistral, "complete", complete)

    assert photos.ask(b"jpeg") == []
    assert seen["model_name"] == "a-vision-model"
    assert seen["retry"] is False and seen["timeout"] == mistral.QUICK_TIMEOUT_SECONDS
    assert seen["temperature"] == 0


# --- 2. what the model may say ------------------------------------------------


@pytest.mark.parametrize("content", ["", "nope", "[]", '{"cards": "x"}',
                                     '{"cards": [1, {"name": 2}, {"name": "  "}]}'])
def test_an_answer_not_shaped_as_asked_gives_nothing(content):
    assert photos.parse(content) == []


def test_an_answer_is_capped_and_counted_sanely():
    many = json.dumps({"cards": [{"name": "y" * 500, "count": 10_000}] * 500
                       + [{"name": "Sol Ring", "count": "two"}]})

    seen = photos.parse(many)

    assert len(seen) == photos.CARDS_MAX
    assert {len(item.name) for item in seen} == {photos.NAME_MAX}
    assert {item.count for item in seen} == {photos.COUNT_MAX}
    assert photos.parse('{"cards": [{"name": "Sol Ring", "count": -3}]}')[0].count == 1


@pytest.fixture
def answers(monkeypatch):
    """The model's reading per photo, by the photo's bytes; None fails it."""
    given: dict[bytes, list | None] = {}

    def ask(jpeg):
        if given[jpeg] is None:
            raise mistral.MistralError("down")
        return [photos.Seen(*item) for item in given[jpeg]]
    monkeypatch.setattr(photos, "ask", ask)
    return given


@pytest.mark.django_db
def test_photos_become_one_list_through_the_import_ladder(catalogue, answers):
    answers[b"fan"] = [("Blood Artist", "Blood Artist", 2),  # a fan, counted twice
                       ("Swamp", "Swamp", 3)]
    answers[b"pile"] = [("Swamp", "Sumpf", 2),
                        ("Lightnin Strike", "Lightning Bolt", 1),  # wrong English, right print
                        ("Totally Made Up", "", 1)]

    reading = photos.read([b"fan", b"pile"])

    found = {r.card.name: r.row.quantity for r in reading.report.resolved}
    assert found == {"Blood Artist": 1, "Swamp": 5, "Lightning Bolt": 1}
    (open_row,) = reading.report.unresolved
    assert open_row.row.name == "Totally Made Up"
    assert reading.photos == 2 and reading.failed == []


@pytest.mark.django_db
def test_a_photo_not_read_is_named_and_the_others_still_count(catalogue, answers, caplog):
    answers[b"one"] = [("Sol Ring", "Sol Ring", 1)]
    answers[b"two"] = None

    with caplog.at_level(logging.WARNING, logger="decks.photos"):
        reading = photos.read([b"one", b"two"])

    assert [r.card.name for r in reading.report.resolved] == ["Sol Ring"]
    assert reading.failed == [2]
    assert "photo 2 of 2" in caplog.text


def test_no_photo_read_is_an_error_to_refund(answers):
    answers[b"one"] = None

    with pytest.raises(mistral.MistralError):
        photos.read([b"one"])


@pytest.mark.django_db
def test_nothing_of_a_photo_reaches_the_log(catalogue, monkeypatch, caplog, settings):
    """Through the real client: its error names the status, not the request."""
    settings.MISTRAL_API_KEY = "test-only"

    def urlopen(request, timeout):
        raise mistral.urllib.error.HTTPError(request.full_url, 500, "x", {}, None)
    monkeypatch.setattr(mistral.urllib.request, "urlopen", urlopen)
    jpeg = photos.prepare(_upload(_jpeg(size=(400, 300))))

    with caplog.at_level(logging.DEBUG), pytest.raises(mistral.MistralError):
        photos.read([jpeg])

    assert "base64" not in caplog.text and "test-only" not in caplog.text
    assert max((len(record.getMessage()) for record in caplog.records), default=0) < 200


# --- 3. the allowance ----------------------------------------------------------


@pytest.mark.django_db
def test_each_plan_reads_its_monthly_number_of_decks():
    allowance = dict(Plan.objects.values_list("slug", "max_photo_imports_per_month"))

    assert allowance == {"guest": 0, "free": 5, "koi": 20, "kraken": 50}


@pytest.mark.django_db
def test_the_free_plan_is_checked_against_its_photo_allowance(django_user_model):
    user = django_user_model.objects.create_user(email="photo@example.com", password="x" * 12)
    metric = UsageRecord.Metric.PHOTO_IMPORTS
    quotas.consume(user, metric, 5)

    decision = quotas.check(user, metric, raise_on_fail=False)

    assert not decision.allowed and decision.limit == 5
