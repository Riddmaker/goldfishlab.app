"""The weekly Search Console review (Paket 5, PR 20): the rules, the key, the workflows.

scripts/search_console.py reads recorded answers here (tests/fixtures/search_console),
so nothing reaches Google. The key is the one thing it must never show: a planted fake
one must not appear in any output or error. The two called workflows run from the
private ops repository; what is checked here is what keeps them safe there.
"""

import datetime as dt
import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml
from django.conf import settings

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "search_console"
WORKFLOWS = ROOT / ".github" / "workflows"
MONDAY = dt.date(2026, 11, 9)
PLANTED = "PLANTED-not-a-key-7f3a91"

_spec = importlib.util.spec_from_file_location(
    "search_console", ROOT / "scripts" / "search_console.py"
)
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


@pytest.fixture
def report():
    return sc.build(sc.read_fixture(FIXTURE), sc.windows(MONDAY), sc.language_codes(), MONDAY)


def _keys(rows):
    return [row["key"] for row in rows]


# --- Windows, title, languages -----------------------------------------------


def test_the_weeks_end_three_days_before_the_run_and_do_not_overlap():
    weeks = sc.windows(MONDAY)
    assert weeks["this"] == (dt.date(2026, 10, 31), dt.date(2026, 11, 6))
    assert weeks["last"] == (dt.date(2026, 10, 24), dt.date(2026, 10, 30))


@pytest.mark.parametrize(
    "today, title",
    [
        (MONDAY, "Search Console review 2026-W46"),
        (dt.date(2026, 11, 13), "Search Console review 2026-W46"),  # a rerun the same week
        (dt.date(2027, 1, 1), "Search Console review 2026-W53"),  # the ISO year, not the calendar's
    ],
)
def test_the_title_is_the_iso_week_of_the_run(today, title):
    assert sc.title(today) == title


def test_the_languages_are_the_settings_languages():
    assert sc.language_codes() == list(settings.LANGUAGE_NAMES)


@pytest.mark.parametrize(
    "page, language",
    [
        ("https://goldfishlab.app/", "en"),
        ("https://goldfishlab.app/decks/12/", "en"),
        ("https://goldfishlab.app/de/", "de"),
        ("https://goldfishlab.app/pt-br/resources/", "pt-br"),
        ("https://goldfishlab.app/JA/", "ja"),
        ("https://goldfishlab.app/dex/", "en"),
        ("https://goldfishlab.app/en/", "en"),
    ],
)
def test_a_page_belongs_to_the_language_of_its_prefix(page, language):
    assert sc.language_of(page, sc.language_codes()) == language


# --- The rules ---------------------------------------------------------------


def test_totals_and_their_change(report):
    clicks = report["totals"]["clicks"]
    assert (clicks["now"], clicks["before"], clicks["delta"]) == (42, 30, 12)
    assert clicks["percent"] == pytest.approx(40)
    assert report["totals"]["position"]["delta"] == pytest.approx(-1.8)


def test_nothing_before_gives_no_percentage_and_no_division_by_zero():
    change = sc.totals({"rows": [{"clicks": 5, "impressions": 9}]}, {"rows": []})["clicks"]
    assert change == {"now": 5, "before": 0, "delta": 5, "percent": None}


def test_new_and_gone_queries(report):
    assert _keys(report["new_queries"]) == [
        "commander mulligan calculator",
        "`@octocat` [hi](https://example.invalid) | <b>",
        "edh deck tester",
    ]
    assert _keys(report["gone_queries"]) == ["old query"]


def test_almost_page_one_needs_position_8_to_20_and_20_impressions(report):
    # "edh deck tester" is at 9 but seen 10 times only.
    assert _keys(report["almost_queries"]) == ["commander mulligan calculator"]
    assert _keys(report["almost_pages"]) == [
        "https://goldfishlab.app/",
        "https://goldfishlab.app/de/",
    ]


