from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .ledger import write_event
from .retry import provider_call_with_retry


PARTIAL_ACK_RECOVERY = "UNKNOWN: provider acknowledgement may precede local write"


def process_queue_item(database: Path, settlement_id: str, send: Callable[[], str]) -> None:
    """Queue-worker entry point that performs a retried external effect."""
    provider_state = provider_call_with_retry(send)
    write_event(database, settlement_id, provider_state, writer="worker")
