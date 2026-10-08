"""The changed privacy policy, shown once to accounts made before it (D2)."""

from datetime import date
from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts import consent

pytestmark = pytest.mark.django_db

UPDATE = reverse("accounts:privacy_update")


@pytest.fixture
def old_account(client):
    """An account from before D2, signed in."""
    user = get_user_model().objects.create_user(email="old@example.com", password="x" * 12)
    user.privacy_accepted = None
    user.save(update_fields=["privacy_accepted"])
    client.force_login(user)
    return user


def test_a_new_account_has_agreed_to_the_current_version():
    user = get_user_model().objects.create_user(email="new@example.com", password="x" * 12)
    assert user.privacy_accepted == consent.CONSENT_VERSION
    assert not consent.needs_consent(user)


def test_an_older_version_is_asked_again(old_account):
    old_account.privacy_accepted = date(2026, 1, 1)
    assert consent.needs_consent(old_account)


def test_the_migration_asks_every_existing_account(django_user_model):
    django_user_model.objects.create_user(email="a@example.com", password="x" * 12)
    migration = import_module("accounts.migrations.0007_user_privacy_accepted")
    migration.clear(django_apps, None)
    assert not django_user_model.objects.exclude(privacy_accepted=None).exists()


def test_an_old_account_is_sent_to_the_change_first(client, old_account):
    response = client.get(reverse("decks:list"))
    assert response.status_code == 302
    assert response["Location"] == f"{UPDATE}?next=%2Fdecks%2F"


def test_the_page_highlights_the_change(client, old_account):
    body = client.get(UPDATE).content.decode()
    assert "data-change" in body
    assert "only about sponsors and labelled links" in body
    assert reverse("accounts:data") in body
    assert reverse("privacy") in body


def test_ok_records_the_version_and_goes_on(client, old_account):
    response = client.post(UPDATE, {"next": "/decks/"})
    assert response["Location"] == "/decks/"
    old_account.refresh_from_db()
    assert old_account.privacy_accepted == consent.CONSENT_VERSION
    assert client.get(reverse("decks:list")).status_code == 200


def test_next_cannot_leave_the_site(client, old_account):
    response = client.post(UPDATE, {"next": "https://evil.example/"})
    assert response["Location"] == reverse("home")


@pytest.mark.parametrize("name", ["privacy", "terms", "imprint", "accounts:data", "account_logout"])
def test_the_way_out_stays_open(client, old_account, name):
    response = client.get(reverse(name))
    assert not response.get("Location", "").startswith(UPDATE)


def test_a_form_post_and_a_background_request_go_on(client, old_account):
    assert client.get(reverse("decks:list"), headers={"HX-Request": "true"}).status_code == 200
    response = client.post(reverse("accounts:export"))
    assert not response.get("Location", "").startswith(UPDATE)


def test_guests_and_visitors_are_not_asked(client):
    assert client.get(reverse("home")).status_code == 200
    guest = get_user_model().objects.create_user(email="g@guest.invalid", password=None,
                                                 is_guest=True, privacy_accepted=None)
    assert not consent.needs_consent(guest)
    lab = get_user_model().objects.create_user(email="lab@x.invalid", password=None,
                                               is_system=True, privacy_accepted=None)
    assert not consent.needs_consent(lab)


def test_an_account_that_agreed_is_sent_on(client, old_account):
    consent.accept(old_account)
    response = client.get(f"{UPDATE}?next=/decks/")
    assert response["Location"] == "/decks/"


def test_sign_up_names_both_documents(client):
    body = client.get(reverse("account_signup")).content.decode()
    assert f'href="{reverse("terms")}"' in body
    assert f'href="{reverse("privacy")}"' in body


def test_the_privacy_policy_names_sponsors_and_the_links(client, settings):
    settings.DISCORD_URL, settings.KOFI_URL = "https://discord.gg/x", "https://ko-fi.com/x"
    body = client.get(reverse("privacy")).content.decode()
    assert "Sponsors and shop links" in body
    assert "we do not advertise" not in body
    assert "Ko-fi Labs Limited" in body
    assert "Discord Inc." in body


def test_the_eu_representative_shows_once_named(client, settings):
    assert "Art. 27 GDPR" not in client.get(reverse("privacy")).content.decode()
    settings.LEGAL_EU_REP_NAME = "Rep GmbH"
    settings.LEGAL_EU_REP_ADDRESS = ["Weg 1", "10115 Berlin"]
    settings.LEGAL_EU_REP_EMAIL = "rep@example.com"
    body = client.get(reverse("privacy")).content.decode()
    assert "Art. 27 GDPR" in body
    assert "Rep GmbH<br>" in body and "10115 Berlin" in body
