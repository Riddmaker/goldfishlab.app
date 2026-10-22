"""P22 phone test: run the engine in a phone's browser through Pyodide.

    python scripts/p22_phone_test/serve.py [port]

The first start builds the site in .data/p22-site/: it downloads the Pyodide
core (about 13 MB, once) and packs `simulation/` from this checkout. Then it
serves that folder on the local network and prints the address to open on a
phone in the same Wi-Fi. Stop it with Ctrl+C. Only that folder is served.

A tool for the P22 spike, kept on its own branch: never merged.
"""

import functools
import http.server
import shutil
import socket
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SITE = ROOT / ".data" / "p22-site"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

PYODIDE = "https://cdn.jsdelivr.net/npm/pyodide@314.0.7/"
PYODIDE_FILES = ("pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm",
                 "python_stdlib.zip", "pyodide-lock.json")
PAGE_FILES = ("engine-phone-test.html", "bench.py", "deck.json")


def build() -> None:
    """Put the page, Pyodide and the engine into `SITE`."""
    (SITE / "pyodide").mkdir(parents=True, exist_ok=True)
    for name in PAGE_FILES:
        shutil.copyfile(HERE / name, SITE / name)
    for name in PYODIDE_FILES:
        target = SITE / "pyodide" / name
        if not target.exists():
            print(f"Downloading {name} ...")
            # A fixed https URL, never user input.
            with urllib.request.urlopen(PYODIDE + name) as response:  # noqa: S310
                target.write_bytes(response.read())
    with zipfile.ZipFile(SITE / "engine.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "simulation").rglob("*.py")):
            archive.write(path, path.relative_to(ROOT).as_posix())


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
        ".mjs": "text/javascript",
        ".js": "text/javascript",
        ".json": "application/json",
        ".zip": "application/zip",
        ".py": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }


def lan_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # no packet is sent; picks the outgoing interface
            return probe.getsockname()[0]
    except OSError:
        return "<this-pc-ip>"


if __name__ == "__main__":
    build()
    handler = functools.partial(Handler, directory=str(SITE))
    # All interfaces on purpose: the phone reaches this PC over the Wi-Fi.
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), handler) as server:  # noqa: S104
        print(f"On the phone, open: http://{lan_address()}:{PORT}/engine-phone-test.html")
        server.serve_forever()
