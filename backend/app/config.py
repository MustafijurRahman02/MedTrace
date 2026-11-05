"""Tiny .env loader (no extra dependency). Reads <project>/.env once and never overrides real environment variables.

Format:  KEY=value   or   KEY="value with # and spaces"   (quotes are removed; lines starting with # are ignored)
Set MEDTRACE_NO_DOTENV=1 to skip loading (the test-suite does this so tests can never touch your Azure database).
"""
import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    if os.environ.get("MEDTRACE_NO_DOTENV") == "1" or not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if val[:1] in ("'", '"') and val.count(val[0]) >= 2:      # quoted: keep everything inside the quotes
            val = val[1:val.index(val[0], 1)]
        elif " #" in val:                                        # unquoted: allow a trailing " # comment"
            val = val.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, val)


load_env()
