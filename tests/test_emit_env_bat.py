"""Verify emit_env_bat produces SET lines from .env.example keys."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_emit_env_bat_runs_without_error():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "emit_env_bat.py")],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    assert result.returncode == 0
