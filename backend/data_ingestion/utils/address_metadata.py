"""Helpers for persisting address-level fields inside addresses.raw_metadata."""
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm.attributes import flag_modified

from data_ingestion.database.models import Address, Agent1Result

AGENT1_RAW_METADATA_KEY = "Smarty_Street"


def persist_buildings_address_in_raw_metadata(
    addr: Address,
    buildings_address: dict[str, Any],
) -> None:
    """Agent 4 only: store building classification under raw_metadata['buildings_address']."""
    meta = dict(addr.raw_metadata or {})
    meta["buildings_address"] = buildings_address
    addr.raw_metadata = meta
    if inspect(addr, raiseerr=False) is not None:
        flag_modified(addr, "raw_metadata")


def sync_reverse_geocode_confidence_in_raw_metadata(addr: Address, confidence: int) -> None:
    """Write reverse-only confidence to raw_metadata (does not touch address_validation)."""
    meta = dict(addr.raw_metadata or {})
    meta["reverse_geocode_confidence_score"] = int(confidence)
    addr.raw_metadata = meta
    flag_modified(addr, "raw_metadata")


def _provider_sub_block(
    *,
    standardized_address: str | None = None,
    dpv: str | None = None,
    zip_plus_4: str | None = None,
    vacant: bool | None = None,
    record_type: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    geocode_precision: str | None = None,
    success: bool | None = None,
    error: str | None = None,
    confidence_score: int | None = None,
    confidence_details: list[str] | None = None,
    confidence_codes: list[str] | None = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {}
    for key, val in (
        ("standardized_address", standardized_address),
        ("dpv", dpv),
        ("zip_plus_4", zip_plus_4),
        ("vacant", vacant),
        ("record_type", record_type),
        ("latitude", latitude),
        ("longitude", longitude),
        ("geocode_precision", geocode_precision),
        ("success", success),
        ("error", error),
        ("confidence_score", confidence_score),
        ("confidence_details", confidence_details),
        ("confidence_codes", confidence_codes),
    ):
        if val is not None and val != "":
            block[key] = val
    return block


def _provider_block_from_result(result: Any, conf: dict[str, Any] | None = None) -> dict[str, Any]:
    conf = conf or {}
    return _provider_sub_block(
        standardized_address=getattr(result, "standardized_address", None),
        dpv=getattr(result, "dpv_match", None),
        zip_plus_4=getattr(result, "zip_plus_4", None),
        vacant=getattr(result, "vacant", None),
        record_type=getattr(result, "record_type", None),
        latitude=getattr(result, "latitude", None),
        longitude=getattr(result, "longitude", None),
        geocode_precision=getattr(result, "geocode_precision", None),
        success=getattr(result, "success", None),
        error=getattr(result, "error", None),
        confidence_score=conf.get("score"),
        confidence_details=conf.get("details"),
        confidence_codes=conf.get("codes"),
    )


def _provider_block_from_row(prefix: str, row: Agent1Result) -> dict[str, Any]:
    standardized = getattr(row, f"{prefix}_standardized_address", None)
    block = _provider_sub_block(
        standardized_address=standardized,
        dpv=getattr(row, f"{prefix}_dpv", None),
        zip_plus_4=getattr(row, f"{prefix}_zip_plus_4", None),
        vacant=getattr(row, f"{prefix}_vacant", None),
        record_type=getattr(row, f"{prefix}_record_type", None),
        latitude=getattr(row, f"{prefix}_lat", None) if prefix == "smarty" else None,
        longitude=getattr(row, f"{prefix}_lon", None) if prefix == "smarty" else None,
    )
    success = bool(standardized)
    block["success"] = success
    if not success:
        err = getattr(row, "exception_reason", None) or ""
        if err:
            block["error"] = err
    return block


def build_agent1_address_validator_block(
    row: Agent1Result,
    *,
    smarty_result: Any | None = None,
    melissa_result: Any | None = None,
    conf_audit: dict[str, Any] | None = None,
    cached: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build Smarty/Melissa provider snapshot for raw_metadata['Smarty_Street']."""
    conf_audit = conf_audit or {}
    smarty_conf = conf_audit.get("smarty") if isinstance(conf_audit.get("smarty"), dict) else {}
    melissa_conf = conf_audit.get("melissa") if isinstance(conf_audit.get("melissa"), dict) else {}

    if smarty_result is not None:
        smarty_block = _provider_block_from_result(smarty_result, smarty_conf)
    else:
        smarty_block = _provider_block_from_row("smarty", row)

    if melissa_result is not None:
        melissa_block = _provider_block_from_result(melissa_result, melissa_conf)
    else:
        melissa_block = _provider_block_from_row("melissa", row)

    block: dict[str, Any] = {
        "smarty": smarty_block or _provider_block_from_row("smarty", row),
        "melissa": melissa_block or _provider_block_from_row("melissa", row),
    }

    for key, val in (
        ("chosen_provider", row.chosen_provider),
        ("chosen_standardized_address", row.chosen_standardized_address),
        ("confidence_score", row.confidence_score),
        ("validation_status", row.validation_status),
        ("structure_hint", row.structure_hint),
        ("exception_reason", row.exception_reason),
        ("comparison_reason", row.comparison_reason),
        ("canonical_address", row.canonical_address),
        ("raw_address", row.raw_address),
    ):
        if val is not None and val != "":
            block[key] = val

    if cached:
        block["source"] = "cache"
        if cached.get("cached_at"):
            block["cached_at"] = cached["cached_at"]
        if cached.get("normalized_full_address"):
            block["normalized_full_address"] = cached["normalized_full_address"]

    return block


def persist_agent1_address_validator_in_raw_metadata(
    addr: Address,
    row: Agent1Result,
    *,
    smarty_result: Any | None = None,
    melissa_result: Any | None = None,
    conf_audit: dict[str, Any] | None = None,
    cached: dict[str, Any] | None = None,
) -> None:
    """Agent 1 only: store Smarty/Melissa provider details under raw_metadata['Smarty_Street']."""
    block = build_agent1_address_validator_block(
        row,
        smarty_result=smarty_result,
        melissa_result=melissa_result,
        conf_audit=conf_audit,
        cached=cached,
    )
    if not block:
        return

    meta = dict(addr.raw_metadata or {})
    meta.pop("agent1_address_validator", None)
    meta[AGENT1_RAW_METADATA_KEY] = block
    addr.raw_metadata = meta
    if inspect(addr, raiseerr=False) is not None:
        flag_modified(addr, "raw_metadata")
