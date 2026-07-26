from __future__ import annotations

from pathlib import Path

from .ledger import write_event


def reconcile(database: Path, settlement_id: str) -> None:
    """Scheduled entry point that records a reconciliation request."""
    write_event(database, settlement_id, "reconcile-requested", writer="cron")
