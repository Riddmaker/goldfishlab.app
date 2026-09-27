"""The code is public under the AGPL-3.0: the licence, the links, the gates.

Publishing the repository made three things into promises. The site offers
its users the source and a way to report problems; the terms and the privacy
policy describe that truthfully; and nothing reaches main without the checks.
"""

from pathlib import Path

import pytest
import yaml
from django.urls import reverse

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/example/goldfish-lab"


@pytest.mark.parametrize("configured", [False, True])
def test_the_footer_links_the_code_and_the_tracker_only_when_configured(
    client, settings, configured
):
    settings.SOURCE_CODE_URL = REPO if configured else ""
    content = client.get(reverse("home")).content.decode()
    assert (f'href="{REPO}"' in content) is configured
    assert (f'href="{REPO}/issues/new/choose"' in content) is configured
    assert ("Report a problem" in content) is configured


@pytest.mark.parametrize("email", ["", "hello@example.ch"])
def test_the_footer_offers_email_for_anyone_without_github(client, settings, email):
    settings.LEGAL_CONTACT_EMAIL = email
    content = client.get(reverse("home")).content.decode()
    assert ("Email us" in content) is bool(email)


@pytest.mark.parametrize("configured", [False, True])
def test_the_terms_state_the_licence_and_no_longer_claim_the_code(
    client, settings, configured
):
    settings.SOURCE_CODE_URL = REPO if configured else ""
    content = client.get(reverse("terms")).content.decode()
    assert "GNU Affero General Public License, version 3" in content
    assert "belongs to the" not in content
    assert (f'href="{REPO}"' in content) is configured


@pytest.mark.parametrize("configured", [False, True])
def test_the_privacy_policy_names_github_only_when_the_site_links_it(
    client, settings, configured
):
    settings.SOURCE_CODE_URL = REPO if configured else ""
    content = client.get(reverse("privacy")).content.decode()
    assert ("GitHub, Inc." in content) is configured


def test_the_licence_is_the_agpl_and_the_font_licence_travels_with_the_fonts():
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").lstrip().startswith(
        "GNU AFFERO GENERAL PUBLIC LICENSE"
    )
    ofl = (ROOT / "static" / "fonts" / "OFL.txt").read_text(encoding="utf-8")
    assert "EB Garamond" in ofl
    assert "SIL Open Font License, Version 1.1" in ofl


def _yaml(*parts):
    return yaml.safe_load((ROOT.joinpath(*parts)).read_text(encoding="utf-8"))


def test_every_issue_goes_through_a_form_and_security_never_goes_public():
    forms = sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
    forms = [f for f in forms if f.name != "config.yml"]
    assert len(forms) >= 3
    for form in forms:
        data = _yaml(".github", "ISSUE_TEMPLATE", form.name)
        assert data["name"] and data["description"] and data["body"], form.name
        assert "public" in yaml.safe_dump(data["body"]), form.name
    config = _yaml(".github", "ISSUE_TEMPLATE", "config.yml")
    assert config["blank_issues_enabled"] is False
    assert (ROOT / "SECURITY.md").is_file()


def test_the_checks_gate_dev_pull_requests_and_the_deploy():
    """GO-LIVE's ruleset requires the status check "test"; this pins its name."""
    checks = _yaml(".github", "workflows", "checks.yml")
    # PyYAML reads the bare key `on` as the boolean True (YAML 1.1).
    triggers = checks[True]
    assert triggers["push"]["branches"] == ["dev"]
    assert triggers["pull_request"]["branches"] == ["main"]
    assert "workflow_call" in triggers
    assert "test" in checks["jobs"]

    deploy = _yaml(".github", "workflows", "deploy-prod.yml")
    assert deploy[True]["push"]["branches"] == ["main"]
    assert deploy["jobs"]["test"]["uses"] == "./.github/workflows/checks.yml"
    assert deploy["jobs"]["build-and-deploy"]["needs"] == "test"
