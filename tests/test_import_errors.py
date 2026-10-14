"""A refused upload says why, also to a screen reader (P20 F6b: E1.40, E1.74).

The file field had no name of its own - the drop zone's words are hidden from
a screen reader, so it said the browser's "Choose File". A refused upload put
its error far down the page with nothing focused, and the field's
`aria-describedby` pointed at an error list without that id. Now the field
with the first error takes the focus, is marked invalid and described by its
error, and the page title starts with "Error:" - for the account's importer
and the guest's trial alike, which share the form.
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from decks.forms import ImportForm

pytestmark = pytest.mark.django_db

User = get_user_model()


@pytest.fixture(params=["account", "guest"])
def door(request, client, catalogue):
    """The importer's URL, signed in for the account's one."""
    if request.param == "account":
        client.force_login(User.objects.create_user(email="a@example.com", password="pw-x-1"))
        return reverse("decks:import")
    return reverse("guests:try")


def _tag(body, element_id):
    return re.search(rf'<(?:input|textarea|select)[^>]*id="{element_id}"[^>]*>', body).group(0)


def test_the_file_field_has_a_name_of_its_own(client, door):
    body = client.get(door).content.decode()

    assert '<label for="id_file" class="sr-only">Deck file</label>' in body
    assert "autofocus" not in _tag(body, "id_file")
    assert "<title>Error:" not in body


def test_a_missing_file_focuses_the_file_field_and_says_why(client, door):
    response = client.post(door, {"source": "file"})

    body = response.content.decode()
    field = _tag(body, "id_file")
    assert response.status_code == 200
    assert "autofocus" in field and 'aria-invalid="true"' in field
    assert 'aria-describedby="dropzone-hint id_file_error"' in field
    assert '<ul id="id_file_error"' in body
    assert "<title>Error: " in body


def test_an_empty_paste_focuses_the_list(client, door):
    body = client.post(door, {"source": "paste", "text": ""}).content.decode()

    field = _tag(body, "id_text")
    assert "autofocus" in field and 'aria-describedby="id_text_error"' in field
    assert '<ul id="id_text_error"' in body
    assert "autofocus" not in _tag(body, "id_file")


def test_a_name_too_long_focuses_the_name_in_the_opened_options(client, door):
    upload = SimpleUploadedFile("deck.txt", b"1 Sol Ring\n", content_type="text/plain")

    body = client.post(door, {"source": "file", "file": upload,
                              "name": "x" * 121}).content.decode()

    field = _tag(body, "id_name")
    assert "autofocus" in field
    assert 'aria-describedby="id_name_helptext id_name_error"' in field
    assert '<p id="id_name_helptext"' in body and '<ul id="id_name_error"' in body
    assert re.search(r"<details[^>]*open>", body)


def test_the_focus_follows_the_order_on_the_page():
    form = ImportForm(data={"source": "paste", "text": "1 Sol Ring"})
    assert form.is_valid() and form.focus is None

    form.add_error("format", "No idea.")
    assert form.focus == "format"
    form.add_error("text", "Not a list.")
    assert form.focus == "text"
