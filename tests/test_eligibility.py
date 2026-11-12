"""Explain actual registry decisions without reserving work or replaying old incidents."""

import json
import sqlite3
from contextlib import contextmanager

import pytest

from swarm import registry as registry_module
from swarm.models import (
    Candidate,
    CommandAck,
    Environment,
    Heartbeat,
    IssueReport,
    Outcome,
    Recipe,
    Registration,
    Work,
    issue_key,
)
from swarm.registry import Registry, RegistryError


def fail(registry, agent_id, environment=None, key=None):
    environment = environment or Environment(platform="darwin")
    key = key or issue_key()
    registry.heartbeat(agent_id, Heartbeat(health="unhealthy", issue_key=key))
    incident = IssueReport(
        agent_id=agent_id, issue_key=key, environment=environment, summary="Cache failed"
    )
    registry.report_incident(incident)
    return incident


@pytest.fixture
def fleet(tmp_path):
    registry = Registry(tmp_path / "registry.sqlite")
    incidents = {}
    for name in ("a", "b", "c"):
        registry.register(
            Registration(agent_id=name, label=name, environment=Environment(platform="darwin"))
        )
        incidents[name] = fail(registry, name)
    registry.heartbeat("a", Heartbeat(health="healthy"))
    candidate = Candidate(
        source_agent_id="a",
        incident_id=incidents["a"].incident_id,
        issue_key=issue_key(),
        environment=incidents["a"].environment,
        evidence_ids=["failure", "change", "recovery"],
        explanation="Manual repair followed by three functional probes",
        verification_count=3,
    )
    recipe = registry.propose(candidate)
    return registry, candidate, recipe


def decisions(registry, recipe_id=None):
    recipes = registry.snapshot(False)["recipes"]
    recipe = next(r for r in recipes if recipe_id is None or r["recipe_id"] == recipe_id)
    return {entry["agent_id"]: entry for entry in recipe["eligibility"]}


def finish(registry, assignment, status="verified"):
    result = Outcome(
        assignment_id=assignment.assignment_id,
        agent_id=assignment.agent_id,
        recipe_id=assignment.recipe.recipe_id,
        incident_id=assignment.incident_id,
        status=status,
        checks_passed=3 if status == "verified" else 0,
        detail="Functional checks completed" if status == "verified" else "Recovery not verified",
    )
    registry.record_outcome(result)
    return result


def database_contents(registry):
    with sqlite3.connect(registry.path) as db:
        return {
            table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("recipes", "assignments", "outcomes", "events", "meta")
        }


def test_preview_does_not_reserve_canary_or_mutate_registry(fleet):
    registry, _, _ = fleet
    before = database_contents(registry)
    for _ in range(3):
        preview = decisions(registry)
        assert preview["a"]["reason"] == "source_incident"
        assert preview["b"]["status"] == preview["c"]["status"] == "eligible"
        assert preview["b"]["role"] == preview["c"]["role"] == "canary"
    assert database_contents(registry) == before
    canary = registry.work("b").assignment
    assert canary.role == preview["b"]["role"]
    preview = decisions(registry)
    assert preview["b"]["status"] == "assigned"
    assert preview["b"]["reason"] == "assignment_pending"
    assert preview["c"]["reason"] == "canary_in_progress"
    assert registry.work("c").assignment is None
    finish(registry, canary)
    preview = decisions(registry)
    assert preview["b"]["reason"] == "outcome_verified"
    assert preview["c"]["role"] == "follower"
    assert registry.work("c").assignment.role == preview["c"]["role"]


def test_exact_environment_exclusion_is_explained_without_preview_events(fleet):
    registry, _, _ = fleet
    environment = Environment(platform="linux", app_version="2.0.0")
    registry.register(Registration(agent_id="d", label="Different", environment=environment))
    fail(registry, "d", environment)
    before = database_contents(registry)
    decision = decisions(registry)["d"]
    assert decision == {
        "agent_id": "d",
        "status": "excluded",
        "reason": "environment_mismatch",
        "reason_detail": "App version 2.0.0 requires 1.0.0; Platform linux requires darwin.",
        "role": None,
    }
    assert database_contents(registry) == before
    assert registry.work("d").assignment is None
    assert registry.work("d").assignment is None
    events = registry.snapshot(False)["events"]
    assert sum(event["kind"] == "repair_incompatible" for event in events) == 1


@pytest.mark.parametrize(
    ("health", "key", "reason", "status"),
    [
        ("healthy", None, "healthy", "excluded"),
        ("unknown", None, "health_unknown", "waiting"),
        ("unhealthy", "other-issue", "no_matching_incident", "excluded"),
    ],
)
def test_health_and_current_issue_gate_preview_and_dispatch(fleet, health, key, reason, status):
    registry, _, _ = fleet
    registry.heartbeat("b", Heartbeat(health=health, issue_key=key))
    assert decisions(registry)["b"]["reason"] == reason
    assert decisions(registry)["b"]["status"] == status
    assert registry.work("b").assignment is None


