"""Account-scoped data ownership helpers."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

import api_server


def test_account_customer_id_normalizes_username() -> None:
    assert api_server._account_customer_id("Ftth.Team") == "ftth.team"
    assert api_server._account_customer_id("  OWNER_NAME  ") == "owner_name"


def test_require_owned_job_allows_owner() -> None:
    job = SimpleNamespace(customer_id="alice")
    session = SimpleNamespace(get=lambda model, job_id: job)

    assert api_server._require_owned_job(session, uuid4(), "Alice") is job


def test_require_owned_job_hides_foreign_job() -> None:
    job = SimpleNamespace(customer_id="bob")
    session = SimpleNamespace(get=lambda model, job_id: job)

    with pytest.raises(HTTPException) as exc:
        api_server._require_owned_job(session, uuid4(), "alice")

    assert exc.value.status_code == 404


def test_require_owned_record_allows_owner() -> None:
    record = SimpleNamespace(customer_id="alice")
    session = SimpleNamespace(get=lambda model, record_id: record)

    assert api_server._require_owned_record(session, 123, "Alice") is record


def test_require_owned_record_hides_foreign_record() -> None:
    record = SimpleNamespace(customer_id="bob")
    session = SimpleNamespace(get=lambda model, record_id: record)

    with pytest.raises(HTTPException) as exc:
        api_server._require_owned_record(session, 123, "alice")

    assert exc.value.status_code == 404