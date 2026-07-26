from __future__ import annotations

import argparse
from pathlib import Path

from .ledger import write_event


def submit_settlement(database: Path, settlement_id: str) -> None:
    """HTTP-like gateway entry point for a new settlement."""
    write_event(database, settlement_id, "submitted", writer="gateway")


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit one fixture settlement")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--settlement-id", default="fixture-settlement")
    args = parser.parse_args()
    submit_settlement(args.ledger, args.settlement_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
