"""Root entrypoint shim — implementation lives in backend/api/server.py."""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
for _path in (_root / "backend", _root):
    _entry = str(_path)
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from backend.api import server as _server

for _name in dir(_server):
    if _name.startswith("__") and _name not in ("__doc__",):
        continue
    globals()[_name] = getattr(_server, _name)

app = _server.app


def run_server() -> None:
    _server.run_server()


if __name__ == "__main__":
    run_server()
