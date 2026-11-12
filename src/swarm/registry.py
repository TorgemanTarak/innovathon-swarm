"""Transactional registry and conservative rollout policy for the hackathon fleet."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class _Eligibility:
    agent_id: str
    status: Literal["eligible", "waiting", "excluded", "assigned", "completed"]
    reason: str
    reason_detail: str
    role: Literal["canary", "follower"] | None = None


def _eligibility(
    agent: dict[str, Any],
    recipe: Recipe,
    *,
    paused: bool,
    command_pending: bool,
    reset_pending: bool,
    pending: Assignment | None,
    previous: Assignment | None,
    outcome: Outcome | None,
    now: float,
) -> _Eligibility:
    """Explain delivery without reserving work or changing registry state.

    `previous` and `outcome` belong to this recipe and the current incident.
    Pending redelivery intentionally precedes fresh health/compatibility checks:
    an endpoint may be reporting recovery while its durable outcome is in flight.
    The endpoint still rechecks every precondition before a new mutation.
    """
    agent_id = agent["agent_id"]
    candidate = recipe.candidate
    same_pending = pending is not None and pending.recipe.recipe_id == recipe.recipe_id
    role = pending.role if pending is not None and same_pending else None

    def decision(
        status: Literal["eligible", "waiting", "excluded", "assigned", "completed"],
        reason: str,
        detail: str,
        assigned_role: Literal["canary", "follower"] | None = role,
    ) -> _Eligibility:
        return _Eligibility(agent_id, status, reason, detail, assigned_role)

    if previous is not None and outcome is not None:
        return decision(
            "completed",
            f"outcome_{outcome.status}",
            f"Current incident: {outcome.status}; "
            f"{outcome.checks_passed}/{outcome.checks_total} functional checks passed. "
            f"{outcome.detail}",
            previous.role,
        )
    if paused:
        return decision("waiting", "global_paused", "Fleet dispatch is paused.")
    if reset_pending:
        return decision(
            "waiting", "reset_pending", "Waiting for a successful endpoint reset acknowledgement."
        )
    if command_pending:
        return decision("waiting", "command_pending", "A demo command must finish before repair.")
    if pending is not None:
        if not same_pending:
            return decision("waiting", "other_assignment", "Another repair is already assigned.")
        if recipe.status == "paused":
            return decision("waiting", "recipe_paused", "This recipe is paused; delivery is held.")
        return decision(
            "assigned", "assignment_pending", "Repair assigned; waiting for its terminal outcome."
        )
    if now - agent["last_seen_epoch"] > 30:
        return decision("waiting", "offline", "No endpoint heartbeat within 30 seconds.")
    if agent_id == candidate.source_agent_id and agent.get("incident_id") == candidate.incident_id:
        return decision(
            "excluded", "source_incident", "The source incident cannot validate its own repair."
        )
    if agent["health"] == "healthy":
        return decision("excluded", "healthy", "The current functional probe is healthy.")
    if agent["health"] != "unhealthy":
        return decision("waiting", "health_unknown", "Waiting for a failing functional probe.")
    if not agent.get("incident_id") or agent.get("issue_key") != candidate.issue_key:
        return decision(
            "excluded", "no_matching_incident", "No current incident matches this issue."
        )
    if agent_id == candidate.source_agent_id and recipe.status != "validated":
        return decision(
            "excluded", "source_cannot_canary", "The source must wait for independent validation."
        )
    required = candidate.environment.model_dump()
    differences = [
        f"{label} {agent['environment'][key]} requires {required[key]}"
        for key, label in (
            ("app_id", "Application"),
            ("app_version", "App version"),
            ("platform", "Platform"),
        )
        if agent["environment"][key] != required[key]
    ]
    if differences:
        return decision("excluded", "environment_mismatch", "; ".join(differences) + ".")
    if recipe.status == "paused":
        return decision(
            "waiting", "recipe_paused", "This recipe is paused after an unverified result."
        )
    if recipe.status == "canary":
        return decision(
            "waiting", "canary_in_progress", "Waiting for the canary to verify recovery."
        )
    if previous is not None:
        return decision(
            "excluded",
            "already_attempted",
            "This recipe was already attempted for this incident.",
            previous.role,
        )
    return decision(
        "eligible",
        "canary_available" if recipe.status == "candidate" else "follower_ready",
        "Matching unhealthy incident; eligible for independent canary validation."
        if recipe.status == "candidate"
        else "Matching unhealthy incident; canary verification permits follower repair.",
        "canary" if recipe.status == "candidate" else "follower",
    )


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
                CREATE INDEX IF NOT EXISTS commands_agent ON commands (agent_id);
                CREATE INDEX IF NOT EXISTS assignments_pending_expiry
                    ON assignments (expires_at) WHERE state='pending';
                CREATE INDEX IF NOT EXISTS assignments_pending_agent
                    ON assignments (agent_id) WHERE state='pending';
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

    @staticmethod
    def _require_reset_complete(db: sqlite3.Connection, agent_id: str) -> None:
        # A reset may arrive while an endpoint is still finishing its previous tick.
        # Its outbox must not repopulate the registry before the endpoint has cleared
        # its local state and acknowledged the reset command.
        rows = db.execute("SELECT data,ack FROM commands WHERE agent_id=?", (agent_id,))
        for row in rows:
            if json.loads(row["data"])["kind"] == "reset" and (
                row["ack"] is None or not json.loads(row["ack"])["success"]
            ):
                raise RegistryError(409, "Endpoint reset is pending; discard pre-reset evidence")

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
            self._require_reset_complete(db, report.agent_id)
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
            self._require_reset_complete(db, candidate.source_agent_id)
            prior = db.execute(
                "SELECT recipe_id,data FROM candidates WHERE id=?", (candidate.candidate_id,)
            ).fetchone()
            if prior:
                if Candidate.model_validate_json(prior["data"]) != candidate:
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
            if Outcome.model_validate_json(existing[0]) != outcome:
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
            # The agent index scopes both pending commands and failed reset barriers.
            command_rows = db.execute(
                "SELECT data,ack FROM commands WHERE agent_id=? AND "
                "(ack IS NULL OR (json_extract(ack,'$.success')=0 "
                "AND json_extract(data,'$.kind')='reset')) ORDER BY rowid",
                (agent_id,),
            ).fetchall()
            commands = [
                DemoCommand.model_validate_json(row["data"])
                for row in command_rows
                if row["ack"] is None
            ]
            reset_pending = any(json.loads(row["data"])["kind"] == "reset" for row in command_rows)
            paused = self._paused(db)
            pending_row = db.execute(
                "SELECT data,recipe_id FROM assignments WHERE agent_id=? AND state='pending' "
                "ORDER BY rowid LIMIT 1",
                (agent_id,),
            ).fetchone()
            pending = Assignment.model_validate_json(pending_row["data"]) if pending_row else None
            if pending is not None:
                recipe = Recipe.model_validate_json(
                    db.execute(
                        "SELECT data FROM recipes WHERE id=?", (pending.recipe.recipe_id,)
                    ).fetchone()[0]
                )
                decision = _eligibility(
                    agent,
                    recipe,
                    paused=paused,
                    command_pending=bool(commands),
                    reset_pending=reset_pending,
                    pending=pending,
                    previous=None,
                    outcome=None,
                    now=time.time(),
                )
                return Work(
                    commands=commands,
                    paused=paused,
                    assignment=pending if decision.status == "assigned" else None,
                )
            # Recipe-independent rejection is cheap on the polling hot path.
            # Keep this after pending redelivery: a recovering endpoint can still
            # need its existing assignment to flush a durable terminal outcome.
            if (
                paused
                or commands
                or reset_pending
                or agent["health"] != "unhealthy"
                or not agent.get("incident_id")
                or not agent.get("issue_key")
                or time.time() - agent["last_seen_epoch"] > 30
            ):
                return Work(commands=commands, paused=paused)
            # One indexed lookup for this incident, not one history query per recipe.
            history = {}
            if agent.get("incident_id"):
                for row in db.execute(
                    "SELECT a.recipe_id,a.data,o.data AS outcome FROM assignments a "
                    "LEFT JOIN outcomes o ON o.id=a.id WHERE a.agent_id=? AND a.incident_id=?",
                    (agent_id, agent["incident_id"]),
                ):
                    history[row["recipe_id"]] = (
                        Assignment.model_validate_json(row["data"]),
                        Outcome.model_validate_json(row["outcome"]) if row["outcome"] else None,
                    )
            for row in db.execute("SELECT data FROM recipes ORDER BY rowid"):
                recipe = Recipe.model_validate_json(row[0])
                previous, outcome = history.get(recipe.recipe_id, (None, None))
                decision = _eligibility(
                    agent,
                    recipe,
                    paused=paused,
                    command_pending=bool(commands),
                    reset_pending=reset_pending,
                    pending=None,
                    previous=previous,
                    outcome=outcome,
                    now=time.time(),
                )
                if decision.reason == "environment_mismatch":
                    marker = f"incompatible:{recipe.recipe_id}:{agent_id}:{agent['incident_id']}"
                    if not db.execute("SELECT 1 FROM meta WHERE key=?", (marker,)).fetchone():
                        db.execute("INSERT INTO meta (key,value) VALUES (?, 'true')", (marker,))
                        self._event(
                            db,
                            "repair_incompatible",
                            f"Repair skipped: {decision.reason_detail}",
                            agent_id,
                            recipe.recipe_id,
                        )
                if decision.status != "eligible":
                    continue
                role = decision.role
                assert role is not None  # Eligible decisions always name the delivery role.
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
            return Work(commands=commands, paused=paused)

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
        """Return history plus a live eligibility preview for the newest recipe.

        `eligibility_recipe_id` names that recipe, or is null without recipes.
        Only the named recipe has an `eligibility` list; older recipe objects omit
        the key (not an empty list). This bounds preview work/output to O(agents),
        independently of recipe history, while preserving all historical evidence.
        """
        with self._db() as db:
            self._expire_assignments(db)
            now = time.time()
            paused = self._paused(db)
            agents = [
                json.loads(row[0]) for row in db.execute("SELECT data FROM agents ORDER BY id")
            ]
            recipes = [
                Recipe.model_validate_json(row[0])
                for row in db.execute("SELECT data FROM recipes ORDER BY rowid DESC")
            ]
            outcomes = [
                Outcome.model_validate_json(row[0])
                for row in db.execute("SELECT data FROM outcomes ORDER BY rowid DESC")
            ]
            outcomes_by_assignment = {outcome.assignment_id: outcome for outcome in outcomes}
            # Group history once for O(1) lookup per endpoint in the one preview.
            history = {}
            pending_by_agent: dict[str, Assignment] = {}
            roles_by_assignment = {}
            for row in db.execute("SELECT data,state FROM assignments ORDER BY rowid"):
                assignment = Assignment.model_validate_json(row["data"])
                roles_by_assignment[assignment.assignment_id] = assignment.role
                history[
                    (assignment.agent_id, assignment.incident_id, assignment.recipe.recipe_id)
                ] = assignment
                if row["state"] == "pending":
                    pending_by_agent.setdefault(assignment.agent_id, assignment)
            command_agents = set()
            reset_agents = set()
            for row in db.execute("SELECT agent_id,data,ack FROM commands"):
                if row["ack"] is None:
                    command_agents.add(row["agent_id"])
                if json.loads(row["data"])["kind"] == "reset" and (
                    row["ack"] is None or not json.loads(row["ack"])["success"]
                ):
                    reset_agents.add(row["agent_id"])
            recipe_data = [recipe.model_dump(mode="json") for recipe in recipes]
            eligibility_recipe_id = recipes[0].recipe_id if recipes else None
            if recipes:
                recipe = recipes[0]
                decisions = []
                for agent in agents:
                    previous = history.get(
                        (agent["agent_id"], agent.get("incident_id"), recipe.recipe_id)
                    )
                    outcome = (
                        outcomes_by_assignment.get(previous.assignment_id) if previous else None
                    )
                    decisions.append(
                        asdict(
                            _eligibility(
                                agent,
                                recipe,
                                paused=paused,
                                command_pending=agent["agent_id"] in command_agents,
                                reset_pending=agent["agent_id"] in reset_agents,
                                pending=pending_by_agent.get(agent["agent_id"]),
                                previous=previous,
                                outcome=outcome,
                                now=now,
                            )
                        )
                    )
                recipe_data[0]["eligibility"] = decisions
            for agent in agents:
                agent["online"] = now - agent.pop("last_seen_epoch") <= 30
            return {
                "agents": agents,
                "recipes": recipe_data,
                "eligibility_recipe_id": eligibility_recipe_id,
                "outcomes": [
                    {
                        **outcome.model_dump(mode="json"),
                        "role": roles_by_assignment.get(outcome.assignment_id),
                    }
                    for outcome in outcomes
                ],
                "events": [
                    json.loads(row[0])
                    for row in db.execute("SELECT data FROM events ORDER BY seq DESC LIMIT 200")
                ],
                "paused": paused,
                "demo_mode": demo_mode,
            }
