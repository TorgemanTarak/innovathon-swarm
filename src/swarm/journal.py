"""Persistent local observations, runtime state, and a delivery outbox."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from swarm.models import new_id, now_iso


class Journal:
    def __init__(self, path: Path) -> None:
        if path.is_symlink():
            raise ValueError("Journal may not be a symlink")
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                kind TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, path TEXT NOT NULL, payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        self.connection.commit()

    def observe(self, kind: str, **data: Any) -> str:
        identity = new_id()
        with self.connection:
            self.connection.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?)",
                (identity, now_iso(), kind, json.dumps(data)),
            )
        return identity

    def evidence(self, identities: list[str]) -> list[dict]:
        result = []
        for identity in identities:
            row = self.connection.execute(
                "SELECT id, timestamp, kind, data FROM observations WHERE id=?", (identity,)
            ).fetchone()
            if row:
                result.append(
                    {
                        "evidence_id": row[0],
                        "timestamp": row[1],
                        "kind": row[2],
                        **json.loads(row[3]),
                    }
                )
        return result

    def get(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value: Any) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value))
            )

    def enqueue(self, identity: str, path: str, payload: dict) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO outbox VALUES (?, ?, ?, ?)",
                (identity, path, json.dumps(payload), now_iso()),
            )

    def pending(self) -> list[tuple[str, str, dict]]:
        rows = self.connection.execute(
            "SELECT id,path,payload FROM outbox ORDER BY rowid"
        ).fetchall()
        return [(row[0], row[1], json.loads(row[2])) for row in rows]

    def delivered(self, identity: str) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM outbox WHERE id=?", (identity,))

    def claim_attempt(self, identity: str, payload: dict) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO attempts VALUES (?, 'executing', ?)",
                (identity, json.dumps(payload)),
            )
        return cursor.rowcount == 1

    def attempt(self, identity: str) -> tuple[str, dict] | None:
        row = self.connection.execute(
            "SELECT state,payload FROM attempts WHERE id=?", (identity,)
        ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def unfinished_attempts(self) -> list[dict]:
        rows = self.connection.execute(
            "SELECT payload FROM attempts WHERE state='executing' ORDER BY rowid"
        ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def finish_attempt(self, identity: str, payload: dict) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE attempts SET state='finished',payload=? WHERE id=?",
                (json.dumps(payload), identity),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO outbox VALUES (?, '/api/outcomes', ?, ?)",
                (f"outcome:{identity}", json.dumps(payload), now_iso()),
            )

    def command(self, identity: str) -> dict | None:
        row = self.connection.execute(
            "SELECT payload FROM commands WHERE id=?", (identity,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def finish_command(self, identity: str, payload: dict) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO commands VALUES (?, ?)", (identity, json.dumps(payload))
            )

    def clear_runtime(self) -> None:
        # Preserve observations/attempt dedupe/command acknowledgements as an audit trail.
        with self.connection:
            self.connection.execute("DELETE FROM state")
            self.connection.execute("DELETE FROM outbox")

    def close(self) -> None:
        self.connection.close()