def test_missing_incident_offline_and_source_cannot_validate_later_incident(fleet, monkeypatch):
    registry, _, _ = fleet
    registry.register(Registration(agent_id="new", label="New"))
    registry.heartbeat("new", Heartbeat(health="unhealthy", issue_key=issue_key()))
    assert decisions(registry)["new"]["reason"] == "no_matching_incident"
    assert registry.work("new").assignment is None
    fail(registry, "a")
    assert decisions(registry)["a"]["reason"] == "source_cannot_canary"
    assert registry.work("a").assignment is None
    with registry._db() as db:
        agent = registry._agent(db, "b")
        last_seen = agent["last_seen_epoch"]
    monkeypatch.setattr("swarm.registry.time.time", lambda: last_seen + 31)
    assert decisions(registry)["b"]["reason"] == "offline"
    assert registry.work("b").assignment is None


def test_pending_redelivery_preserves_durable_outcome_and_respects_delivery_gates(
    fleet, monkeypatch
):
    registry, _, _ = fleet
    assignment = registry.work("b").assignment
    # Recovery heartbeat can arrive before delivery of the terminal outcome.
    registry.heartbeat("b", Heartbeat(health="healthy"))
    with registry._db() as db:
        last_seen = registry._agent(db, "b")["last_seen_epoch"]
    monkeypatch.setattr("swarm.registry.time.time", lambda: last_seen + 31)
    assert decisions(registry)["b"]["status"] == "assigned"
    assert registry.work("b").assignment == assignment
    registry.pause(True)
    assert decisions(registry)["b"]["reason"] == "global_paused"
    assert registry.work("b").assignment is None
    registry.pause(False)
    registry.enqueue("fail_next_verification", ["b"])
    assert decisions(registry)["b"]["reason"] == "command_pending"
    work = registry.work("b")
    assert work.assignment is None
    registry.acknowledge("b", CommandAck(command_id=work.commands[0].command_id, success=True))
    assert registry.work("b").assignment == assignment
    with registry._db() as db:
        recipe = Recipe.model_validate_json(db.execute("SELECT data FROM recipes").fetchone()[0])
        recipe.status = "paused"
        registry._save_recipe(db, recipe)
    assert decisions(registry)["b"]["reason"] == "recipe_paused"
    assert registry.work("b").assignment is None


def test_reset_acknowledgement_gate_applies_to_preview_and_dispatch(fleet):
    registry, _, _ = fleet
    registry.enqueue("reset", ["b"])
    assert decisions(registry)["b"]["reason"] == "reset_pending"
    work = registry.work("b")
    assert work.assignment is None
    registry.acknowledge("b", CommandAck(command_id=work.commands[0].command_id, success=False))
    assert decisions(registry)["b"]["reason"] == "reset_pending"
    assert registry.work("b").assignment is None


@pytest.mark.parametrize("status", ["failed", "rejected", "inconclusive"])
def test_current_terminal_outcome_explains_paused_rollout(fleet, status):
    registry, _, _ = fleet
    assignment = registry.work("b").assignment
    finish(registry, assignment, status)
    preview = decisions(registry)
    assert preview["b"]["status"] == "completed"
    assert preview["b"]["reason"] == f"outcome_{status}"
    assert preview["c"]["reason"] == "recipe_paused"
    assert registry.work("b").assignment is None
    assert registry.work("c").assignment is None


def test_new_incident_does_not_reuse_old_completion_or_role(fleet):
    registry, _, _ = fleet
    canary = registry.work("b").assignment
    first_result = finish(registry, canary)
    assert decisions(registry)["b"]["role"] == "canary"
    fail(registry, "b")
    preview = decisions(registry)
    assert preview["b"]["status"] == "eligible"
    assert preview["b"]["role"] == "follower"
    follower = registry.work("b").assignment
    second_result = finish(registry, follower)
    assert decisions(registry)["b"]["role"] == "follower"
    outcomes = {item["assignment_id"]: item for item in registry.snapshot(False)["outcomes"]}
    assert outcomes[first_result.assignment_id]["role"] == "canary"
    assert outcomes[second_result.assignment_id]["role"] == "follower"
    fail(registry, "a")
    assert decisions(registry)["a"]["role"] == "follower"
    assert registry.work("a").assignment.role == "follower"


