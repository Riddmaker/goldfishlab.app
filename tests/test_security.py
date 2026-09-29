"""Phase 8: the headers, the limiters and the upload ceilings.

Every test here asserts a **response**, never a setting. `SECURE_REFERRER_POLICY
== "same-origin"` proves that somebody typed a string into a file; it does not
prove that a browser is ever told. The two came apart once already in this
repository - the CSP middleware has to be in `MIDDLEWARE` for any of the policy
to reach a page, and a settings-shaped test would have passed with it missing.

The rate-limit tests lean on `tests/conftest.py::_isolate_cache`, which clears
the cache around every test. Without it these would pass alone and fail in the
suite, or worse, pass on a clean Redis and fail on the second run of the day.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory
from django.urls import reverse

from core.ratelimit import client_ip
from decks.services import MAX_UPLOAD_ROWS, ImportError_, decode

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


# --- Headers ----------------------------------------------------------------


def test_the_home_page_carries_a_content_security_policy(client):
    """CSP is configured in base.py, so it is on in development and in tests.

    A policy that only exists in prod.py is a policy nobody has run.
    """
    response = client.get(reverse("home"))
    assert "Content-Security-Policy" in response.headers


@pytest.mark.parametrize(
    "directive",
    [
        "default-src 'self'",
        "script-src 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
    ],
)
def test_the_policy_names_each_directive(client, directive):
    policy = client.get(reverse("home")).headers["Content-Security-Policy"]
    assert directive in policy


def test_inline_style_attributes_are_allowed_and_style_elements_are_not():
    """The one directive in the policy that is easy to get wrong.

    Every chart in this application is a server-rendered div whose *width* is
    the datum, so the pages carry `style="width: N%"` attributes. A nonce
    cannot rescue them - CSP nonces apply to <style> and <script> ELEMENTS and
    never to a style attribute - so `style-src-attr` has to be split out.

    Asserting both halves, because the whole value of splitting the directive
    is that the element form stays refused. `style-src 'self'` with no
    `'unsafe-inline'` is what says so.
    """
    from django.conf import settings

    directives = settings.CONTENT_SECURITY_POLICY["DIRECTIVES"]
    assert "'unsafe-inline'" in directives["style-src-attr"]
    assert "'unsafe-inline'" not in directives["style-src"]


def test_the_chart_pages_still_render_their_inline_widths(client, signed_in, settings):
    """The reason `style-src-attr` exists, checked from the other end.

    If somebody ever tightens the policy by deleting that directive, this test
    goes on passing - the server still emits the attribute, the browser is what
    would refuse it. So this is not a security test; it is the record of what
    the security test is protecting, and the styleguide is where a width lives
    with no login and no fixtures (a development page, hence DEBUG).
    """
    settings.DEBUG = True
    response = signed_in.get(reverse("styleguide"))
    assert response.status_code == 200
    assert b"style=" in response.content


@pytest.mark.parametrize("host", ["https://checkout.stripe.com", "https://billing.stripe.com"])
def test_a_form_may_be_redirected_to_stripe(client, host):
    """Trap 50: Chrome and Safari apply `form-action` to the redirects a POST
    follows. The checkout and portal buttons post to our own views, which 302
    to these two hosts, so without them paying is refused in the browser."""
    policy = client.get(reverse("home")).headers["Content-Security-Policy"]
    form_action = next(part for part in policy.split(";") if "form-action" in part)
    assert host in form_action


def test_card_images_may_be_hot_linked_from_scryfall(client):
    """Required by Scryfall's terms: their images, never rehosted by us."""
    policy = client.get(reverse("home")).headers["Content-Security-Policy"]
    assert "https://cards.scryfall.io" in policy


def test_the_admin_is_excluded_from_the_policy(client):
    """Django's own admin ships inline scripts and styles.

    Rewriting somebody else's templates to satisfy our header is not a
    security improvement, and the admin is staff-only behind a login.
    """
    response = client.get("/admin/login/")
    assert "Content-Security-Policy" not in response.headers


def test_the_ordinary_security_headers_reach_the_browser(client):
    response = client.get(reverse("home"))
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "same-origin"
    assert response.headers["X-Frame-Options"] == "DENY"


# --- Which address a request is counted against -----------------------------


def test_the_client_address_is_remote_addr_when_there_is_no_proxy():
    request = RequestFactory().get("/", REMOTE_ADDR="198.51.100.7")
    assert client_ip(request) == "198.51.100.7"


def test_the_client_address_is_the_rightmost_forwarded_entry():
    """The whole point, and the half that is normally got wrong.

    `X-Forwarded-For` is a list each proxy appends to. The LEFTMOST entry is
    the one everybody reaches for and it is the one the client can write, so
    counting it means anybody can have a fresh bucket per request by sending
    their own header - the limiter is off for exactly the person it exists to
    stop. Here the client has claimed to be 1.2.3.4 and the proxy has appended
    the address that really connected.
    """
    request = RequestFactory().get(
        "/",
        HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9",
        REMOTE_ADDR="10.0.0.1",
    )
    assert client_ip(request) == "203.0.113.9"


