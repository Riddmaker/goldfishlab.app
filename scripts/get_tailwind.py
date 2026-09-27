"""Download the pinned Tailwind CSS standalone binary into .bin/ (gitignored).

Usage:  python scripts/get_tailwind.py
Then:   .bin/tailwindcss -i assets/css/input.css -o static/css/main.css --watch

The version and sha256 digests are pinned; bump both together when upgrading
(digests are published on the GitHub release page). The Docker image build
downloads its own (linux-x64) binary — see Dockerfile.
"""

import hashlib
import platform
import stat
import sys
import urllib.request
from pathlib import Path

VERSION = "v4.3.3"

# (asset name, sha256) per platform key
ASSETS = {
    ("Windows", "AMD64"): (
        "tailwindcss-windows-x64.exe",
        "e0e260ce048014e9268f6237ff18f8ccf02cef521cbd0ae04e82c2cdf7aa3955",
    ),
    ("Linux", "x86_64"): (
        "tailwindcss-linux-x64",
        "dc61b3ac6b8c9ca874c0cc4c57b2409791a64c5540404ca5f5367360babc313a",
    ),
    ("Linux", "aarch64"): (
        "tailwindcss-linux-arm64",
        "55fd0b241214eff3de1e8ee4f22796662f2d2e7a49bcfca7477cfd0bac398195",
    ),
    ("Darwin", "arm64"): (
        "tailwindcss-macos-arm64",
        "cdf646702987a743464dff4d9c60fd4480d1c1e73dd819a9a67f1078815dce9d",
    ),
    ("Darwin", "x86_64"): (
        "tailwindcss-macos-x64",
        "7922e0953f2110c05976e3bf58f14e643d90427575e766b7d433f5f80cbee7e1",
    ),
}


def main() -> int:
    key = (platform.system(), platform.machine())
    if key not in ASSETS:
        print(f"Unsupported platform: {key}", file=sys.stderr)
        return 1
    asset, digest = ASSETS[key]

    target = Path(__file__).resolve().parent.parent / ".bin"
    target.mkdir(exist_ok=True)
    dest = target / ("tailwindcss.exe" if asset.endswith(".exe") else "tailwindcss")

    if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == digest:
        print(f"{dest} already up to date ({VERSION})")
        return 0

    url = (
        "https://github.com/tailwindlabs/tailwindcss/releases/download/"
        f"{VERSION}/{asset}"
    )
    print(f"Downloading {url} …")
    data = urllib.request.urlopen(url).read()  # noqa: S310 — pinned https URL

    actual = hashlib.sha256(data).hexdigest()
    if actual != digest:
        print(f"sha256 mismatch: expected {digest}, got {actual}", file=sys.stderr)
        return 1

    dest.write_bytes(data)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"OK -> {dest} ({VERSION}, sha256 verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
