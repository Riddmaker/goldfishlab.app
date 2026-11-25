"""People's own mats and sleeves (B-10, PR 1): storage and the upload pipeline.

The share is the first place where files from people land. These tests hold
the lockdown from ARCHITECTURE-SECURITY §8: the type is read from the content,
the image is re-encoded (EXIF gone), bytes and pixels have a ceiling, the name
comes from the server, no path leaves the folder, only the owner gets a file,
and a file goes with its row and with the account.
"""

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from accounts import privacy
from accounts.models import User
from looks import colours, images, services
from looks.models import Look


@pytest.fixture(autouse=True)
def share(settings, tmp_path):
    settings.CUSTOM_IMAGES_DIR = tmp_path / "share"
    return settings.CUSTOM_IMAGES_DIR


@pytest.fixture
def owner(db):
    return User.objects.create_user(email="looks@example.com", password="pw-for-test-only")


@pytest.fixture
def look(owner):
    return Look.objects.create(owner=owner, is_default=True)


def _upload(fmt="PNG", size=(400, 300), mode="RGB", name="x.png", colour="red", **save):
    out = io.BytesIO()
    Image.new(mode, size, colour if mode != "1" else 1).save(out, fmt, **save)
    return SimpleUploadedFile(name, out.getvalue(), content_type="image/png")


def _opened(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


# --- colours ---------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "stored"), [
    ("#ABC", "#aabbccff"),
    ("#abcd", "#aabbccdd"),
    ("#12345678", "#12345678"),
    (" #a0b1c2 ", "#a0b1c2ff"),
    ("rgb(255, 0, 10)", "#ff000aff"),
    ("rgba(0,0,0,0.5)", "#00000080"),
    ("rgba(0, 0, 0, 50%)", "#00000080"),
])
def test_a_colour_is_kept_in_one_form(typed, stored):
    assert colours.normalise(typed) == stored


@pytest.mark.parametrize("typed", [
    "", "red", "#12", "#1234567", "rgba(300, 0, 0, 1)", "rgba(0, 0, 0, 2)",
    "#fff; background: url(//evil)", "rgb(1,2,3)) }", "expression(alert(1))", "var(--x)",
])
def test_anything_else_never_reaches_the_css(typed):
    with pytest.raises(ValidationError):
        colours.normalise(typed)


def test_the_model_takes_only_the_stored_form(look):
    look.inner_colour = "#fff"
    with pytest.raises(ValidationError):
        look.full_clean()
    look.inner_colour = "#ffffffff"
    look.full_clean()


# --- the pipeline ----------------------------------------------------------------


def test_a_sleeve_comes_out_as_webp_cut_to_a_card():
    out = _opened(images.process(_upload(size=(1000, 1000)), images.SLEEVE))

    assert out.format == "WEBP"
    assert out.size == images.SLEEVE_SIZE


def test_a_mat_keeps_its_shape_and_shrinks_to_fit():
    out = _opened(images.process(_upload("JPEG", size=(4000, 2000), name="m.jpg"), images.MAT))

    assert out.format == "WEBP"
    assert out.size == (1920, 960)


def test_exif_does_not_survive():
    exif = Image.Exif()
    exif[0x010F] = "Camera maker"  # Make
    exif[0x8825] = {2: (47.0, 22.0, 0.0)}  # GPS: where the photo was taken
    upload = _upload("JPEG", name="p.jpg", exif=exif.tobytes())

    out = _opened(images.process(upload, images.MAT))

    assert not out.getexif()
    assert "exif" not in out.info and "xmp" not in out.info


def test_transparency_is_kept():
    out = _opened(images.process(_upload(mode="RGBA", colour=(255, 0, 0, 128)), images.SLEEVE))

    assert out.mode == "RGBA"


@pytest.mark.parametrize(("content", "name"), [
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "a.png"),
    (b"<html><script>alert(1)</script></html>", "a.webp"),
    (b"GIF89a" + b"\x00" * 64, "a.gif"),
    (b"", "a.png"),
])
def test_the_type_is_read_from_the_content_not_the_name(content, name):
    upload = SimpleUploadedFile(name, content, content_type="image/png")

    with pytest.raises(images.ImageRefused):
        images.process(upload, images.MAT)


def test_a_gif_is_refused_even_when_it_is_a_real_image():
    with pytest.raises(images.ImageRefused):
        images.process(_upload("GIF", name="a.gif"), images.MAT)


def test_more_than_five_megabytes_is_refused_before_it_is_read():
    upload = SimpleUploadedFile("big.png", b"\x89PNG" + b"\x00" * images.MAX_BYTES)

    with pytest.raises(images.ImageRefused, match="5 MB"):
        images.process(upload, images.MAT)