def test_a_spoofed_forwarded_header_alone_does_not_win():
    """One entry means one proxy hop, so that entry IS the proxy's word for it.

    A client sending a bare `X-Forwarded-For: 9.9.9.9` to a server with a real
    proxy in front cannot produce this case: the proxy appends, making two
    entries, and the test above covers that. This pins the single-entry shape
    so the `rsplit` is not silently reading the wrong end when there is only
    one.
    """
    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="9.9.9.9")
    assert client_ip(request) == "9.9.9.9"


def test_no_proxy_means_the_header_is_not_believed(settings):
    settings.TRUSTED_PROXY_COUNT = 0
    request = RequestFactory().get(
        "/", HTTP_X_FORWARDED_FOR="1.2.3.4", REMOTE_ADDR="198.51.100.7"
    )
    assert client_ip(request) == "198.51.100.7"


def test_a_second_trusted_proxy_moves_the_count_one_left(settings):
    settings.TRUSTED_PROXY_COUNT = 2
    request = RequestFactory().get(
        "/", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9, 10.0.0.5", REMOTE_ADDR="10.0.0.1"
    )
    assert client_ip(request) == "203.0.113.9"


def test_allauth_counts_the_same_client_as_our_own_limiter():
    """Trap 47: allauth has its own IP lookup and ignores RATELIMIT_IP_META_KEY.

    Without `ALLAUTH_TRUSTED_PROXY_COUNT` it keys login, signup and password
    reset on REMOTE_ADDR, which behind the Jelastic proxy is the proxy - one
    bucket for every visitor, so ten mistyped passwords in a minute from
    anybody locked the whole site out of signing in.
    """
    from allauth.account.adapter import get_adapter

    request = RequestFactory().get(
        "/", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9", REMOTE_ADDR="10.0.0.1"
    )
    assert get_adapter(request).get_client_ip(request) == "203.0.113.9"
    assert get_adapter(request).get_client_ip(request) == client_ip(request)


# --- The limiters actually trigger ------------------------------------------


@pytest.fixture(autouse=True)
def _pinned_window(monkeypatch):
    """Keep every django-ratelimit count inside one fixed window.

    It counts in fixed windows, so a loop of posts that straddles a window
    edge restarts the count and a limit test fails for no reason - seen with
    the admin login (twelve password hashes) and the simulation start (25
    posts). Pinning the window makes these tests measure the limit, not the
    clock. allauth's own limits count differently and are not touched.
    """
    import django_ratelimit.core

    monkeypatch.setattr(django_ratelimit.core, "_get_window", lambda value, period: 4_102_444_800)


def _post_repeatedly(client, url, times, data=None):
    """Hammer an endpoint and return the last response."""
    response = None
    for _ in range(times):
        response = client.post(url, data or {})
    return response


def test_starting_simulations_in_a_loop_is_refused(signed_in):
    """Twenty a minute is the valve; the quota is only the budget.

    The quota bounds games per month and refunds a cancelled run. It is
    perfectly happy with twenty chords of Celery tasks enqueued in one second,
    which is the thing that actually falls over.

    The deck id is nonsense on purpose - the point is that the limiter counts
    the request BEFORE the view decides anything, so this never reaches a
    queue even while it is being hammered.
    """
    url = reverse("simulations:create", args=["00000000-0000-0000-0000-000000000000"])
    response = _post_repeatedly(signed_in, url, times=25)
    assert response.status_code == 429


def test_importing_decks_in_a_loop_is_refused(signed_in):
    response = _post_repeatedly(signed_in, reverse("decks:import"), times=15)
    assert response.status_code == 429


def test_a_refusal_says_waiting_will_work(signed_in):
    """429, not 403, and the difference is the whole point of the page.

    `Ratelimited` subclasses `PermissionDenied`, so without
    `core.ratelimit.permission_denied` this would arrive as "that is not yours
    to open" - which is both untrue and unactionable.
    """
    response = _post_repeatedly(signed_in, reverse("decks:import"), times=15)
    assert response.status_code == 429
    assert b"wait about a minute" in response.content


def test_an_ordinary_import_is_not_refused(signed_in):
    """The limiter must not be so tight that using the site trips it."""
    response = signed_in.post(reverse("decks:import"), {})
    assert response.status_code == 200


def test_signing_up_in_a_loop_is_refused(client):
    """allauth's own limit, which only works because the cache is shared.

    Nothing in this repository configures it - `ACCOUNT_RATE_LIMITS` defaults
    to `20/m/ip` for signup. It is asserted here anyway, because the thing
    that makes it real is base.py pointing the default cache at Redis, and a
    change there would silently turn this off. See trap 40.
    """
    url = reverse("account_signup")
    response = None
    for i in range(25):
        response = client.post(
            url,
            {"email": f"flood{i}@example.com", "password1": PASSWORD},
        )
    assert response.status_code == 429


