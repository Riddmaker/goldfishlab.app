"""Account creation, and the subscription that must come with it."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from billing.models import Plan, Subscription

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_user_is_identified_by_email():
    user = User.objects.create_user(email="a@example.com", password="pw-for-test-only")
    assert user.email == "a@example.com"
    assert str(user) == "a@example.com"


def test_user_model_has_no_username_field():
    """allauth is configured for a username-less model; keep it that way.

    If a username field ever reappears, ACCOUNT_USER_MODEL_USERNAME_FIELD=None
    becomes a lie and every signup page 500s.
    """
    field_names = {f.name for f in User._meta.get_fields()}
    assert "username" not in field_names


def test_email_must_be_unique():
    User.objects.create_user(email="dup@example.com", password="pw-for-test-only")
    with pytest.raises(Exception):  # noqa: B017 - IntegrityError or ValidationError
        User.objects.create_user(email="dup@example.com", password="pw-for-test-only")


def test_superuser_flags():
    admin = User.objects.create_superuser(email="root@example.com", password="pw-for-test-only")
    assert admin.is_staff and admin.is_superuser


def test_new_user_gets_free_subscription():
    """Every user must have a Subscription from the moment they exist.

    This is what lets quota checks assume `user.subscription` is never null.
    """
    user = User.objects.create_user(email="sub@example.com", password="pw-for-test-only")
    subscription = Subscription.objects.get(user=user)
    assert subscription.plan.slug == "free"
    assert subscription.status == Subscription.Status.ACTIVE


def test_signup_page_renders(client):
    """Regression guard for the allauth username-field crash."""
    response = client.get(reverse("account_signup"))
    assert response.status_code == 200


def test_login_page_renders(client):
    response = client.get(reverse("account_login"))
    assert response.status_code == 200


def test_signup_creates_user_and_subscription(client):
    response = client.post(
        reverse("account_signup"),
        {
            "email": "flow@example.com",
            "password1": "a-long-enough-test-password",
            "password2": "a-long-enough-test-password",
        },
    )
    assert response.status_code in (200, 302)
    user = User.objects.get(email="flow@example.com")
    assert user.subscription.plan.slug == "free"


def test_free_plan_exists_and_is_default():
    """Seeded by billing/migrations/0002_seed_plans.py."""
    free = Plan.objects.get(slug="free")
    assert free.is_default
    assert Plan.objects.filter(is_default=True).count() == 1