def test_low_ctr_needs_a_top_five_place_and_20_impressions(report):
    assert _keys(report["low_ctr_queries"]) == ["mtg goldfish simulator"]
    # /ja/resources/ is at 3 with 2.5 %: not low.
    assert _keys(report["low_ctr_pages"]) == ["https://goldfishlab.app/decks/"]


def test_top_ten_by_clicks():
    rows = {f"q{n}": {"clicks": n, "impressions": 100, "ctr": 0, "position": 1} for n in range(15)}
    assert _keys(sc.top(rows)) == [f"q{n}" for n in range(14, 4, -1)]


def test_languages_sum_the_pages_per_prefix(report):
    by = {row["language"]: row for row in report["languages"]}
    assert list(by) == list(settings.LANGUAGE_NAMES)
    assert by["en"]["impressions"]["now"] == 950  # / and /decks/
    assert by["de"]["clicks"]["now"] == 5 and by["de"]["clicks"]["before"] == 2
    assert by["ja"]["impressions"]["percent"] is None
    assert by["fr"]["clicks"]["now"] == 0


def test_sitemaps_read_the_counts_google_sends_as_strings(report):
    assert report["sitemaps"] == [
        {
            "path": "https://goldfishlab.app/sitemap.xml",
            "last_downloaded": "2026-11-05T10:12:00.000Z",
            "pending": False,
            "errors": 0,
            "warnings": 2,
        }
    ]


# --- Markdown ----------------------------------------------------------------


def test_the_report_reads_as_an_issue(report):
    text = sc.markdown(report)
    assert text.startswith(
        "Property `sc-domain:goldfishlab.app`: 2026-10-31 to 2026-11-06, "
        "compared with 2026-10-24 to 2026-10-30."
    )
    assert "| Clicks | 42 | +12 (+40 %) |" in text
    for heading in ("## Totals", "## Findings", "## Languages", "## Sitemaps", "## Top 10"):
        assert heading in text
    assert "2 to 3 days late" in text and "Rare queries are left out" in text
    assert "No data" not in text


def test_a_query_from_a_stranger_renders_as_plain_text(report):
    """A query is typed by anyone: no mention, link or extra table cell."""
    text = sc.markdown(report)
    row = next(line for line in text.splitlines() if "octocat" in line)
    assert row.startswith("| `'@octocat' [hi](https://example.invalid) / <b>` | 0 |")
    assert row.count("|") == 6
    assert "`@octocat`" not in text
    assert sc.literal("a\nb\r\tc" + "x" * 300).count("\n") == 0
    assert len(sc.literal("x" * 300)) == 202


def test_no_data_yet_is_said_not_failed():
    empty = {name.removesuffix(".json"): {"rows": []} for name in sc.FIXTURE_FILES}
    empty["sitemaps"] = {}
    text = sc.markdown(sc.build(empty, sc.windows(MONDAY), sc.language_codes(), MONDAY))
    assert "**No data for this week yet.**" in text
    assert "**No sitemap is submitted.**" in text
    assert "| Clicks | 0 | +0 |" in text


def test_the_command_writes_the_three_files(tmp_path, capsys):
    code = sc.main(["--fixture", str(FIXTURE), "--today", "2026-11-09", "--out", str(tmp_path)])
    assert code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.json", "report.md", "title.txt"]
    assert (tmp_path / "title.txt").read_text() == "Search Console review 2026-W46\n"
    assert json.loads((tmp_path / "report.json").read_text())["totals"]["clicks"]["now"] == 42
    assert capsys.readouterr().out == "Search Console review 2026-W46\n"


# --- Reading Search Console, with a fake session --------------------------------


