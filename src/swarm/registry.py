"""Transactional registry and conservative rollout policy for the hackathon fleet."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from swarm.models import (
    Assignment,
    Candidate,
    CommandAck,
    DemoCommand,
    FleetEvent,
    Heartbeat,
    IssueReport,
    Outcome,
    Recipe,
    Registration,
    Work,
    now_iso,
)


class RegistryError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _dump(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _environment_key(value: dict[str, Any]) -> tuple[str, str, str]:
    return value["app_id"], value["app_version"], value["platform"]


class Registry:
    """One transaction per operation; requests and restart recovery share the same policy."""

    def __init__(self, path: str | Path, assignment_timeout: float = 120.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.assignment_timeout = assignment_timeout
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recipes (
                    id TEXT PRIMARY KEY, cohort TEXT NOT NULL UNIQUE, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, incident_id TEXT NOT NULL,
                    recipe_id TEXT NOT NULL, data TEXT NOT NULL, state TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    UNIQUE(agent_id, incident_id, recipe_id)
                );
                CREATE TABLE IF NOT EXISTS outcomes (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, data TEXT NOT NULL, ack TEXT
                );
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO meta (key,value) VALUES ('paused','false');
                """
            )

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        kind: str,
        message: str,
        agent_id: str | None = None,
        recipe_id: str | None = None,
    ) -> None:
        event = FleetEvent(kind=kind, message=message, agent_id=agent_id, recipe_id=recipe_id)
        db.execute("INSERT INTO events (data) VALUES (?)", (_dump(event),))
        db.execute(
            "DELETE FROM events WHERE seq NOT IN (SELECT seq FROM events ORDER BY seq DESC LIMIT 500)"
        )

    @staticmethod
    def _agent(db: sqlite3.Connection, agent_id: str) -> dict[str, Any]:
        row = db.execute("SELECT data FROM agents WHERE id=?", (agent_id,)).fetchone()
        if not row:
            raise RegistryError(404, "Unknown agent; register first")
        return json.loads(row["data"])

    @staticmethod
    def _save_agent(db: sqlite3.Connection, agent: dict[str, Any]) -> None:
        db.execute(
            "INSERT OR REPLACE INTO agents (id,data) VALUES (?,?)",
            (agent["agent_id"], _dump(agent)),
        )

    @staticmethod
    def _save_recipe(db: sqlite3.Connection, recipe: Recipe) -> None:
        db.execute("UPDATE recipes SET data=? WHERE id=?", (_dump(recipe), recipe.recipe_id))

    @staticmethod
    def _paused(db: sqlite3.Connection) -> bool:
        return json.loads(db.execute("SELECT value FROM meta WHERE key='paused'").fetchone()[0])

    def register(self, registration: Registration) -> None:
        with self._db() as db:
            row = db.execute(
                "SELECT data FROM agents WHERE id=?", (registration.agent_id,)
            ).fetchone()
            existing = json.loads(row["data"]) if row else {}
            changed = existing and existing["environment"] != registration.environment.model_dump()
            state = {
                "health": "unknown",
                "detail": "Waiting for functional probe",
                "issue_key": None,
                "incident_id": None,
                "last_seen": now_iso(),
                "last_seen_epoch": time.time(),
            }
            if existing and not changed:
                state = {key: existing[key] for key in state}
            agent = {**registration.model_dump(), **state}
            self._save_agent(db, agent)
            if not existing:
                self._event(
                    db,
                    "agent_joined",
                    f"{registration.label} joined the swarm",
                    registration.agent_id,
                )
            elif changed:
                self._expire_agent_assignments(
                    db, registration.agent_id, "Agent environment changed"
                )
                self._event(
                    db,
                    "environment_changed",
                    "Environment changed; awaiting new incident",
                    registration.agent_id,
                )

    def heartbeat(self, agent_id: str, heartbeat: Heartbeat) -> None:
        with self._db() as db:
            agent = self._agent(db, agent_id)
            old_health = agent["health"]
            agent.update(heartbeat.model_dump())
            agent.update(last_seen=now_iso(), last_seen_epoch=time.time())
            self._save_agent(db, agent)
            if old_health != heartbeat.health:
                self._event(db, "health_changed", f"{agent['label']}: {heartbeat.health}", agent_id)

    def report_incident(self, report: IssueReport) -> None:
        with self._db() as db:
            agent = self._agent(db, report.agent_id)
            existing = db.execute(
                "SELECT data FROM incidents WHERE id=?", (report.incident_id,)
            ).fetchone()
            if existing:
                if existing["data"] != _dump(report):
                    raise RegistryError(409, "Incident ID already identifies different evidence")
                return
            if report.environment.model_dump() != agent["environment"]:
                raise RegistryError(409, "Incident environment differs from registered environment")
            if agent.get("incident_id") and agent["incident_id"] != report.incident_id:
                self._expire_agent_assignments(
                    db, report.agent_id, "Incident superseded before result"
                )
            db.execute(
                "INSERT INTO incidents (id,data) VALUES (?,?)", (report.incident_id, _dump(report))
            )
            agent.update(incident_id=report.incident_id, issue_key=report.issue_key)
            self._save_agent(db, agent)
            self._event(db, "issue_detected", report.summary, report.agent_id)

    def propose(self, candidate: Candidate) -> Recipe:
        with self._db() as db:
            prior = db.execute(
                "SELECT recipe_id,data FROM candidates WHERE id=?", (candidate.candidate_id,)
            ).fetchone()
            if prior:
                if prior["data"] != _dump(candidate):
                    raise RegistryError(409, "Candidate ID already identifies a different proposal")
                return Recipe.model_validate_json(
                    db.execute(
                        "SELECT data FROM recipes WHERE id=?", (prior["recipe_id"],)
                    ).fetchone()[0]
                )
            source = self._agent(db, candidate.source_agent_id)
            incident_row = db.execute(
                "SELECT data FROM incidents WHERE id=?", (candidate.incident_id,)
            ).fetchone()
            if not incident_row:
                raise RegistryError(409, "Candidate must reference a recorded incident")
            incident = IssueReport.model_validate_json(incident_row[0])
            if (
                incident.agent_id != candidate.source_agent_id
                or incident.issue_key != candidate.issue_key
                or incident.environment != candidate.environment
            ):
                raise RegistryError(409, "Candidate does not match its source incident")
            if candidate.environment.model_dump() != source["environment"]:
                raise RegistryError(409, "Candidate environment differs from source registration")
            if source["health"] != "healthy" or time.time() - source["last_seen_epoch"] > 30:
                raise RegistryError(409, "Source needs a recent healthy functional probe")
            if not candidate.evidence_ids:
                raise RegistryError(422, "Candidate needs local evidence references")
            cohort = _dump(
                [
                    candidate.issue_key,
                    candidate.action,
                    *_environment_key(candidate.environment.model_dump()),
                ]
            )
            existing = db.execute("SELECT data FROM recipes WHERE cohort=?", (cohort,)).fetchone()
            if existing:
                recipe = Recipe.model_validate_json(existing[0])
            else:
                recipe = Recipe(candidate=candidate)
                db.execute(
                    "INSERT INTO recipes (id,cohort,data) VALUES (?,?,?)",
                    (recipe.recipe_id, cohort, _dump(recipe)),
                )
                self._event(
                    db,
                    "candidate_discovered",
                    candidate.explanation,
                    candidate.source_agent_id,
                    recipe.recipe_id,
                )
            db.execute(
                "INSERT INTO candidates (id,recipe_id,data) VALUES (?,?,?)",
                (candidate.candidate_id, recipe.recipe_id, _dump(candidate)),
            )
            return recipe

    def _finish(self, db: sqlite3.Connection, outcome: Outcome) -> None:
        row = db.execute(
            "SELECT data,state FROM assignments WHERE id=?", (outcome.assignment_id,)
        ).fetchone()
        if not row:
            raise RegistryError(409, "Unknown or reset assignment")
        assignment = Assignment.model_validate_json(row["data"])
        if (
            assignment.agent_id != outcome.agent_id
            or assignment.recipe.recipe_id != outcome.recipe_id
            or assignment.incident_id != outcome.incident_id
        ):
            raise RegistryError(409, "Outcome does not match the issued assignment")
        existing = db.execute(
            "SELECT data FROM outcomes WHERE id=?", (outcome.assignment_id,)
        ).fetchone()
        if existing:
            if existing[0] != _dump(outcome):
                raise RegistryError(409, "Assignment already has a different terminal outcome")
            return
        if row["state"] != "pending":
            raise RegistryError(409, "Assignment is no longer pending")
        if outcome.checks_passed > outcome.checks_total:
            raise RegistryError(422, "Passed checks cannot exceed total checks")
        if outcome.status == "verified" and (
            outcome.checks_total < 3 or outcome.checks_passed != outcome.checks_total
        ):
            raise RegistryError(
                422, "Verified recovery requires at least three successful functional probes"
            )
        recipe = Recipe.model_validate_json(
            db.execute("SELECT data FROM recipes WHERE id=?", (outcome.recipe_id,)).fetchone()[0]
        )
        db.execute(
            "INSERT INTO outcomes (id,data) VALUES (?,?)", (outcome.assignment_id, _dump(outcome))
        )
        db.execute("UPDATE assignments SET state='complete' WHERE id=?", (outcome.assignment_id,))
        if outcome.status == "verified":
            recipe.successes += 1
            if recipe.status != "paused":
                recipe.status = "validated"
            self._event(
                db,
                "repair_verified",
                f"Recovery verified: {outcome.checks_passed}/{outcome.checks_total} functional probes passed",
                outcome.agent_id,
                recipe.recipe_id,
            )
        else:
            recipe.failures += 1
            recipe.status = "paused"
            self._event(
                db,
                "rollout_paused",
                f"Rollout paused: {outcome.status}. {outcome.detail}",
                outcome.agent_id,
                recipe.recipe_id,
            )
        self._save_recipe(db, recipe)

    def record_outcome(self, outcome: Outcome) -> None:
        # Commit timeout decisions even when the subsequent late result conflicts.
        with self._db() as db:
            self._expire_assignments(db)
        with self._db() as db:
            self._finish(db, outcome)

    def _expire_agent_assignments(self, db: sqlite3.Connection, agent_id: str, detail: str) -> None:
        rows = db.execute(
            "SELECT data FROM assignments WHERE agent_id=? AND state='pending'", (agent_id,)
        ).fetchall()
        for row in rows:
            assignment = Assignment.model_validate_json(row[0])
            self._finish(
                db,
                Outcome(
                    assignment_id=assignment.assignment_id,
                    agent_id=agent_id,
                    recipe_id=assignment.recipe.recipe_id,
                    incident_id=assignment.incident_id,
                    status="inconclusive",
                    detail=detail,
                ),
            )

    def _expire_assignments(self, db: sqlite3.Connection) -> None:
        rows = db.execute(
            "SELECT data FROM assignments WHERE state='pending' AND expires_at < ?", (time.time(),)
        ).fetchall()
        for row in rows:
            assignment = Assignment.model_validate_json(row[0])
            self._finish(
                db,
                Outcome(
                    assignment_id=assignment.assignment_id,
                    agent_id=assignment.agent_id,
                    recipe_id=assignment.recipe.recipe_id,
                    incident_id=assignment.incident_id,
                    status="inconclusive",
                    detail="Assignment timed out; recovery was not confirmed",
                ),
            )

    def work(self, agent_id: str) -> Work:
        with self._db() as db:
            agent = self._agent(db, agent_id)
            self._expire_assignments(db)
            commands = [
                DemoCommand.model_validate_json(row[0])
                for row in db.execute(
                    "SELECT data FROM commands WHERE agent_id=? AND ack IS NULL ORDER BY rowid",
                    (agent_id,),
                )
            ]
            paused = self._paused(db)
            if paused or commands:
                return Work(commands=commands, paused=paused)
            # Do not re-deliver a pending repair after a global/recipe pause.
            pending = db.execute(
                "SELECT data,recipe_id FROM assignments WHERE agent_id=? AND state='pending'",
                (agent_id,),
            ).fetchone()
            if pending:
                recipe = Recipe.model_validate_json(
                    db.execute(
                        "SELECT data FROM recipes WHERE id=?", (pending["recipe_id"],)
                    ).fetchone()[0]
                )
                if recipe.status == "paused":
                    return Work(paused=False)
                return Work(assignment=Assignment.model_validate_json(pending["data"]))
            if (
                agent["health"] != "unhealthy"
                or not agent.get("incident_id")
                or not agent.get("issue_key")
                or time.time() - agent["last_seen_epoch"] > 30
            ):
                return Work()
            for row in db.execute("SELECT data FROM recipes ORDER BY rowid"):
                recipe = Recipe.model_validate_json(row[0])
                candidate = recipe.candidate
                if candidate.issue_key != agent["issue_key"]:
                    continue
                if candidate.source_agent_id == agent_id and (
                    recipe.status != "validated" or agent["incident_id"] == candidate.incident_id
                ):
                    # The origin cannot validate its own discovery, but can reuse
                    # independently validated knowledge for a later incident.
                    continue
                if _environment_key(candidate.environment.model_dump()) != _environment_key(
                    agent["environment"]
                ):
                    marker = f"incompatible:{recipe.recipe_id}:{agent_id}:{agent['incident_id']}"
                    if not db.execute("SELECT 1 FROM meta WHERE key=?", (marker,)).fetchone():
                        db.execute("INSERT INTO meta (key,value) VALUES (?, 'true')", (marker,))
                        self._event(
                            db,
                            "repair_incompatible",
                            "Repair skipped: application version or platform differs",
                            agent_id,
                            recipe.recipe_id,
                        )
                    continue
                if recipe.status in {"paused", "canary"}:
                    continue
                previous = db.execute(
                    "SELECT 1 FROM assignments WHERE agent_id=? AND incident_id=? AND recipe_id=?",
                    (agent_id, agent["incident_id"], recipe.recipe_id),
                ).fetchone()
                if previous:
                    continue
                role: Literal["canary", "follower"] = (
                    "canary" if recipe.status == "candidate" else "follower"
                )
                if role == "canary":
                    recipe.status = "canary"
                    recipe.canary_agent_id = agent_id
                    self._save_recipe(db, recipe)
                assignment = Assignment(
                    agent_id=agent_id, incident_id=agent["incident_id"], recipe=recipe, role=role
                )
                db.execute(
                    "INSERT INTO assignments (id,agent_id,incident_id,recipe_id,data,state,expires_at) VALUES (?,?,?,?,?,'pending',?)",
                    (
                        assignment.assignment_id,
                        agent_id,
                        agent["incident_id"],
                        recipe.recipe_id,
                        _dump(assignment),
                        time.time() + self.assignment_timeout,
                    ),
                )
                self._event(
                    db,
                    "canary_started" if role == "canary" else "repair_dispatched",
                    f"{role.capitalize()} repair assigned to {agent['label']}",
                    agent_id,
                    recipe.recipe_id,
                )
                return Work(assignment=assignment)
            return Work()

    def pause(self, paused: bool) -> None:
        with self._db() as db:
            db.execute("UPDATE meta SET value=? WHERE key='paused'", (_dump(paused),))
            self._event(
                db,
                "fleet_paused" if paused else "fleet_resumed",
                "New repair dispatch is paused; running actions may finish"
                if paused
                else "Fleet dispatch resumed; failed recipes remain paused",
            )

    def enqueue(
        self,
        kind: Literal["inject_fault", "manual_repair", "reset", "fail_next_verification"],
        agent_ids: list[str],
    ) -> None:
        with self._db() as db:
            targets = list(dict.fromkeys(agent_ids)) or [
                row[0] for row in db.execute("SELECT id FROM agents ORDER BY id")
            ]
            if not targets:
                raise RegistryError(409, "No endpoints have registered yet")
            if kind == "manual_repair" and len(targets) != 1:
                raise RegistryError(
                    422, "Choose exactly one source endpoint for the simulated human repair"
                )
            for agent_id in targets:
                self._agent(db, agent_id)
            for agent_id in targets:
                command = DemoCommand(kind=kind)
                db.execute(
                    "INSERT INTO commands (id,agent_id,data) VALUES (?,?,?)",
                    (command.command_id, agent_id, _dump(command)),
                )
                self._event(
                    db, "demo_command", f"Demo command queued: {kind.replace('_', ' ')}", agent_id
                )

    def acknowledge(self, agent_id: str, ack: CommandAck) -> None:
        with self._db() as db:
            row = db.execute(
                "SELECT agent_id,ack FROM commands WHERE id=?", (ack.command_id,)
            ).fetchone()
            if not row or row["agent_id"] != agent_id:
                raise RegistryError(409, "Unknown or reset demo command")
            if row["ack"] is not None:
                if row["ack"] != _dump(ack):
                    raise RegistryError(409, "Command already has a different acknowledgement")
                return
            db.execute("UPDATE commands SET ack=? WHERE id=?", (_dump(ack), ack.command_id))
            self._event(
                db,
                "demo_command_completed" if ack.success else "demo_command_failed",
                ack.detail or "Demo command completed",
                agent_id,
            )

    def reset(self) -> None:
        with self._db() as db:
            for table in (
                "incidents",
                "recipes",
                "candidates",
                "assignments",
                "outcomes",
                "commands",
                "events",
            ):
                db.execute(f"DELETE FROM {table}")  # Table names are fixed, never user input.
            db.execute("DELETE FROM meta WHERE key != 'paused'")
            db.execute("UPDATE meta SET value='false' WHERE key='paused'")
            for row in db.execute("SELECT data FROM agents").fetchall():
                agent = json.loads(row[0])
                agent.update(
                    health="unknown", detail="Reset queued", issue_key=None, incident_id=None
                )
                self._save_agent(db, agent)
                command = DemoCommand(kind="reset")
                db.execute(
                    "INSERT INTO commands (id,agent_id,data) VALUES (?,?,?)",
                    (command.command_id, agent["agent_id"], _dump(command)),
                )
            self._event(
                db,
                "demo_reset",
                "Demo reset requested; endpoints are restoring their dedicated demo state",
            )

    def snapshot(self, demo_mode: bool) -> dict[str, Any]:
        with self._db() as db:
            self._expire_assignments(db)
            agents = []
            for row in db.execute("SELECT data FROM agents ORDER BY id"):
                agent = json.loads(row[0])
                agent["online"] = time.time() - agent.pop("last_seen_epoch") <= 30
                agents.append(agent)
            return {
                "agents": agents,
                "recipes": [
                    json.loads(row[0])
                    for row in db.execute("SELECT data FROM recipes ORDER BY rowid DESC")
                ],
                "outcomes": [
                    json.loads(row[0])
                    for row in db.execute("SELECT data FROM outcomes ORDER BY rowid DESC")
                ],
                "events": [
                    json.loads(row[0])
                    for row in db.execute("SELECT data FROM events ORDER BY seq DESC LIMIT 200")
                ],
                "paused": self._paused(db),
                "demo_mode": demo_mode,
            }
