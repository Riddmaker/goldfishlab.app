"""Phase 8: the data export, account deletion, and the legal pages.

The export and the deletion are legal obligations, so the tests are written
against the obligation rather than against the implementation: "everything is
returned" and "everything is gone" are counted across every user-owned model,
not asserted field by field on the two or three somebody remembered.

The test that matters most is
`test_every_user_owned_model_is_exported_or_deliberately_skipped`. A model
added in a later phase is a new category of personal data, and the failure mode
of a hand-written export is that it silently stops being complete.
"""

import json

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from accounts import privacy

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "pw-for-test-only"


@pytest.fixture
def member():
    return User.objects.create_user(email="member@example.com", password=PASSWORD)


@pytest.fixture
def signed_in(client, member):
    client.force_login(member)
    return client


@pytest.fixture
def furnished(member):
    """A member with a row in as many of their own tables as is cheap to make."""
    from decks.models import Deck
    from simulations.models import SimulationRun

    deck = Deck.objects.create(owner=member, name="Chainer, Dementia Master")
    SimulationRun.objects.create(
        owner=member, deck=deck, games_total=100, turns=6, seed=12345
    )
    return member


# --- The export is complete -------------------------------------------------


def test_every_user_owned_model_is_exported_or_deliberately_skipped():
    """The test that keeps the export honest as the schema grows.

    Every model with a path to the user model has to be either in
    `privacy.EXPORTED` or named here with a reason. Adding a user-owned model
    in a later phase is adding a category of personal data, and the whole
    failure mode of a hand-written export is that nobody notices.
    """
    exported = {label for _, label, _ in privacy.EXPORTED}

    #: Models that reach a user but are NOT that user's personal data to
    #: return. Each needs its reason here, not in a commit message.
    skipped = {
        # Django's own. A session is a credential, not data about somebody, and
        # handing one back in a downloadable file would be handing back a key.
        "sessions.Session",
        "admin.LogEntry",
        "auth.Permission",
        "auth.Group",
        # The account row itself, handled by hand under "account" so the
        # password hash cannot be included by accident.
        settings.AUTH_USER_MODEL,
        # allauth's. An email confirmation is a token; a stored address is
        # already in the account block.
        "account.EmailAddress",
        "account.EmailConfirmation",
        # A Stripe webhook receipt. Keyed on Stripe's event id and kept for
        # idempotency; it is our record of a delivery, not the user's data.
        "billing.StripeEvent",
        # Celery's result rows. Keyed on a task id, owned by nobody.
        "django_celery_results.TaskResult",
        "django_celery_results.ChordCounter",
        "django_celery_results.GroupResult",
    }

    missed = []
    for model in apps.get_models():
        label = model._meta.label
        if label in exported or label in skipped:
            continue
        # Does anything on this model lead to a user?
        reaches_user = any(
            field.is_relation
            and field.related_model is not None
            and field.related_model._meta.label == settings.AUTH_USER_MODEL
            for field in model._meta.get_fields()
        )
        if reaches_user:
            missed.append(label)

    assert not missed, (
        f"These models hold data linked to a user but are neither exported nor "
        f"listed as deliberately skipped: {missed}. Add them to "
        f"accounts.privacy.EXPORTED, or to `skipped` here with the reason."
    )


def test_the_export_contains_the_users_decks(furnished):
    data = privacy.export(furnished)
    names = [deck["name"] for deck in data["decks"]]
    assert "Chainer, Dementia Master" in names


def test_the_export_contains_the_users_runs(furnished):
    assert len(privacy.export(furnished)["simulation_runs"]) == 1


def test_the_export_contains_no_other_accounts_data(furnished):
    """The obvious bug in any export, and the worst one."""
    from decks.models import Deck

    stranger = User.objects.create_user(email="stranger@example.com", password=PASSWORD)
    Deck.objects.create(owner=stranger, name="Not Yours")

    names = [deck["name"] for deck in privacy.export(furnished)["decks"]]
    assert "Not Yours" not in names