def test_legacy_candidate_and_outcome_defaults_remain_idempotent(fleet):
    registry, candidate, recipe = fleet
    assignment = registry.work("b").assignment
    outcome = finish(registry, assignment)
    with registry._db() as db:
        for table, identifier in (
            ("candidates", candidate.candidate_id),
            ("outcomes", outcome.assignment_id),
        ):
            payload = json.loads(
                db.execute(f"SELECT data FROM {table} WHERE id=?", (identifier,)).fetchone()[0]
            )
            payload.pop("evidence", None)
            db.execute(
                f"UPDATE {table} SET data=? WHERE id=?", (json.dumps(payload, indent=2), identifier)
            )
    assert registry.propose(candidate).recipe_id == recipe.recipe_id
    registry.record_outcome(outcome)
    with pytest.raises(RegistryError, match="different proposal"):
        registry.propose(candidate.model_copy(update={"explanation": "Different proposal"}))
    with pytest.raises(RegistryError, match="different terminal outcome"):
        registry.record_outcome(outcome.model_copy(update={"detail": "Different result"}))
    assert len(registry.snapshot(False)["outcomes"]) == 1


@pytest.mark.parametrize("mode", ["healthy", "no_incident", "offline", "paused", "command"])
def test_idle_poll_does_not_read_growing_recipe_catalog(fleet, monkeypatch, mode):
    registry, _, recipe = fleet
    if mode == "healthy":
        registry.heartbeat("b", Heartbeat(health="healthy"))
    elif mode == "no_incident":
        with registry._db() as db:
            agent = registry._agent(db, "b")
            agent["incident_id"] = None
            registry._save_agent(db, agent)
    elif mode == "offline":
        with registry._db() as db:
            agent = registry._agent(db, "b")
            agent["last_seen_epoch"] = 0
            registry._save_agent(db, agent)
    elif mode == "paused":
        registry.pause(True)
    else:
        registry.enqueue("inject_fault", ["b"])

    original_db = registry._db
    statements = []

    @contextmanager
    def traced_db():
        with original_db() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(registry, "_db", traced_db)
    before = registry.work("b")
    with original_db() as db:
        for index in range(500):
            extra = recipe.model_copy(update={"recipe_id": f"extra-{index}"})
            db.execute(
                "INSERT INTO recipes (id,cohort,data) VALUES (?,?,?)",
                (extra.recipe_id, f"cohort-{index}", extra.model_dump_json()),
            )
    statements.clear()
    assert registry.work("b") == before
    assert not any("FROM recipes" in statement for statement in statements)
    assert not any("LEFT JOIN outcomes" in statement for statement in statements)


def test_snapshot_bounds_preview_to_newest_recipe_and_reads_histories_once(fleet, monkeypatch):
    registry, _, recipe = fleet
    finish(registry, registry.work("b").assignment)
    original_eligibility = registry_module._eligibility
    calls = []

    def counted_eligibility(agent, recipe, **kwargs):
        calls.append((agent["agent_id"], recipe.recipe_id))
        return original_eligibility(agent, recipe, **kwargs)

    monkeypatch.setattr(registry_module, "_eligibility", counted_eligibility)
    initial = registry.snapshot(False)
    assert initial["eligibility_recipe_id"] == recipe.recipe_id
    assert len(calls) == 3
    calls.clear()
    with registry._db() as db:
        for index in range(500):
            extra = recipe.model_copy(update={"recipe_id": f"extra-{index}"})
            db.execute(
                "INSERT INTO recipes (id,cohort,data) VALUES (?,?,?)",
                (extra.recipe_id, f"cohort-{index}", extra.model_dump_json()),
            )
    original_db = registry._db
    statements = []

    @contextmanager
    def traced_db():
        with original_db() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(registry, "_db", traced_db)
    snapshot = registry.snapshot(False)
    assert len(snapshot["recipes"]) == 501
    assert snapshot["eligibility_recipe_id"] == "extra-499"
    assert len(snapshot["recipes"][0]["eligibility"]) == 3
    assert all("eligibility" not in item for item in snapshot["recipes"][1:])
    assert len(calls) == 3
    assert {recipe_id for _, recipe_id in calls} == {"extra-499"}
    assert snapshot["outcomes"] == initial["outcomes"]
    assert sum("FROM commands" in statement for statement in statements) == 1
    assert sum("FROM outcomes" in statement for statement in statements) == 1
    # One expiry lookup plus one grouped history read, independent of recipe count.
    assert sum("FROM assignments" in statement for statement in statements) == 2
    assert registry.work("a") == Work()


def test_snapshot_without_recipes_has_no_preview_target(tmp_path):
    registry = Registry(tmp_path / "registry.sqlite")
    registry.register(Registration(agent_id="a", label="A"))
    snapshot = registry.snapshot(False)
    assert snapshot["eligibility_recipe_id"] is None
    assert snapshot["recipes"] == []
