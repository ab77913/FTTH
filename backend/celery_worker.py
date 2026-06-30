"""
Celery worker entry-point for Windows.

Run from repo root:
    python celery_worker.py

Equivalent to:
    celery -A data_ingestion.worker.celery_app worker --pool=solo --loglevel=info --concurrency=1
"""
from __future__ import annotations

import os
import sys

_root = os.path.dirname(os.path.abspath(__file__))
_project = os.path.dirname(_root)
for _p in (_root, _project):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_ingestion.worker.celery_app import celery_app


def main() -> None:
    celery_app.worker_main(
        argv=[
            "worker",
            "--pool=solo",
            "--loglevel=info",
            "--concurrency=1",
            "--hostname=ftth-worker@%h",
        ]
    )


if __name__ == "__main__":
    main()
