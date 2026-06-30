"""Python version compatibility contract checks."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_project_declares_python_310_or_newer():
    pyproject = _read("pyproject.toml")

    assert 'requires-python = ">=3.10"' in pyproject
    assert 'target-version = "py310"' in pyproject


def test_ci_runs_supported_python_matrix():
    ci = _read(".github/workflows/ci.yml")

    assert 'PYTHON_DEFAULT_VERSION: "3.10"' in ci
    assert 'python-version: ["3.10", "3.11", "3.12", "3.13"]' in ci
    assert 'python-version: ${{ matrix.python-version }}' in ci


def test_dockerfile_defaults_to_minimum_supported_python():
    dockerfile = _read("Dockerfile")

    assert "ARG PYTHON_VERSION=3.10" in dockerfile
    assert "FROM python:${PYTHON_VERSION}-slim AS builder" in dockerfile
    assert "FROM python:${PYTHON_VERSION}-slim AS runtime" in dockerfile
