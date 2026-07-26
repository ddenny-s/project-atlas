from __future__ import annotations

from pathlib import Path

from .ledger import write_event


OWNER = "UNKNOWN: deprecated migration writer has no current owner"


def replay_legacy_row(database: Path, settlement_id: str) -> None:
    """Deprecated writer retained to expose a conflicting legacy contour."""
    write_event(database, settlement_id, "legacy-replayed", writer="migration")
