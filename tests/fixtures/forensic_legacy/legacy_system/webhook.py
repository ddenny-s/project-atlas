from __future__ import annotations

from pathlib import Path

from .authority import choose_settlement_state
from .ledger import write_event


def receive_provider_event(
    database: Path,
    settlement_id: str,
    provider_state: str,
    *,
    compliance_state: str | None = None,
    actor_role: str = "provider",
) -> None:
    """Webhook entry point with an explicit authority boundary."""
    state = choose_settlement_state(
        provider_state=provider_state,
        compliance_state=compliance_state,
        actor_role=actor_role,
    )
    write_event(database, settlement_id, state, writer="webhook")
