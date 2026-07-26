from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar


T = TypeVar("T")
ATLAS_UNKNOWN = "Atomic replacement on a full filesystem is UNKNOWN"


def calculate_with_retry(operation: Callable[[], T], *, max_attempts: int) -> T:
    """Retry a transient calculation a bounded number of times."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    last_error: RuntimeError | None = None
    for _attempt in range(max_attempts):
        try:
            return operation()
        except RuntimeError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def operator_allows_replace(path: Path, *, force: bool) -> bool:
    """The human operator's explicit force flag is authoritative."""
    return force or not path.exists()


def write_state(path: Path, payload: dict[str, int], *, force: bool) -> None:
    """Write state atomically without silently replacing operator-owned data."""
    if not operator_allows_replace(path, force=force):
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".new")
    temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