def test_a_small_file_with_too_many_pixels_is_refused():
    # 30 megapixels of one colour pack into a few kilobytes: a bomb in miniature.
    upload = _upload(size=(6000, 5000), mode="1")
    assert upload.size < 100_000

    with pytest.raises(images.ImageRefused, match="25 megapixels"):
        images.process(upload, images.MAT)


# --- paths -----------------------------------------------------------------------


@pytest.mark.parametrize("relative", [
    "../etc/passwd", "/etc/passwd", "a/b.webp", "",
    "0" * 32 + "/../" + "0" * 28 + ".webp", "0" * 32 + "/" + "0" * 32 + ".png",
])
def test_no_path_leaves_the_share(relative):
    with pytest.raises(ValueError):
        images.resolve(relative)


def test_the_server_names_the_file_inside_the_share(look, share):
    relative = services.set_image(look, images.SLEEVE, _upload(name="../../evil.png"))

    assert images.PATH.fullmatch(relative)
    assert relative.startswith(look.pk.hex + "/")
    path = images.resolve(relative)
    assert path.is_relative_to(share.resolve()) and path.is_file()
    assert oct(path.stat().st_mode & 0o777) == oct(0o640)
    look.refresh_from_db()
    assert look.sleeve_image == relative


def test_a_refused_upload_leaves_nothing(look, share):
    with pytest.raises(images.ImageRefused):
        services.set_image(look, images.MAT, SimpleUploadedFile("a.png", b"nope"))

    assert not share.exists() or not any(share.rglob("*"))
    look.refresh_from_db()
    assert look.mat_image == ""


# --- a file goes with its row ----------------------------------------------------


def test_a_new_image_replaces_the_old_file(look, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        first = services.set_image(look, images.MAT, _upload())
    with django_capture_on_commit_callbacks(execute=True):
        second = services.set_image(look, images.MAT, _upload())

    assert first != second
    assert not images.resolve(first).exists()
    assert images.resolve(second).exists()


def test_back_to_the_standard_deletes_the_file(look, django_capture_on_commit_callbacks):
    path = images.resolve(services.set_image(look, images.MAT, _upload()))

    with django_capture_on_commit_callbacks(execute=True):
        services.clear_image(look, images.MAT)

    assert not path.exists()
    assert look.mat_image == ""


def test_deleting_the_look_deletes_its_files(look, django_capture_on_commit_callbacks):
    paths = [images.resolve(services.set_image(look, kind, _upload())) for kind in images.KINDS]

    with django_capture_on_commit_callbacks(execute=True):
        look.delete()

    assert not any(path.exists() for path in paths)
    assert not paths[0].parent.exists()


def test_deleting_the_account_deletes_the_files(owner, look, django_capture_on_commit_callbacks):
    path = images.resolve(services.set_image(look, images.SLEEVE, _upload()))

    with django_capture_on_commit_callbacks(execute=True):
        privacy.delete(owner)

    assert not path.exists()
    assert not Look.objects.exists()


def test_the_data_export_has_the_looks(owner, look):
    services.set_image(look, images.MAT, _upload())

    exported = privacy.export(owner)

    assert [row["mat_image"] for row in exported["looks"]] == [Look.objects.get().mat_image]


# --- serving ---------------------------------------------------------------------


def _url(look, kind=images.MAT):
    return reverse("looks:image", args=[look.pk, kind])


def test_the_owner_gets_the_image_typed_and_private(client, owner, look):
    services.set_image(look, images.MAT, _upload())
    client.force_login(owner)

    response = client.get(_url(look))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/webp"
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["Cache-Control"] == "private, max-age=86400"
    assert _opened(b"".join(response.streaming_content)).format == "WEBP"


def test_nobody_else_learns_the_image_exists(client, look):
    services.set_image(look, images.MAT, _upload())
    other = User.objects.create_user(email="other@example.com", password="pw-for-test-only")

    assert client.get(_url(look)).status_code == 302  # to sign in
    client.force_login(other)
    assert client.get(_url(look)).status_code == 404


def test_a_missing_file_or_kind_is_a_404(client, owner, look):
    relative = services.set_image(look, images.MAT, _upload())
    images.resolve(relative).unlink()
    client.force_login(owner)

    assert client.get(_url(look)).status_code == 404
    assert client.get(_url(look, images.SLEEVE)).status_code == 404
    assert client.get(_url(look, "passwd")).status_code == 404


def test_a_row_that_points_out_of_the_share_is_never_served(client, owner, look):
    Look.objects.filter(pk=look.pk).update(mat_image="../../../etc/passwd")
    client.force_login(owner)

    assert client.get(_url(look)).status_code == 404


def test_one_default_look_per_account(owner, look):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        Look.objects.create(owner=owner, is_default=True)
    Look.objects.create(owner=owner)
