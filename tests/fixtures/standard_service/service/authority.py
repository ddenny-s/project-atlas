from __future__ import annotations


def resolve_status(*, automatic: str, admin_override: str | None, actor_role: str) -> str:
    """An administrator's explicit override has authority over automation."""
    if admin_override is not None:
        if actor_role != "admin":
            raise PermissionError("only an admin may override automatic status")
        return admin_override
    return automatic
