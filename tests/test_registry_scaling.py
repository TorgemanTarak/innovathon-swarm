"""Measure coordinator SQLite work with synthetic history, not wall-clock throughput."""

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest

from swarm.models import (
    Assignment,
    Candidate,
    CommandAck,
    DemoCommand,
    Environment,
    IssueReport,
    Recipe,
    Registration,
    Work,
    issue_key,
)
from swarm.registry import Registry


def add_history(registry: Registry, kind: str, start: int, stop: int) -> None:
    """Insert typed historical payloads directly to isolate lookup cost from setup work."""
    with registry._db() as db:
        if kind == "commands":
            rows = []
            for index in range(start, stop):
                command = DemoCommand(command_id=f"command-{index}", kind="reset")
                rows.append((command.command_id, f"other-{index}", command.model_dump_json()))
            db.executemany("INSERT INTO commands (id,agent_id,data) VALUES (?,?,?)", rows)
            return
        recipe = Recipe(
            recipe_id="historical-recipe",
            candidate=Candidate(
                source_agent_id="source",
                incident_id="source-incident",
                issue_key=issue_key(),
                environment=Environment(platform="linux"),
                evidence_ids=["synthetic-evidence"],
                explanation="Synthetic coordinator history; no app probes were performed",
                verification_count=3,
            ),
        )
        rows = []
        for index in range(start, stop):
            agent_id = "poller" if kind == "own_complete_assignments" else f"other-{index}"
            assignment = Assignment(
                assignment_id=f"{kind}-{index}",
                agent_id=agent_id,
                incident_id=f"incident-{kind}-{index}",
                recipe=recipe,
                role="follower",
            )
            pending = kind == "pending_assignments"
            rows.append(
                (
                    assignment.assignment_id,
                    agent_id,
                    assignment.incident_id,
                    recipe.recipe_id,
                    assignment.model_dump_json(),
                    "pending" if pending else "complete",
                    2000 if pending else 0,
                )
            )
        db.executemany(
            "INSERT INTO assignments (id,agent_id,incident_id,recipe_id,data,state,expires_at) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def sql_steps(registry: Registry, monkeypatch, operation: Callable[[], None]) -> int:
    """Count executed SQLite VM instructions, excluding connection/transaction setup."""
    count = 0
    original = registry._db

    def progress() -> int:
        nonlocal count
        count += 1
        return 0

    @contextmanager
    def measured_db() -> Iterator[sqlite3.Connection]:
        with original() as db:
            db.set_progress_handler(progress, 1)
            try:
                yield db
            finally:
                db.set_progress_handler(None, 0)

    with monkeypatch.context() as patch:
        patch.setattr(registry, "_db", measured_db)
        operation()
    return count


def assert_idle(registry: Registry) -> None:
    assert registry.work("poller") == Work()


@pytest.mark.parametrize(
    "kind",
    ["commands", "complete_assignments", "pending_assignments", "own_complete_assignments"],
)
def test_idle_poll_does_not_scan_growing_history(tmp_path, monkeypatch, kind):
    monkeypatch.setattr("swarm.registry.time.time", lambda: 1000)
    registry = Registry(tmp_path / "registry.sqlite")
    registry.register(Registration(agent_id="poller", label="Idle endpoint"))
    add_history(registry, kind, 0, 100)
    small = sql_steps(registry, monkeypatch, lambda: assert_idle(registry))
    add_history(registry, kind, 100, 2500)
    large = sql_steps(registry, monkeypatch, lambda: assert_idle(registry))
    # Allow planner/version overhead, but reject a scan proportional to 25x the history.
    assert large <= small * 2 + 100, (kind, small, large)


def test_reset_evidence_barrier_ignores_other_endpoints_command_history(tmp_path, monkeypatch):
    registry = Registry(tmp_path / "registry.sqlite")
    registration = Registration(agent_id="poller", label="Source endpoint")
    registry.register(registration)
    incident = IssueReport(
        agent_id="poller",
        environment=registration.environment,
        issue_key=issue_key(),
        summary="Synthetic incident for evidence barrier lookup",
    )
    registry.report_incident(incident)
    add_history(registry, "commands", 0, 100)
    small = sql_steps(registry, monkeypatch, lambda: registry.report_incident(incident))
    add_history(registry, "commands", 100, 2500)
    large = sql_steps(registry, monkeypatch, lambda: registry.report_incident(incident))
    assert large <= small * 2 + 100, (small, large)


def test_existing_database_gains_bounded_lookups_without_losing_commands(tmp_path, monkeypatch):
    monkeypatch.setattr("swarm.registry.time.time", lambda: 1000)
    path = tmp_path / "registry.sqlite"
    registry = Registry(path)
    registry.register(Registration(agent_id="poller", label="Idle endpoint"))
    registry.register(Registration(agent_id="waiting", label="Command recipient"))
    registry.enqueue("inject_fault", ["waiting"])
    registry.enqueue("manual_repair", ["waiting"])
    queued = registry.work("waiting").commands
    assert [command.kind for command in queued] == ["inject_fault", "manual_repair"]
    add_history(registry, "commands", 0, 2500)
    add_history(registry, "complete_assignments", 0, 2500)
    add_history(registry, "pending_assignments", 0, 2500)
    with registry._db() as db:
        # Recreate the previous schema's lookup behavior, preserving all table data
        # and automatic indexes for primary/unique constraints.
        indexes = db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ).fetchall()
        for row in indexes:
            name = row[0].replace('"', '""')
            db.execute(f'DROP INDEX "{name}"')
    legacy_steps = sql_steps(registry, monkeypatch, lambda: assert_idle(registry))

    Registry(path)  # Opening an existing database upgrades it.
    upgraded = Registry(path)  # Opening it again must remain safe and idempotent.
    upgraded_steps = sql_steps(upgraded, monkeypatch, lambda: assert_idle(upgraded))
    assert upgraded_steps * 10 < legacy_steps, (legacy_steps, upgraded_steps)
    assert upgraded.work("waiting").commands == queued
    upgraded.acknowledge("waiting", CommandAck(command_id=queued[0].command_id, success=True))
    assert upgraded.work("waiting").commands == queued[1:]
    with upgraded._db() as db:
        assert db.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 2502
        assert db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0] == 5000
