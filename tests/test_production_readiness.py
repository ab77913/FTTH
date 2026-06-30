"""Production readiness audit tests."""

from scripts.production_readiness_check import run_audit


def test_production_readiness_audit_runs_without_real_env():
    findings = run_audit(include_real_env=False)
    assert isinstance(findings, list)
    assert not [item for item in findings if item.severity == "critical"]


def test_production_readiness_checks_openapi_and_frontend_assets():
    findings = run_audit(include_real_env=False)
    messages = "\n".join(item.message for item in findings)
    assert "Swagger/OpenAPI" not in messages
    assert "index.html does not load" not in messages