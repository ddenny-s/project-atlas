from __future__ import annotations

from pathlib import Path

from .state import record_status


def accept_parcel(database: Path, parcel_id: str) -> None:
    """Request-boundary entry point for a newly accepted parcel."""
    if not parcel_id.strip():
        raise ValueError("parcel_id is required")
    record_status(database, parcel_id, "accepted", writer="api")
