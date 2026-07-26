from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def provider_call_with_retry(call: Callable[[], T], *, attempts: int = 4) -> T:
    """Retry timeouts without claiming provider-side idempotency."""
    if attempts < 1:
        raise ValueError("attempts must be positive")
    final_error: TimeoutError | None = None
    for _attempt in range(attempts):
        try:
            return call()
        except TimeoutError as exc:
            final_error = exc
    assert final_error is not None
    raise final_error
