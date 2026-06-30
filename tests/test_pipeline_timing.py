"""Tests for pipeline elapsed-time helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from data_ingestion.utils.pipeline_timing import (
    agent_elapsed_timing,
    elapsed_seconds,
    format_duration,
    job_elapsed_timing,
    parse_pipeline_timestamp,
)


def test_format_duration_variants() -> None:
    assert format_duration(45) == "45s"
    assert format_duration(125) == "2m 5s"
    assert format_duration(3600) == "1h"


def test_elapsed_seconds_from_iso_timestamps() -> None:
    start = "2026-06-25T10:00:00Z"
    end = "2026-06-25T10:02:30Z"
    assert elapsed_seconds(start, end) == 150


def test_agent_elapsed_timing_running_uses_now() -> None:
    started = datetime.now(timezone.utc).replace(microsecond=0)
    started_iso = started.strftime("%Y-%m-%dT%H:%M:%SZ")
    timing = agent_elapsed_timing({"status": "running", "started_at": started_iso})
    assert timing["elapsed_seconds"] >= 0
    assert timing["elapsed_time"]


def test_job_elapsed_timing_from_progress_and_agents() -> None:
    payload = job_elapsed_timing(
        {
            "pipeline_started_at": "2026-06-25T10:00:00Z",
            "pipeline_completed_at": "2026-06-25T10:10:00Z",
        },
        [{"started_at": "2026-06-25T10:00:00Z", "completed_at": "2026-06-25T10:05:00Z"}],
        status="completed",
    )
    assert payload["total_elapsed_seconds"] == 600
    assert payload["total_elapsed_time"] == "10m"


def test_parse_pipeline_timestamp_accepts_z_suffix() -> None:
    parsed = parse_pipeline_timestamp("2026-06-25T10:00:00Z")
    assert parsed is not None
    assert parsed.year == 2026
