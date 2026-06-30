"""Root Celery worker shim — implementation in backend/celery_worker.py."""
from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
for _path in (_root / "backend", _root):
    _entry = str(_path)
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from backend.celery_worker import main

if __name__ == "__main__":
    main()
