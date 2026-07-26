from __future__ import annotations


def choose_settlement_state(
    *, provider_state: str, compliance_state: str | None, actor_role: str
) -> str:
    """Manual compliance decisions outrank external provider automation."""
    if compliance_state is not None:
        if actor_role != "compliance":
            raise PermissionError("compliance authority is required")
        return compliance_state
    return provider_state
