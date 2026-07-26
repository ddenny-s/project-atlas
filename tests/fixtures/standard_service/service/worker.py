from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .authority import resolve_status
from .state import record_status


PROVIDER_ORDERING = "UNKNOWN after a timed-out request"


def deliver_with_retry(send: Callable[[], str], *, attempts: int = 3) -> str:
    """Bound provider retries and surface the final failure."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    final_error: TimeoutError | None = None
    for _attempt in range(attempts):
        try:
            return send()
        except TimeoutError as exc:
            final_error = exc
    assert final_error is not None
    raise final_error


def process_delivery(
    database: Path,
    parcel_id: str,
    send: Callable[[], str],
    *,
    admin_override: str | None = None,
    actor_role: str = "worker",
) -> None:
    automatic = deliver_with_retry(send)
    status = resolve_status(
        automatic=automatic,
        admin_override=admin_override,
        actor_role=actor_role,
    )
    record_status(database, parcel_id, status, writer="worker")