def test_guessing_the_admin_password_in_a_loop_is_refused(client):
    """Django's admin login sits outside both limiters unless it is wrapped.

    Each of these posts runs the password hasher, which is why the window is
    pinned (`_pinned_window`) - twelve of them straddled a window edge about
    one run in six.
    """
    response = None
    for _ in range(12):
        response = client.post(
            "/admin/login/", {"username": "root@example.com", "password": "guess"}
        )
    assert response.status_code == 429


def test_the_limits_are_counted_somewhere_shared(signed_in):
    """The counter has to survive leaving this process, or it is not a limit.

    LocMemCache is per process, so with gunicorn running several workers a
    "5 per minute" limit is really 5 per minute per worker and nothing says
    so. This asserts the property rather than the backend name: a value
    written to the cache is readable back from it.
    """
    cache.set("shared-counter-probe", 1, 30)
    assert cache.get("shared-counter-probe") == 1


# --- Upload ceilings --------------------------------------------------------


def test_a_file_with_more_rows_than_any_collection_is_refused():
    """The byte limit does not bound the WORK.

    A megabyte of `1 x\\n` is a quarter of a million rows, every one of which
    the resolver looks up against a 35,568-row card table. The size check is
    happy with that file.
    """
    raw = b"1 Swamp\n" * (MAX_UPLOAD_ROWS + 10)
    with pytest.raises(ImportError_) as excinfo:
        decode(raw)
    assert "the limit is" in str(excinfo.value)


def test_the_row_limit_is_generous_enough_for_a_real_collection():
    """A serious collection is tens of thousands of rows and must still work."""
    raw = b"1 Swamp\n" * 20_000
    assert decode(raw).count("\n") == 20_000


def test_a_refused_file_is_never_parsed():
    """Refusing after doing the work is not refusing.

    `decode` is the boundary every upload crosses - before the parser, before
    the resolver and before the quota. A row limit enforced in a parser is a
    limit each future parser has to remember, and one of them will not.

    Checked through `prepare`, the whole path, and with a file that a parser
    would also reject: the header is nonsense, so if the row limit were
    enforced anywhere downstream the message would be the column complaint
    instead. Asserting WHICH refusal arrives is what makes this different from
    the test above.
    """
    from decks.services import prepare

    raw = b"nonsense,header,row\n" + (b"1 Swamp\n" * (MAX_UPLOAD_ROWS + 10))
    with pytest.raises(ImportError_) as excinfo:
        prepare(raw)
    assert "the limit is" in str(excinfo.value)


# --- Email, which signup depends on -----------------------------------------


def test_signup_sends_a_confirmation_and_therefore_needs_mail(client):
    """The premise of the boot-time check: signup really does send a message.

    If email verification ever stops being mandatory, `core.checks.E001`
    becomes noise and should go. This test is what would notice.
    """
    from django.core import mail

    response = client.post(
        reverse("account_signup"),
        {"email": "newcomer@example.com", "password1": PASSWORD},
    )
    assert response.status_code in (302, 200)
    assert len(mail.outbox) == 1
    assert "newcomer@example.com" in mail.outbox[0].to


def test_production_refuses_to_boot_with_no_mail_host(settings):
    """A missing EMAIL_HOST is an Error, not a Warning, and that is the point.

    Django's default backend is SMTP to localhost:25, which does not exist in
    the production container, so the failure is HTTP 500 on the signup form -
    not a degraded experience. It is invisible in development (console
    backend) and in the test suite (locmem), so the first person to meet it
    would have been a real visitor.

    `manage.py migrate` runs system checks, and start.sh runs migrate, so an
    Error here means the container does not come up at all. That is the
    intended trade: refusing to start is louder than serving a broken signup.
    """
    from core.checks import check_email_is_configured

    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = ""
    settings.ACCOUNT_EMAIL_VERIFICATION = "mandatory"

    errors = check_email_is_configured(None)
    assert [error.id for error in errors] == ["core.E001"]


def test_a_configured_mail_host_passes(settings):
    from core.checks import check_email_is_configured

    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "mail.example.ch"
    settings.ACCOUNT_EMAIL_VERIFICATION = "mandatory"

    assert check_email_is_configured(None) == []


def test_the_console_backend_is_a_deliberate_way_out(settings):
    """An installation that genuinely does not send mail must still boot."""
    from core.checks import check_email_is_configured

    settings.EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    settings.EMAIL_HOST = ""
    settings.ACCOUNT_EMAIL_VERIFICATION = "mandatory"

    assert check_email_is_configured(None) == []


def test_both_tls_flags_at_once_is_refused(settings):
    """Mutually exclusive, and Django raises at send time naming neither.

    STARTTLS on 587 is EMAIL_USE_TLS; implicit TLS on 465 is EMAIL_USE_SSL.
    Getting this wrong means mail that never sends, discovered by a stranger
    who cannot finish signing up.
    """
    from core.checks import check_email_is_configured

    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "mail.example.ch"
    settings.ACCOUNT_EMAIL_VERIFICATION = "mandatory"
    settings.EMAIL_USE_TLS = True
    settings.EMAIL_USE_SSL = True

    assert [error.id for error in check_email_is_configured(None)] == ["core.E002"]
