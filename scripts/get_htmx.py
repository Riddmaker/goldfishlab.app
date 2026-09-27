"""Fetch the pinned htmx build into static/js/.

Usage:  py -3.13 scripts/get_htmx.py

Unlike the Tailwind binary, the fetched file **is committed**: it is served to
every visitor, so the repository has to contain the exact bytes that ship
rather than whatever a CDN returns on the day of a deploy. This script exists
so that upgrading is a deliberate act with a verified digest, not a download.

Self-hosted rather than loaded from a CDN, for three reasons that all matter
more than the saved kilobytes: the page keeps working when the CDN does not,
no third party learns who visits the site, and there is no way for a
compromised CDN to run its own JavaScript on a signed-in user's page.

Pinned to 2.0.10 (see docs/phases/phase-5-playtest.md). Bump VERSION and
SHA256 together; the digest is what makes the pin mean anything.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

VERSION = "2.0.10"
SHA256 = "71ea67185bfa8c98c39d31717c6fce5d852370fcdfd129db4543774d3145c0de"
URL = f"https://unpkg.com/htmx.org@{VERSION}/dist/htmx.min.js"
TARGET = Path(__file__).resolve().parent.parent / "static" / "js" / "htmx.min.js"


def main() -> int:
    print(f"downloading htmx {VERSION}")
    with urllib.request.urlopen(URL) as response:  # noqa: S310 - pinned https URL
        payload = response.read()

    digest = hashlib.sha256(payload).hexdigest()
    if digest != SHA256:
        print(f"DIGEST MISMATCH\n  expected {SHA256}\n  got      {digest}")
        return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(payload)
    print(f"wrote {TARGET} ({len(payload):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
