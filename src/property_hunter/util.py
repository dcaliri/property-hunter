"""Shared small utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def utcnow() -> str:
    """Current UTC time as ISO-8601 with timezone offset."""
    return datetime.now(timezone.utc).isoformat()


def window_bounds(window_days: int, end: datetime | None = None) -> tuple[str, str]:
    """Return (window_start, window_end) ISO-8601 for a trailing window."""
    end_dt = end or datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=window_days)
    return start_dt.isoformat(), end_dt.isoformat()
