"""Create a local .env from .env.example with generated secrets.

Usage:  python scripts/bootstrap_env.py

Secrets are generated and written straight to the file. They are never printed
to stdout, so they cannot leak into a terminal transcript or an agent's
context window (HABIT 1).

Existing .env files are left alone; delete it first to regenerate.
"""

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"

PLACEHOLDER = "replace-me"


def main() -> int:
    if TARGET.exists():
        print(f"{TARGET.name} already exists - leaving it untouched.")
        return 0
    if not EXAMPLE.is_file():
        print(f"Missing {EXAMPLE}", file=sys.stderr)
        return 1

    secret_key = secrets.token_urlsafe(64)
    db_password = secrets.token_urlsafe(24)

    lines = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith("DJANGO_SECRET_KEY="):
            line = f"DJANGO_SECRET_KEY={secret_key}"
        elif line.startswith("POSTGRES_PASSWORD="):
            line = f"POSTGRES_PASSWORD={db_password}"
        elif line.startswith("DATABASE_URL="):
            line = (
                f"DATABASE_URL=postgres://goldfishlab:{db_password}"
                "@127.0.0.1:5432/goldfishlab"
            )
        lines.append(line)

    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {TARGET.name} with generated secrets (not shown here).")
    print("For docker compose, change the DATABASE_URL host from 127.0.0.1 to 'db'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
