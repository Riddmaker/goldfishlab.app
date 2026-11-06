"""The weekly Search Console review (Paket 5, PR 20): last week against the week before.

    python scripts/search_console.py --out DIR [--fixture DIR] [--today YYYY-MM-DD]

Reads `sc-domain:goldfishlab.app` through the plain REST API with a read-only
service account and writes DIR/report.md (the issue body), DIR/report.json and
DIR/title.txt ("Search Console review 2026-W45", the ISO week of the run). A
few fixed rules pick out what a session should look at; nothing here decides
or changes anything. `.github/workflows/seo-review.yml` runs it every Monday
from the private ops repository, so the numbers never reach the public one.

The key is the JSON of the service account in the environment variable
GSC_SERVICE_ACCOUNT_JSON. It is never printed: every failure names only what
went wrong (a missing key, an HTTP status), never a body or the key.
`--fixture DIR` reads recorded answers instead (FIXTURE_FILES), so the tests
and a dry run need neither the key nor the network.

No Django: the languages come from LANGUAGE_NAMES in the settings file, read
as text, so the job installs only google-auth and requests.
"""

import argparse
import ast
import datetime as dt
import json
import os
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "goldfishlab" / "settings" / "base.py"

SITE = "sc-domain:goldfishlab.app"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
API = "https://www.googleapis.com/webmasters/v3/sites/"
KEY_VARIABLE = "GSC_SERVICE_ACCOUNT_JSON"

#: Search Console's numbers settle two to three days late.
LAG_DAYS = 3
WEEK_DAYS = 7
#: The API's largest page of rows.
ROW_LIMIT = 25000
TOP = 10
#: "Almost page one": a mean position from 8 to 20, seen often enough to count.
ALMOST_FROM, ALMOST_TO = 8, 20
MIN_IMPRESSIONS = 20
#: Low CTR: on the first five places, yet fewer than 2 % click.
LOW_CTR_POSITION = 5
LOW_CTR = 0.02

#: The recorded answers --fixture reads: one file per question and week.
FIXTURE_FILES = [
    f"{week}-{kind}.json" for week in ("this", "last") for kind in ("total", "query", "page")
] + ["sitemaps.json"]


class ReviewError(Exception):
    """A failure whose message is safe to print: no key, no answer body."""


# --- Windows and languages ---------------------------------------------------


def windows(today: dt.date) -> dict[str, tuple[dt.date, dt.date]]:
    """The two weeks compared: seven days ending LAG_DAYS before today, and
    the seven before them. Both ends inclusive, as the API counts them."""
    end = today - dt.timedelta(days=LAG_DAYS)
    start = end - dt.timedelta(days=WEEK_DAYS - 1)
    last_end = start - dt.timedelta(days=1)
    return {"this": (start, end), "last": (last_end - dt.timedelta(days=WEEK_DAYS - 1), last_end)}


def title(today: dt.date) -> str:
    year, week, _ = today.isocalendar()
    return f"Search Console review {year}-W{week:02d}"