class FakeAnswer:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, answer=None, rows=0, error=None):
        self.calls, self.answer, self.rows, self.error = [], answer, rows, error

    def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url, json))
        if self.error:
            raise self.error
        if self.answer:
            return self.answer
        if url.endswith("/sitemaps"):
            return FakeAnswer(200, {"sitemap": []})
        # A full first page, then a short one: two calls per question.
        count = self.rows if json["startRow"] == 0 else 1
        return FakeAnswer(200, {"rows": [{"keys": ["k"], "clicks": 1}] * count})


def test_fetch_asks_the_documented_endpoints(monkeypatch):
    monkeypatch.setattr(sc, "ROW_LIMIT", 2)
    session = FakeSession(rows=2)
    data = sc.fetch(session, sc.windows(MONDAY))
    site = "https://www.googleapis.com/webmasters/v3/sites/sc-domain%3Agoldfishlab.app"
    assert session.calls[0] == (
        "POST",
        f"{site}/searchAnalytics/query",
        {
            "startDate": "2026-10-31",
            "endDate": "2026-11-06",
            "dimensions": [],
            "type": "web",
            "rowLimit": 2,
            "startRow": 0,
        },
    )
    assert session.calls[1][2]["startRow"] == 2  # a full page asks for the next
    assert session.calls[-1] == ("GET", f"{site}/sitemaps", None)
    assert sorted(data) == sorted(name.removesuffix(".json") for name in sc.FIXTURE_FILES)


# --- The key never shows ---------------------------------------------------------


