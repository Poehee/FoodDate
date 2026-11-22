"""Create a local .env with random secrets, without overwriting an existing one."""

import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    values = {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
        "DJANGO_DEBUG": "true",
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
    }
    lines = []
    for line in (ROOT / ".env.example").read_text().splitlines():
        if line.startswith("#"):
            continue
        key, _, default = line.partition("=")
        lines.append(f"{key}={values.get(key, default)}\n")

    try:
        descriptor = os.open(ROOT / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        sys.exit(".env already exists, left unchanged.")
    with os.fdopen(descriptor, "w") as file:
        file.writelines(lines)
    print("Created .env")


if __name__ == "__main__":
    main()
