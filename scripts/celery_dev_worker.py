"""Run Celery worker and restart when data_ingestion Python code changes."""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WATCH_DIR = PROJECT_ROOT / "backend" / "data_ingestion"


def _celery_cmd() -> list[str]:
    return [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "data_ingestion.worker.celery_app",
        "worker",
        "--pool=solo",
        "--loglevel=warning",
        f"--logfile={PROJECT_ROOT / 'logs' / 'services' / 'celery_worker.log'}",
        "-n",
        "ftth_pipeline@%h",
    ]


def _start_celery() -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(PROJECT_ROOT), str(PROJECT_ROOT / "backend")])
    (PROJECT_ROOT / "logs" / "services").mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / "logs" / "agents").mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(_celery_cmd(), cwd=str(PROJECT_ROOT), env=env)


def main() -> None:
    try:
        from watchfiles import watch
    except ImportError:
        proc = _start_celery()
        proc.wait()
        return

    proc = _start_celery()
    print(f"Celery worker pid={proc.pid}; watching {WATCH_DIR}")
    try:
        for changes in watch(WATCH_DIR, recursive=True, debounce=500, step=500):
            if proc.poll() is not None:
                break
            if not any(str(path).endswith(".py") for _, path in changes):
                continue
            print("Restarting Celery after code change...")
            proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=15)
            if proc.poll() is None:
                proc.kill()
            proc = _start_celery()
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
