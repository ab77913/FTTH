"""Emit Windows batch SET lines from .env for startup scripts.

Usage (from cmd):
  .venv\\Scripts\\python.exe scripts\\emit_env_bat.py > .env.runtime.bat
  call .env.runtime.bat
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if not env_path.is_file():
        return 0
    try:
        from dotenv import dotenv_values
    except ImportError:
        print("REM python-dotenv not installed; skip .env load", file=sys.stderr)
        return 1

    values = dotenv_values(env_path)
    for key, value in values.items():
        if not key or value is None:
            continue
        name = str(key).strip()
        if not name or name.startswith("#"):
            continue
        val = str(value).replace('"', '""')
        print(f'set "{name}={val}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

