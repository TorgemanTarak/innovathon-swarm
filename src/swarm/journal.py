"""Persistent local observations, runtime state, and a delivery outbox."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from swarm.models import EvidenceSummary, new_id, now_iso


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

    def bind_agent(self, agent_id: str) -> None:
        """Keep one endpoint identity attached to this directory across resets."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT value FROM state WHERE key='owner_agent_id'"
            ).fetchone()
            owners = {json.loads(row[0])} if row else set()
            if not owners:
                # Journals created before owner binding may still carry an active
                # incident or durable repair attempts identifying their endpoint.
                incident = self.get("incident")
                if incident:
                    owners.add(incident["agent_id"])
                owners.update(
                    json.loads(item[0])["agent_id"]
                    for item in self.connection.execute("SELECT payload FROM attempts")
                )
            if owners and owners != {agent_id}:
                raise ValueError(
                    f"This SWARM data directory belongs to endpoint {', '.join(sorted(owners))!r}; "
                    "reuse its --id or choose a new --data-dir"
                )
            self.connection.execute(
                "INSERT OR REPLACE INTO state VALUES ('owner_agent_id', ?)",
                (json.dumps(agent_id),),
            )

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

    def evidence_summaries(self, identities: list[str]) -> list[EvidenceSummary]:
        """Project only the fixed diagnostic fields; old/unsupported records stay local."""
        summaries = []
        for record in self.evidence(identities[:8]):
            fields = {
                key: record[key]
                for key in (
                    "cache_before",
                    "cache_after",
                    "probe_result",
                    "error_code",
                    "checks_passed",
                    "checks_total",
                )
                if key in record
            }
            if record["kind"] == "cache_changed":
                fields.update(
                    cache_before=record.get("previous"), cache_after=record.get("current")
                )
            elif record["kind"] == "recovery_verified":
                count = record.get("success_count")
                if type(count) is not int or not 3 <= count <= 100:
                    continue
                fields.update(probe_result="passed", checks_passed=count, checks_total=count)
            try:
                summaries.append(
                    EvidenceSummary(
                        evidence_id=record["evidence_id"],
                        observed_at=record["timestamp"],
                        kind=record["kind"],
                        **fields,
                    )
                )
            except ValidationError:
                # Historical observations remain readable as references. Do not
                # invent a timestamp, hash or successful probe for missing data.
                continue
        return summaries

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
            self.connection.execute("DELETE FROM state WHERE key != 'owner_agent_id'")
            self.connection.execute("DELETE FROM outbox")

    def close(self) -> None:
        self.connection.close()