def test_the_export_never_carries_a_password_hash(furnished):
    body = json.dumps(privacy.export(furnished), default=str)
    assert furnished.password not in body
    assert "pbkdf2" not in body


def test_the_export_never_carries_stripe_identifiers(furnished):
    """A downloadable file holding these is a file worth stealing.

    The subscription's *state* is exported; the identifiers Stripe
    authenticates on are not.
    """
    subscription = furnished.subscription
    subscription.stripe_customer_id = "cus_test_notreal"
    subscription.stripe_subscription_id = "sub_test_notreal"
    subscription.save()

    body = json.dumps(privacy.export(furnished), default=str)
    assert "cus_test_notreal" not in body
    assert "sub_test_notreal" not in body
    # ...but the plan and status are, or the export would be hiding something
    # the person is entitled to see.
    assert privacy.export(furnished)["subscription"][0]["status"]


def test_the_export_says_what_it_leaves_out(furnished):
    """A person who opens an export deserves to know if it is the whole answer."""
    assert "Scryfall" in privacy.export(furnished)["about_this_file"]


# --- The export is served safely --------------------------------------------


def test_downloading_needs_a_post(signed_in):
    """A GET export is a URL in a history file and in every proxy log."""
    assert signed_in.get(reverse("accounts:export")).status_code == 405


def test_downloading_needs_a_login(client):
    response = client.post(reverse("accounts:export"))
    assert response.status_code == 302
    assert "login" in response["Location"]