def language_codes(settings_file: Path = SETTINGS) -> list[str]:
    """The site's language codes, from LANGUAGE_NAMES in the settings file.
    English has no URL prefix; every other language is /<code>/."""
    tree = ast.parse(settings_file.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "LANGUAGE_NAMES"
            for target in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise ReviewError(f"no LANGUAGE_NAMES in {settings_file.name}")


def language_of(page: str, codes: list[str]) -> str:
    first = urllib.parse.urlsplit(page).path.strip("/").split("/")[0].lower()
    return first if first in codes and first != "en" else "en"


# --- Reading Search Console --------------------------------------------------


def _session():
    """An authorised requests session. Every error of the key is reported as
    one fixed sentence: the libraries' own messages may quote the key."""
    raw = os.environ.get(KEY_VARIABLE, "")
    if not raw.strip():
        raise ReviewError(f"{KEY_VARIABLE} is not set")
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        info = json.loads(raw)
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
    except ImportError:
        raise ReviewError(
            "google-auth is not installed: pip install -r requirements-seo.txt"
        ) from None
    except Exception:  # noqa: BLE001 - the message could carry the key
        raise ReviewError(f"{KEY_VARIABLE} is not a valid service-account key") from None
    return AuthorizedSession(credentials)


def _call(session, method: str, url: str, body: dict | None = None) -> dict:
    try:
        answer = session.request(method, url, json=body, timeout=60)
    except Exception as error:  # noqa: BLE001 - a token refresh error quotes Google's answer
        raise ReviewError(f"Search Console could not be reached ({type(error).__name__})") from None
    if answer.status_code != 200:
        raise ReviewError(f"Search Console answered HTTP {answer.status_code}")
    return answer.json()


def fetch(session, weeks: dict) -> dict:
    """Every answer the report needs, keyed like FIXTURE_FILES."""
    site = API + urllib.parse.quote(SITE, safe="")
    data = {}
    for week, (start, end) in weeks.items():
        for kind, dimensions in (("total", []), ("query", ["query"]), ("page", ["page"])):
            rows, start_row = [], 0
            while True:
                body = {
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "dimensions": dimensions,
                    "type": "web",
                    "rowLimit": ROW_LIMIT,
                    "startRow": start_row,
                }
                page = _call(session, "POST", f"{site}/searchAnalytics/query", body).get("rows", [])
                rows += page
                if len(page) < ROW_LIMIT:
                    break
                start_row += ROW_LIMIT
            data[f"{week}-{kind}"] = {"rows": rows}
    data["sitemaps"] = _call(session, "GET", f"{site}/sitemaps")
    return data


def read_fixture(folder: Path) -> dict:
    return {
        name.removesuffix(".json"): json.loads((folder / name).read_text(encoding="utf-8"))
        for name in FIXTURE_FILES
    }


# --- The rules ---------------------------------------------------------------


def _rows(answer: dict) -> dict[str, dict]:
    """Rows by their key, numbers as floats; Google sends them as doubles."""
    out = {}
    for row in answer.get("rows", []):
        key = row["keys"][0] if row.get("keys") else ""
        out[key] = {
            name: float(row.get(name, 0)) for name in ("clicks", "impressions", "ctr", "position")
        }
    return out


def _change(now: float, before: float) -> dict:
    """The change week over week; no percentage when there was nothing before."""
    return {
        "now": now,
        "before": before,
        "delta": now - before,
        "percent": (now - before) / before * 100 if before else None,
    }


def totals(this: dict, last: dict) -> dict:
    now = _rows(this).get("", {})
    before = _rows(last).get("", {})
    return {
        name: _change(now.get(name, 0.0), before.get(name, 0.0))
        for name in ("clicks", "impressions", "ctr", "position")
    }


def top(rows: dict[str, dict], count: int = TOP) -> list[dict]:
    ranked = sorted(
        rows.items(), key=lambda item: (-item[1]["clicks"], -item[1]["impressions"], item[0])
    )
    return [{"key": key, **numbers} for key, numbers in ranked[:count]]


def appeared(this: dict[str, dict], last: dict[str, dict]) -> list[dict]:
    """Shown this week, not at all the week before; most impressions first."""
    found = [
        {"key": key, **numbers}
        for key, numbers in this.items()
        if numbers["impressions"] > 0 and key not in last
    ]
    return sorted(found, key=lambda row: (-row["impressions"], row["key"]))


def almost_page_one(rows: dict[str, dict]) -> list[dict]:
    found = [
        {"key": key, **numbers}
        for key, numbers in rows.items()
        if ALMOST_FROM <= numbers["position"] <= ALMOST_TO
        and numbers["impressions"] >= MIN_IMPRESSIONS
    ]
    return sorted(found, key=lambda row: (-row["impressions"], row["key"]))


def low_ctr(rows: dict[str, dict]) -> list[dict]:
    found = [
        {"key": key, **numbers}
        for key, numbers in rows.items()
        if numbers["position"] <= LOW_CTR_POSITION
        and numbers["ctr"] < LOW_CTR
        and numbers["impressions"] >= MIN_IMPRESSIONS
    ]
    return sorted(found, key=lambda row: (-row["impressions"], row["key"]))


def languages(this: dict[str, dict], last: dict[str, dict], codes: list[str]) -> list[dict]:
    """Clicks and impressions per language, summed over the pages."""
    sums = {code: {"clicks": [0.0, 0.0], "impressions": [0.0, 0.0]} for code in codes}
    for index, rows in enumerate((this, last)):
        for page, numbers in rows.items():
            code = language_of(page, codes)
            for name in ("clicks", "impressions"):
                sums[code][name][index] += numbers[name]
    return [
        {"language": code, **{name: _change(*pair) for name, pair in values.items()}}
        for code, values in sums.items()
    ]


def sitemaps(answer: dict) -> list[dict]:
    """Each sitemap's last read, errors and warnings (longs, sent as strings)."""
    return [
        {
            "path": entry.get("path", ""),
            "last_downloaded": entry.get("lastDownloaded"),
            "pending": bool(entry.get("isPending")),
            "errors": int(entry.get("errors", 0)),
            "warnings": int(entry.get("warnings", 0)),
        }
        for entry in answer.get("sitemap", [])
    ]


def build(data: dict, weeks: dict, codes: list[str], today: dt.date) -> dict:
    rows = {name: _rows(answer) for name, answer in data.items() if name != "sitemaps"}
    return {
        "title": title(today),
        "site": SITE,
        "weeks": {
            week: [start.isoformat(), end.isoformat()] for week, (start, end) in weeks.items()
        },
        "has_data": bool(
            rows["this-query"]
            or rows["this-page"]
            or rows["this-total"].get("", {}).get("impressions")
        ),
        "totals": totals(data["this-total"], data["last-total"]),
        "top_queries": top(rows["this-query"]),
        "top_pages": top(rows["this-page"]),
        "new_queries": appeared(rows["this-query"], rows["last-query"]),
        "gone_queries": appeared(rows["last-query"], rows["this-query"]),
        "almost_queries": almost_page_one(rows["this-query"]),
        "almost_pages": almost_page_one(rows["this-page"]),
        "low_ctr_queries": low_ctr(rows["this-query"]),
        "low_ctr_pages": low_ctr(rows["this-page"]),
        "languages": languages(rows["this-page"], rows["last-page"], codes),
        "sitemaps": sitemaps(data["sitemaps"]),
    }


# --- Markdown ----------------------------------------------------------------


def literal(text: str) -> str:
    """``text`` as a Markdown code span. Queries are typed by strangers: no
    link, mention, table cell or formatting in one may render."""
    return "`" + " ".join(str(text).replace("`", "'").replace("|", "/").split())[:200] + "`"


def _number(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}"


def _signed(change: dict, digits: int = 0, unit: str = "") -> str:
    text = f"{change['delta']:+,.{digits}f}{unit}"
    return text + (f" ({change['percent']:+.0f} %)" if change["percent"] is not None else "")


def _table(rows: list[dict], heading: str, more: int = 0) -> list[str]:
    lines = [f"| {heading} | Clicks | Impressions | CTR | Position |", "|---|--:|--:|--:|--:|"]
    lines += [
        f"| {literal(row['key'])} | {_number(row['clicks'])} | {_number(row['impressions'])} "
        f"| {row['ctr'] * 100:.1f} % | {row['position']:.1f} |"
        for row in rows
    ]
    return lines + (["", f"And {more} more."] if more > 0 else [])


def _section(name: str, rows: list[dict], heading: str, empty: str) -> list[str]:
    lines = [f"### {name}", ""]
    if not rows:
        return lines + [empty, ""]
    return lines + _table(rows[:TOP], heading, len(rows) - TOP) + [""]


def markdown(report: dict) -> str:
    this, last = report["weeks"]["this"], report["weeks"]["last"]
    lines = [
        f"Property {literal(report['site'])}: {this[0]} to {this[1]}, "
        f"compared with {last[0]} to {last[1]}.",
        "",
    ]
    if not report["has_data"]:
        lines += [
            "**No data for this week yet.** A new property needs a few days before "
            "Search Console reports anything.",
            "",
        ]
    t = report["totals"]
    lines += [
        "## Totals",
        "",
        "| | This week | Change |",
        "|---|--:|--:|",
        f"| Clicks | {_number(t['clicks']['now'])} | {_signed(t['clicks'])} |",
        f"| Impressions | {_number(t['impressions']['now'])} | {_signed(t['impressions'])} |",
        f"| CTR | {t['ctr']['now'] * 100:.1f} % | {t['ctr']['delta'] * 100:+.1f} points |",
        # A smaller position is better: the change is shown as it is, not as a gain.
        f"| Mean position | {t['position']['now']:.1f} | {t['position']['delta']:+.1f} |",
        "",
        "## Findings",
        "",
    ]
    lines += _section("New queries", report["new_queries"], "Query", "None.")
    lines += _section("Queries gone", report["gone_queries"], "Query (last week)", "None.")
    lines += _section(
        f"Almost page one: queries (position {ALMOST_FROM} to {ALMOST_TO}, "
        f"{MIN_IMPRESSIONS}+ impressions)",
        report["almost_queries"],
        "Query",
        "None.",
    )
    lines += _section("Almost page one: pages", report["almost_pages"], "Page", "None.")
    lines += _section(
        f"Low CTR: queries (position {LOW_CTR_POSITION} or better, under {LOW_CTR * 100:.0f} %, "
        f"{MIN_IMPRESSIONS}+ impressions)",
        report["low_ctr_queries"],
        "Query",
        "None.",
    )
    lines += _section("Low CTR: pages", report["low_ctr_pages"], "Page", "None.")
    lines += [
        "## Languages",
        "",
        "| Language | Clicks | Change | Impressions | Change |",
        "|---|--:|--:|--:|--:|",
    ]
    lines += [
        f"| {row['language']} | {_number(row['clicks']['now'])} | {_signed(row['clicks'])} "
        f"| {_number(row['impressions']['now'])} | {_signed(row['impressions'])} |"
        for row in report["languages"]
    ]
    lines += ["", "## Sitemaps", ""]
    if report["sitemaps"]:
        lines += ["| Sitemap | Last read | Errors | Warnings |", "|---|---|--:|--:|"]
        lines += [
            f"| {literal(entry['path'])} | {entry['last_downloaded'] or 'never'}"
            f"{' (pending)' if entry['pending'] else ''} "
            f"| {entry['errors']} | {entry['warnings']} |"
            for entry in report["sitemaps"]
        ]
    else:
        lines += ["**No sitemap is submitted.**"]
    lines += ["", "## Top 10 this week", ""]
    lines += _section("Queries", report["top_queries"], "Query", "None.")
    lines += _section("Pages", report["top_pages"], "Page", "None.")
    lines += [
        "---",
        "",
        f"Search Console's numbers are {LAG_DAYS - 1} to {LAG_DAYS} days late, so the week "
        f"ends {LAG_DAYS} days before the run. Rare queries are left out of the query rows "
        "for privacy; the totals count them, so the queries add up to less. The languages "
        "sum the page rows, which count a little differently from the totals.",
        "",
        "Next: a session turns the findings into actions and notes them in "
        "docs/SEQUENCE-SUPPORT-KPI.html.",
    ]
    return "\n".join(lines) + "\n"


# --- The command -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--out", type=Path, required=True, help="folder for report.md, report.json, title.txt"
    )
    parser.add_argument("--fixture", type=Path, help="read recorded answers from this folder")
    parser.add_argument("--today", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args(argv)
    try:
        weeks = windows(args.today)
        data = read_fixture(args.fixture) if args.fixture else fetch(_session(), weeks)
        report = build(data, weeks, language_codes(), args.today)
    except ReviewError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.md").write_text(markdown(report), encoding="utf-8")
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (args.out / "title.txt").write_text(report["title"] + "\n", encoding="utf-8")
    print(report["title"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
