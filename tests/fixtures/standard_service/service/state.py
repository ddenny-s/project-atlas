from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path


def record_status(database: Path, parcel_id: str, status: str, *, writer: str) -> None:
    """Persistent state writer shared by the API and worker runtimes."""
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS parcel_state "
                "(parcel_id TEXT PRIMARY KEY, status TEXT NOT NULL, writer TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO parcel_state(parcel_id, status, writer) VALUES (?, ?, ?) "
                "ON CONFLICT(parcel_id) DO UPDATE SET status=excluded.status, writer=excluded.writer",
                (parcel_id, status, writer),
            )
