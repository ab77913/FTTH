"""Elapsed-time helpers for pipeline and agent progress."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_pipeline_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def elapsed_seconds(
    started_at: str | None,
    completed_at: str | None = None,
    *,
    now: datetime | None = None,
) -> int | None:
    started = parse_pipeline_timestamp(started_at)
    if not started:
        return None
    if completed_at:
        finished = parse_pipeline_timestamp(completed_at)
    else:
        finished = now or datetime.now(timezone.utc)
    if not finished:
        return None
    return max(0, int((finished - started).total_seconds()))


def format_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "unknown"
    total = max(0, int(round(float(seconds))))
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    secs = total % 60
    if minutes < 60:
        return f"{minutes}m" if secs == 0 else f"{minutes}m {secs}s"
    hours = minutes // 60
    rem_minutes = minutes % 60
    return f"{hours}h" if rem_minutes == 0 else f"{hours}h {rem_minutes}m"


def format_duration_range(min_seconds: int, max_seconds: int) -> str:
    min_seconds = max(0, int(min_seconds))
    max_seconds = max(min_seconds, int(max_seconds))
    if min_seconds == max_seconds:
        return format_duration(min_seconds)
    return f"{format_duration(min_seconds)} - {format_duration(max_seconds)}"


def agent_elapsed_timing(agent: dict[str, Any]) -> dict[str, Any]:
    status = str(agent.get("status") or "")
    started_at = agent.get("started_at")
    completed_at = agent.get("completed_at")
    end = completed_at if status in ("completed", "failed") else None
    seconds = elapsed_seconds(started_at, end)
    if seconds is None:
        return {}
    return {
        "elapsed_seconds": seconds,
        "elapsed_time": format_duration(seconds),
    }


def job_elapsed_timing(
    progress: dict[str, Any] | None,
    agents: list[dict[str, Any]],
    *,
    status: str,
) -> dict[str, Any]:
    progress = progress or {}
    started_at = progress.get("pipeline_started_at")
    completed_at = progress.get("pipeline_completed_at")

    if not started_at:
        starts = [a.get("started_at") for a in agents if a.get("started_at")]
        started_at = min(starts) if starts else None

    normalized = str(status or "").lower()
    if not completed_at and normalized in ("completed", "failed"):
        ends = [a.get("completed_at") for a in agents if a.get("completed_at")]
        completed_at = max(ends) if ends else None

    end_for_elapsed = completed_at if normalized in ("completed", "failed") else None
    total_seconds = elapsed_seconds(started_at, end_for_elapsed)

    return {
        "pipeline_started_at": started_at,
        "pipeline_completed_at": completed_at if normalized in ("completed", "failed") else None,
        "total_elapsed_seconds": total_seconds,
        "total_elapsed_time": format_duration(total_seconds) if total_seconds is not None else None,
    }
