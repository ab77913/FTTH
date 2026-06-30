"""Print expected log files and whether they exist (for manual testing)."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from data_ingestion.config.log_paths import all_expected_logs, ensure_log_dirs  # noqa: E402


def main() -> int:
    ensure_log_dirs()
    expected = all_expected_logs()
    present = 0
    missing = 0
    print("FTTH log file status\n" + "=" * 60)
    for key, path in sorted(expected.items()):
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        status = f"OK ({size:,} bytes)" if exists else "missing"
        print(f"  [{status:>18}] {key:40} {path}")
        if exists:
            present += 1
        else:
            missing += 1
    print("=" * 60)
    agent_missing = sum(1 for k in expected if k.startswith("agent:") and not expected[k].exists())
    service_missing = sum(1 for k in expected if k.startswith("service:") and not expected[k].exists())
    print(f"Present: {present}  Missing: {missing}  (services missing: {service_missing}, agents missing: {agent_missing})")
    print("\nAgent logs appear after you click Process on a project.")
    print("Service logs appear after restart via start_app.bat.")
    return 1 if service_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
