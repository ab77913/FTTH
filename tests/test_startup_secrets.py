"""Security regression: startup scripts must not embed API keys."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Patterns that must not appear in version-controlled startup scripts.
FORBIDDEN_IN_STARTUP = (
    "AIzaSy",
    "SMARTY_AUTH_TOKEN=",
    "AZURE_VISION_KEY=",
    "MELISSA_LICENSE_KEY=",
)

STARTUP_SCRIPTS = (
    ROOT / "start_app.bat",
)


def test_start_app_bat_has_no_embedded_api_keys():
    content = (ROOT / "start_app.bat").read_text(encoding="utf-8", errors="replace")
    for pattern in FORBIDDEN_IN_STARTUP:
        assert pattern not in content, f"start_app.bat must not contain {pattern!r}; use .env instead"


def test_emit_env_bat_script_exists():
    assert (ROOT / "scripts" / "emit_env_bat.py").is_file()