def _key(**changes):
    key = {
        "type": "service_account",
        "project_id": "goldfishlab-seo",
        "private_key_id": PLANTED,
        "private_key": f"-----BEGIN PRIVATE KEY-----\n{PLANTED}\n-----END PRIVATE KEY-----\n",
        "client_email": "review@goldfishlab-seo.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    return json.dumps({**key, **changes})


@pytest.mark.parametrize(
    "value, message",
    [
        ("", "GSC_SERVICE_ACCOUNT_JSON is not set"),
        (_key(), "GSC_SERVICE_ACCOUNT_JSON is not a valid service-account key"),
        (f"{{not json {PLANTED}", "GSC_SERVICE_ACCOUNT_JSON is not a valid service-account key"),
        (_key(client_email=None), "GSC_SERVICE_ACCOUNT_JSON is not a valid service-account key"),
    ],
)
def test_a_bad_key_fails_with_one_sentence_and_never_shows(
    monkeypatch, tmp_path, capsys, value, message
):
    monkeypatch.setenv("GSC_SERVICE_ACCOUNT_JSON", value)
    assert sc.main(["--today", "2026-11-09", "--out", str(tmp_path)]) == 1
    out, err = capsys.readouterr()
    assert err == f"error: {message}\n"
    assert PLANTED not in out + err
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_an_http_error_names_the_status_and_not_the_body():
    session = FakeSession(answer=FakeAnswer(403, {"error": {"message": PLANTED}}))
    with pytest.raises(sc.ReviewError) as caught:
        sc.fetch(session, sc.windows(MONDAY))
    assert str(caught.value) == "Search Console answered HTTP 403"
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_a_failed_token_refresh_names_only_its_kind():
    session = FakeSession(error=RuntimeError(f"invalid_grant {PLANTED}"))
    with pytest.raises(sc.ReviewError) as caught:
        sc.fetch(session, sc.windows(MONDAY))
    assert str(caught.value) == "Search Console could not be reached (RuntimeError)"
    assert caught.value.__suppress_context__


def test_the_script_has_no_print_of_the_environment():
    text = (ROOT / "scripts" / "search_console.py").read_text()
    assert text.count("os.environ") == 1
    assert "print(raw" not in text and "print(info" not in text


# --- Requirements and workflows -----------------------------------------------------


def _workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(job):
    return job.get("steps", [])


def test_the_job_dependencies_are_pinned_and_audited():
    lines = [
        line
        for line in (ROOT / "requirements-seo.txt").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    assert lines and all(re.fullmatch(r"[A-Za-z0-9_.-]+==[\d.]+", line) for line in lines)
    audit = next(
        step["run"]
        for step in _workflow("checks.yml")["jobs"]["test"]["steps"]
        if step.get("name") == "Audit dependencies"
    )
    assert "pip-audit -r requirements-seo.txt" in audit
    assert "-r requirements-seo.txt" in (ROOT / "requirements-dev.txt").read_text()


@pytest.mark.parametrize("name", ["seo-review.yml", "forge-sync.yml"])
def test_the_operator_workflows_are_only_called(name):
    """Run here, their issues, logs and artifacts would be public."""
    triggers = _workflow(name)[True]  # YAML reads the key `on` as True
    assert list(triggers) == ["workflow_call"]


@pytest.mark.parametrize("name", ["seo-review.yml", "forge-sync.yml"])
def test_every_action_is_pinned_to_a_commit(name):
    for job in _workflow(name)["jobs"].values():
        for step in _steps(job):
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]


@pytest.mark.parametrize("name", ["seo-review.yml", "forge-sync.yml"])
def test_no_job_writes_the_repository(name):
    workflow = _workflow(name)
    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert "write" not in job["permissions"].get("contents", "read")
        for step in _steps(job):
            assert "git push" not in step.get("run", "")


@pytest.mark.parametrize(
    "name, writer", [("seo-review.yml", "publish"), ("forge-sync.yml", "publish")]
)
def test_the_writing_job_runs_no_python_and_holds_no_secret(name, writer):
    job = _workflow(name)["jobs"][writer]
    assert job["permissions"] == {"issues": "write"}
    text = yaml.safe_dump(job)
    assert "secrets." not in text
    for step in _steps(job):
        assert not re.search(r"\b(python|pip)\b", step.get("run", ""))
        assert step.get("uses", "actions/").startswith("actions/")


def test_the_key_reaches_one_step_and_nothing_is_installed_there():
    workflow = _workflow("seo-review.yml")
    assert workflow[True]["workflow_call"]["secrets"] == {
        "GSC_SERVICE_ACCOUNT_JSON": {"required": True}
    }
    holders = [
        (job_name, step)
        for job_name, job in workflow["jobs"].items()
        for step in _steps(job)
        if "secrets." in yaml.safe_dump(step)
    ]
    assert [(job, step["name"]) for job, step in holders] == [("review", "Read Search Console")]
    step = holders[0][1]
    assert step["env"] == {"GSC_SERVICE_ACCOUNT_JSON": "${{ secrets.GSC_SERVICE_ACCOUNT_JSON }}"}
    assert step["run"].startswith("python scripts/search_console.py")
    assert workflow["jobs"]["review"]["permissions"] == {"contents": "read"}


def test_the_review_runs_this_repositorys_main():
    checkout = _steps(_workflow("seo-review.yml")["jobs"]["review"])[0]
    assert checkout["with"] == {
        "repository": "Riddmaker/goldfishlab.app",
        "ref": "main",
        "persist-credentials": False,
    }


def test_the_publish_job_validates_the_handed_over_files():
    steps = _steps(_workflow("seo-review.yml")["jobs"]["publish"])
    validate = next(step["run"] for step in steps if step.get("name") == "Validate the report")
    assert '"report.json report.md title.txt "' in validate
    assert "Search\\ Console\\ review\\ [0-9]{4}-W[0-9]{2}$" in validate
    names = [step.get("name") for step in steps]
    assert names.index("Validate the report") < names.index("Open or update this week's issue")


@pytest.mark.parametrize(
    "name, title",
    [("seo-review.yml", "Search Console review failed"), ("forge-sync.yml", "Forge sync failed")],
)
def test_a_failed_run_opens_an_issue(name, title):
    failed = _workflow(name)["jobs"]["failed"]
    assert failed["if"] == "failure()"
    assert failed["permissions"] == {"issues": "write"}
    assert f'title="{title}"' in failed["steps"][0]["run"]
