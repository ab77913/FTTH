"""Production readiness audit for the FTTH project.

Run from the repository root:
    python scripts/production_readiness_check.py

This script is intentionally dependency-light so it can run before deploys, before
publishing a private repo, or inside CI without starting the full stack.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

SECRET_KEY_HINTS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "API_KEY",
    "AUTH",
    "PRIVATE",
)

REQUIRED_FILES = (
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    "pyproject.toml",
    "frontend/public/index.html",
    "frontend/src/styles/app.css",
    "frontend/src/styles/map.css",
    "backend/api/server.py",
    "backend/data_ingestion/config/startup_checks.py",
    "backend/data_ingestion/health.py",
    "tests/test_api_contracts.py",
    "tests/test_static_frontend.py",
)

RECOMMENDED_DOCS = (
    "docs/SECURITY.md",
    "docs/DEPLOYMENT.md",
    "docs/RUNBOOK.md",
    "docs/ARCHITECTURE.md",
)

INSECURE_VALUES = {
    "",
    "change-me",
    "change-me-in-production",
    "change-me-in-production-please-use-env-var",
    "changeme",
    "password",
    "admin",
    "ftth",
    "secret",
}


@dataclass
class Finding:
    severity: str
    check: str
    message: str


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _is_secret_key(key: str) -> bool:
    upper = key.upper()
    return any(hint in upper for hint in SECRET_KEY_HINTS)


def _has_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in INSECURE_VALUES:
        return True
    return bool(re.search(r"(your|example|dummy|sample|placeholder|todo)", normalized))


def _check_files(findings: list[Finding]) -> None:
    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            findings.append(Finding("critical", "required-file", f"Missing required file: {rel}"))
    for rel in RECOMMENDED_DOCS:
        if not (ROOT / rel).is_file():
            findings.append(Finding("warning", "recommended-doc", f"Recommended doc is missing: {rel}"))


def _check_env(findings: list[Finding], *, include_real_env: bool) -> None:
    example = _load_env(ROOT / ".env.example")
    real = _load_env(ROOT / ".env") if include_real_env else {}

    if not example:
        findings.append(Finding("critical", "env-example", ".env.example is missing or empty"))
    for required in ("DATABASE_URL", "JWT_SECRET_KEY", "FTTH_ENV"):
        if required not in example:
            findings.append(Finding("critical", "env-example", f".env.example missing {required}"))

    candidate_envs = [(".env.example", example)]
    if include_real_env:
        if not (ROOT / ".env").is_file():
            findings.append(Finding("warning", "env", ".env not found; deploy target must provide runtime secrets"))
        candidate_envs.append((".env", real))

    for label, values in candidate_envs:
        for key, value in values.items():
            if _is_secret_key(key) and _has_placeholder(value):
                severity = "warning" if label == ".env.example" else "critical"
                findings.append(Finding(severity, "secret-placeholder", f"{label} has placeholder or weak value for {key}"))

    production_env = (real.get("FTTH_ENV") or os.environ.get("FTTH_ENV") or "").strip().lower()
    if production_env in {"production", "prod"}:
        jwt = real.get("JWT_SECRET_KEY") or os.environ.get("JWT_SECRET_KEY", "")
        if len(jwt) < 32 or _has_placeholder(jwt):
            findings.append(Finding("critical", "jwt", "Production JWT_SECRET_KEY must be long and non-placeholder"))
        cors = real.get("FTTH_CORS_ORIGINS") or os.environ.get("FTTH_CORS_ORIGINS", "")
        if not cors or "*" in cors:
            findings.append(Finding("critical", "cors", "Production FTTH_CORS_ORIGINS must be an explicit allowlist"))
        database_url = real.get("DATABASE_URL") or os.environ.get("DATABASE_URL", "")
        parsed = urlparse(database_url)
        if (parsed.password or "") in INSECURE_VALUES:
            findings.append(Finding("critical", "database", "Production database password is missing or weak"))


def _check_frontend_contract(findings: list[Finding]) -> None:
    index = ROOT / "frontend" / "public" / "index.html"
    if not index.is_file():
        return
    html = index.read_text(encoding="utf-8", errors="replace")
    expected = (
        "/assets/styles/app.css",
        "/assets/styles/map.css",
        "/assets/components/map-search-panel.jsx",
        "/assets/components/map-selected-panel.jsx",
        "/assets/pages/map-page.jsx",
    )
    for token in expected:
        if token not in html:
            findings.append(Finding("critical", "frontend-assets", f"index.html does not load {token}"))


def _check_openapi_contract(findings: list[Finding]) -> None:
    server = ROOT / "backend" / "api" / "server.py"
    if not server.is_file():
        return
    text = server.read_text(encoding="utf-8", errors="replace")
    for token in ("docs_url=\"/docs\"", "redoc_url=\"/redoc\"", "openapi_url=\"/openapi.json\"", "configure_openapi_documentation(app)"):
        if token not in text:
            findings.append(Finding("critical", "openapi", f"Swagger/OpenAPI configuration token missing: {token}"))


def run_audit(*, include_real_env: bool) -> list[Finding]:
    findings: list[Finding] = []
    _check_files(findings)
    _check_env(findings, include_real_env=include_real_env)
    _check_frontend_contract(findings)
    _check_openapi_contract(findings)
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit FTTH production readiness.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output.")
    parser.add_argument("--include-real-env", action="store_true", help="Also inspect .env for weak placeholders. Avoid in shared logs.")
    args = parser.parse_args()

    findings = run_audit(include_real_env=args.include_real_env)
    summary = {
        "critical": sum(1 for item in findings if item.severity == "critical"),
        "warning": sum(1 for item in findings if item.severity == "warning"),
        "findings": [asdict(item) for item in findings],
    }

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print("FTTH production readiness audit")
        print(f"critical={summary['critical']} warning={summary['warning']}")
        for item in findings:
            print(f"[{item.severity.upper()}] {item.check}: {item.message}")
        if not findings:
            print("No findings.")

    return 1 if summary["critical"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
