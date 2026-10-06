"""A crowd of guests against a local stack (P1, load protection).

Usage:  python scripts/loadtest.py [--guests 40] [--base http://127.0.0.1:8000]

Each virtual guest does what a visitor from a Reddit post does: opens the trial
page, pastes a deck, lands on its run and waits for the report. The script
counts how many runs started, how many were turned away with the friendly
"a lot of people" message, and how long the uploads and the runs took.

Run it against the local stack only - gunicorn with the production's two
workers, a short worker at concurrency 1 and a long one at 2 (docker-compose.yml
shows the commands). Every guest sends its own X-Forwarded-For address, which
the site believes because DJANGO_TRUSTED_PROXY_COUNT defaults to one proxy;
otherwise the per-address limit on new guests (5 in 5 minutes) would turn the
crowd away before it reached the workers. In production Cloudflare sets that
header, so this script cannot get past the limit there.

Standard library only, so it runs from any checkout without installing more.
"""

import argparse
import http.cookiejar
import re
import statistics
import threading
import time
import urllib.parse
import urllib.request

DECK = "// Commander\n1 Chainer, Dementia Master\n// Deck\n1 Sol Ring\n36 Swamp\n"
REFUSED = "A lot of people are trying Goldfish Lab right now"
RUN_PATH = re.compile(r"/runs/([0-9a-f-]{36})/")
#: How long one guest waits for its report before it counts as unfinished.
PATIENCE_SECONDS = 600


class Guest(threading.Thread):
    """One visitor: its own cookies, its own address."""

    def __init__(self, number: int, base: str, results: list, lock: threading.Lock):
        super().__init__(daemon=True)
        self.base = base
        # Documentation ranges (RFC 5737): never a real visitor's address.
        network = "203.0.113" if number < 250 else "198.51.100"
        self.address = f"{network}.{number % 250 + 1}"
        self.results = results
        self.lock = lock
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, path: str, data: dict | None = None):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        # main() restricts the base to http(s), which is what S310 asks for.
        request = urllib.request.Request(  # noqa: S310
            urllib.parse.urljoin(self.base, path), data=body,
            headers={"X-Forwarded-For": self.address, "Referer": self.base + "/try/"})
        return self.opener.open(request, timeout=60)

    def run(self):
        outcome = {"upload_s": None, "report_s": None, "result": "error"}
        try:
            page = self._open("/try/").read().decode()
            token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page).group(1)
            started = time.monotonic()
            response = self._open("/try/", {"csrfmiddlewaretoken": token,
                                             "source": "paste", "text": DECK})
            landed = response.geturl()
            text = response.read().decode()
            outcome["upload_s"] = time.monotonic() - started
            match = RUN_PATH.search(urllib.parse.urlparse(landed).path)
            if match is None:
                outcome["result"] = "refused" if REFUSED in text else "no run"
            else:
                outcome["result"] = self._wait(match.group(1), started)
                outcome["report_s"] = time.monotonic() - started
        except Exception as exc:  # noqa: BLE001 - every failure is a result here
            outcome["result"] = f"error: {type(exc).__name__}"
        with self.lock:
            self.results.append(outcome)

    def _wait(self, run_id: str, started: float) -> str:
        while time.monotonic() - started < PATIENCE_SECONDS:
            fragment = self._open(f"/runs/{run_id}/progress/").read().decode()
            if "hx-trigger" not in fragment:
                return "finished"
            time.sleep(2)
        return "unfinished"


def _seconds(values: list[float]) -> str:
    if not values:
        return "-"
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    return f"median {statistics.median(ordered):.1f} s, 95th {p95:.1f} s, max {ordered[-1]:.1f} s"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--guests", type=int, default=40)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--spread", type=float, default=10.0,
                        help="seconds over which the guests arrive")
    args = parser.parse_args()
    if urllib.parse.urlparse(args.base).scheme not in ("http", "https"):
        parser.error("--base must be an http(s) address")

    results: list[dict] = []
    lock = threading.Lock()
    crowd = [Guest(n, args.base, results, lock) for n in range(args.guests)]
    began = time.monotonic()
    for guest in crowd:
        guest.start()
        time.sleep(args.spread / max(1, args.guests))
    for guest in crowd:
        guest.join()

    tally: dict[str, int] = {}
    for outcome in results:
        tally[outcome["result"]] = tally.get(outcome["result"], 0) + 1
    print(f"{args.guests} guests in {time.monotonic() - began:.0f} s: {tally}")
    print("upload to run page:", _seconds([r["upload_s"] for r in results if r["upload_s"]]))
    print("upload to report:  ", _seconds([r["report_s"] for r in results
                                          if r["result"] == "finished"]))


if __name__ == "__main__":
    main()
