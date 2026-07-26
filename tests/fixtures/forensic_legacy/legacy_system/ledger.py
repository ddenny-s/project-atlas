from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def write_event(database: Path, settlement_id: str, state: str, *, writer: str) -> None:
    """Append-only state writer used by several independent runtimes."""
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS settlement_events "
                "(sequence INTEGER PRIMARY KEY AUTOINCREMENT, settlement_id TEXT NOT NULL, "
                "state TEXT NOT NULL, writer TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO settlement_events(settlement_id, state, writer) VALUES (?, ?, ?)",
                (settlement_id, state, writer),
            )