def test_the_download_is_a_json_attachment_that_is_not_cached(signed_in):
    response = signed_in.post(reverse("accounts:export"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    assert "attachment" in response["Content-Disposition"]
    assert response["Cache-Control"] == "no-store"
    assert json.loads(response.content)["account"]["email"] == "member@example.com"


# --- Deletion actually deletes ----------------------------------------------


def _rows_for(user):
    """Count every row in every exported table that belongs to this user."""
    return {
        label: apps.get_model(label).objects.filter(**{path: user}).count()
        for _, label, path in privacy.EXPORTED
    }


def test_deleting_an_account_leaves_nothing_behind(furnished):
    """Counted across every user-owned model, not just the two that are obvious.

    Every one of them reaches the user through a CASCADE, so `user.delete()` is
    the whole erasure - but that is a property of the schema and schemas
    change, so it is asserted rather than trusted.
    """
    assert sum(_rows_for(furnished).values()) > 0

    pk = furnished.pk
    privacy.delete(furnished)

    assert not User.objects.filter(pk=pk).exists()
    for _, label, path in privacy.EXPORTED:
        model = apps.get_model(label)
        assert model.objects.filter(**{f"{path}_id": pk}).count() == 0, label


def test_deleting_an_account_leaves_other_accounts_alone(furnished):
    from decks.models import Deck

    stranger = User.objects.create_user(email="stranger@example.com", password=PASSWORD)
    Deck.objects.create(owner=stranger, name="Still Here")

    privacy.delete(furnished)

    assert Deck.objects.filter(owner=stranger).count() == 1


def test_a_paid_subscription_blocks_deletion(member):
    """A protection, not an obstacle.

    Every user-owned row cascades, so deleting here would take the local
    subscription with it and leave Stripe billing a card every month for an
    account nobody can sign in to stop. One place ends a subscription, and it
    is not us.
    """
    from billing.models import Plan, Subscription

    paid = Plan.objects.create(
        name="Paid", slug="paid-test", is_default=False, price_chf_cents=900
    )
    subscription = member.subscription
    subscription.plan = paid
    subscription.status = Subscription.Status.ACTIVE
    subscription.save()

    assert privacy.deletion_blockers(member)


def test_a_free_account_is_not_blocked(member):
    assert privacy.deletion_blockers(member) == []


def test_the_blocked_account_is_refused_by_the_server_not_only_the_page(signed_in, member):
    """A page that hides a button is not a rule."""
    from billing.models import Plan, Subscription

    paid = Plan.objects.create(
        name="Paid", slug="paid-test", is_default=False, price_chf_cents=900
    )
    subscription = member.subscription
    subscription.plan = paid
    subscription.status = Subscription.Status.ACTIVE
    subscription.save()

    signed_in.post(reverse("accounts:delete"), {"confirm_email": member.email})
    assert User.objects.filter(pk=member.pk).exists()


def test_deleting_needs_the_address_typed_correctly(signed_in, member):
    response = signed_in.post(reverse("accounts:delete"), {"confirm_email": "nope@example.com"})
    assert response.status_code == 302
    assert User.objects.filter(pk=member.pk).exists()


def test_deleting_with_no_confirmation_at_all_does_nothing(signed_in, member):
    signed_in.post(reverse("accounts:delete"), {})
    assert User.objects.filter(pk=member.pk).exists()


def test_the_typed_address_is_not_case_sensitive(signed_in, member):
    """Refusing "Member@Example.com" would be pedantry, not safety."""
    signed_in.post(reverse("accounts:delete"), {"confirm_email": "MEMBER@example.com"})
    assert not User.objects.filter(pk=member.pk).exists()


def test_deleting_signs_the_person_out(signed_in, member):
    signed_in.post(reverse("accounts:delete"), {"confirm_email": member.email})
    response = signed_in.get(reverse("home"))
    assert not response.wsgi_request.user.is_authenticated


def test_deleting_needs_a_post(signed_in, member):
    """A browser's prefetcher must not be able to delete an account."""
    assert signed_in.get(reverse("accounts:delete")).status_code == 405
    assert User.objects.filter(pk=member.pk).exists()


def test_deleting_needs_a_login(client):
    response = client.post(reverse("accounts:delete"))
    assert response.status_code == 302
    assert "login" in response["Location"]


# --- The pages themselves ---------------------------------------------------


@pytest.mark.parametrize("name", ["terms", "privacy", "imprint", "methodology"])
def test_the_public_pages_render_without_a_login(client, name):
    """All of them have to be readable BEFORE somebody signs up."""
    response = client.get(reverse(name))
    assert response.status_code == 200


def test_the_data_page_needs_a_login(client):
    response = client.get(reverse("accounts:data"))
    assert response.status_code == 302


def test_the_data_page_renders(signed_in):
    response = signed_in.get(reverse("accounts:data"))
    assert response.status_code == 200
    assert b"Download my data" in response.content


def test_every_page_links_to_the_legal_pages(client):
    """The footer is on every page, so one check covers the site."""
    content = client.get(reverse("home")).content
    for name in ("terms", "privacy", "imprint", "methodology"):
        assert reverse(name).encode() in content


def test_the_privacy_policy_names_what_is_stored(client):
    content = client.get(reverse("privacy")).content
    for promised in (b"Stripe", b"Switzerland", b"email address"):
        assert promised in content


def test_the_privacy_policy_names_every_service_that_sees_data(client):
    """DSG Art. 19: the recipients, and the countries abroad."""
    content = client.get(reverse("privacy")).content.decode()
    for recipient in ("Infomaniak", "Cloudflare", "Stripe", "Scryfall", "Commander Spellbook"):
        assert recipient in content
    for country in ("Switzerland", "Ireland", "USA"):
        assert country in content
    assert "FDPIC" in content
    # Cloudflare's analytics and security events, Free plan (phase 12 J13).
    assert "for up to 31 days" in content


def test_the_retention_periods_stated_are_the_ones_enforced(client):
    """A period in the policy is a claim about a schedule; this ties the two."""
    from core import tasks

    content = client.get(reverse("privacy")).content.decode()
    assert f"{tasks.PENDING_IMPORT_DAYS} days" in content
    assert f"Contents: {tasks.STRIPE_PAYLOAD_DAYS} days" in content
    assert f"at most {settings.SESSION_COOKIE_AGE // 86400 // 7} weeks" in content


@pytest.mark.parametrize("name", ["terms", "privacy", "imprint"])
def test_the_legal_pages_name_the_operator(client, settings, name):
    settings.LEGAL_OPERATOR_NAME = "Erika Muster"
    settings.LEGAL_OPERATOR_ADDRESS = ["Musterstrasse 1", "8000 Zürich", "Switzerland"]
    settings.LEGAL_CONTACT_EMAIL = "hello@example.ch"
    content = client.get(reverse(name)).content.decode()
    assert "Erika Muster" in content
    assert "8000 Zürich<br>" in content
    assert 'href="mailto:hello@example.ch"' in content
    assert "Not configured in this installation" not in content


@pytest.mark.parametrize("switched_on", [False, True])
def test_the_legal_pages_say_who_sells_a_paid_plan(client, settings, switched_on):
    """Stripe Managed Payments changes the seller, so both pages must follow it.

    On: Link is the merchant of record - named as the seller in the terms and
    as an independent controller in the privacy policy. Off: neither page may
    mention Link at all, because a policy naming a recipient that receives
    nothing is as wrong as one that leaves a recipient out.
    """
    settings.STRIPE_MANAGED_PAYMENTS = switched_on
    terms = client.get(reverse("terms")).content.decode()
    policy = client.get(reverse("privacy")).content.decode()

    assert ("Sold through Link, LLC" in terms) is switched_on
    assert ("https://link.com/terms" in terms) is switched_on
    assert ("Sold through Link, LLC" in policy) is switched_on
    assert ("https://link.com/privacy" in policy) is switched_on
    assert ("cancels the subscription" in policy) is switched_on
    # What does not change with it: our own refund promise and Stripe as processor.
    assert "14 days" in terms
    assert "Stripe Payments Europe" in policy


def test_an_unnamed_operator_is_visible_rather_than_blank(client, settings):
    settings.LEGAL_OPERATOR_NAME = ""
    content = client.get(reverse("imprint")).content.decode()
    assert "Not configured in this installation" in content


def test_the_privacy_policy_links_to_the_buttons_it_promises(client):
    """A right described in prose and implemented nowhere is not a right."""
    content = client.get(reverse("privacy")).content
    assert reverse("accounts:data").encode() in content


def test_the_methodology_page_states_the_limits_rather_than_the_features(client):
    """The page exists to say what is NOT simulated. That is its whole value."""
    content = client.get(reverse("methodology")).content
    for honest in (b"no opponent", b"Combat", b"hypergeometric", b"London mulligan"):
        assert honest in content


def test_the_methodology_page_states_the_mulligan_rule_the_engine_implements():
    """Pinned against the engine, not against the prose.

    The page tells people the keep rule decides every number that follows it,
    which makes it the sentence on the site most worth keeping true.
    """
    from pathlib import Path

    from simulation.game import MAX_MULLIGANS, Game

    page = Path(settings.BASE_DIR, "templates/core/methodology.html").read_text(
        encoding="utf-8"
    )
    assert "2 to 5 lands" in page
    assert "0 or 6 or more lands" in page
    assert f"after {['zero', 'one', 'two', 'three'][MAX_MULLIGANS]} mulligans" in page
    # The free first mulligan, stated on the page, asserted from the engine.
    assert Game.cards_to_bottom(1) == 0
    assert Game.cards_to_bottom(2) == 1


def test_the_methodology_page_states_the_draw_rule_the_form_defaults_to():
    """CR 103.8c: in multiplayer nobody skips the first draw.

    The run form used to argue for "on the draw" with a reason that was
    backwards. Pinned from both ends: the page says it, the form defaults to
    it, and the engine does draw on turn one when told the multiplayer rule.
    """
    import random
    from pathlib import Path

    from simulation.game import Game
    from simulations.forms import RunForm

    page = Path(settings.BASE_DIR, "templates/core/methodology.html").read_text(
        encoding="utf-8"
    )
    assert "103.8c" in page
    assert RunForm().fields["on_the_play"].initial == 0

    game = Game(random.Random(1), on_the_play=False)
    game.take_opening_hand()
    before = len(game.hand)
    game.begin_turn()
    assert len(game.hand) == before + 1
