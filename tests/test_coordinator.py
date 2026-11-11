from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from swarm.coordinator import create_app
from swarm.models import Candidate, Environment, IssueReport, Registration, issue_key


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "fleet.sqlite", demo_mode=True)) as value:
        yield value


def register(client, name, version="1.0.0", os_name="darwin"):
    env = Environment(platform=os_name, app_version=version)
    response = client.post(
        "/api/agents/register",
        json=Registration(agent_id=name, label=name, environment=env).model_dump(),
    )
    assert response.status_code == 200
    return env


def report_failure(client, name, env):
    assert (
        client.post(
            f"/api/agents/{name}/heartbeat", json={"health": "unhealthy", "issue_key": issue_key()}
        ).status_code
        == 200
    )
    incident = IssueReport(
        agent_id=name,
        issue_key=issue_key(),
        summary="Workspace cache cannot be parsed",
        environment=env,
        evidence_ids=["failure-1"],
    )
    assert client.post("/api/incidents", json=incident.model_dump()).status_code == 200
    return incident


def make_fleet(client, names=("a", "b", "c")):
    incidents = {}
    for name in names:
        env = register(client, name)
        incidents[name] = report_failure(client, name, env)
    assert client.post("/api/agents/a/heartbeat", json={"health": "healthy"}).status_code == 200
    candidate = Candidate(
        source_agent_id="a",
        incident_id=incidents["a"].incident_id,
        issue_key=issue_key(),
        environment=incidents["a"].environment,
        evidence_ids=["failure-1", "cache-change-2", "health-3"],
        explanation="Manual cache quarantine was followed by 3 successful workspace operations",
        verification_count=3,
    )
    response = client.post("/api/candidates", json=candidate.model_dump())
    assert response.status_code == 200, response.text
    return incidents, candidate, response.json()


def work(client, agent):
    response = client.get(f"/api/agents/{agent}/work")
    assert response.status_code == 200, response.text
    return response.json()


def outcome(assignment, status="verified"):
    return {
        "assignment_id": assignment["assignment_id"],
        "agent_id": assignment["agent_id"],
        "recipe_id": assignment["recipe"]["recipe_id"],
        "incident_id": assignment["incident_id"],
        "status": status,
        "checks_total": 3,
        "checks_passed": 3 if status == "verified" else 0,
        "detail": "Functional work completed"
        if status == "verified"
        else "Functional probe still fails",
        "observed_at": "2026-11-11T10:00:00+00:00",
    }


def test_canary_must_verify_before_follower_and_outcome_is_idempotent(client):
    _, _, recipe = make_fleet(client)
    canary = work(client, "b")["assignment"]
    assert canary["role"] == "canary"
    assert work(client, "b")["assignment"]["assignment_id"] == canary["assignment_id"]
    assert work(client, "c")["assignment"] is None
    result = outcome(canary)
    assert client.post("/api/outcomes", json=result).status_code == 200
    assert client.post("/api/outcomes", json=result).status_code == 200
    follower = work(client, "c")["assignment"]
    assert follower["role"] == "follower"
    assert client.post("/api/outcomes", json=outcome(follower)).status_code == 200
    state = client.get("/api/state").json()
    assert state["recipes"][0]["recipe_id"] == recipe["recipe_id"]
    assert state["recipes"][0]["successes"] == 2
    assert len(state["outcomes"]) == 2
    assert work(client, "b")["assignment"] is None


def test_simultaneous_pollers_get_exactly_one_canary(client):
    make_fleet(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda name: work(client, name), ["b", "c"]))
    assignments = [result["assignment"] for result in results if result["assignment"]]
    assert len(assignments) == 1
    assert assignments[0]["role"] == "canary"


def test_failure_pauses_and_new_candidate_cannot_bypass_gate(client):
    _, candidate, recipe = make_fleet(client)
    canary = work(client, "b")["assignment"]
    assert client.post("/api/outcomes", json=outcome(canary, "failed")).status_code == 200
    assert work(client, "c")["assignment"] is None
    candidate.candidate_id = "new-candidate-id"
    duplicate = client.post("/api/candidates", json=candidate.model_dump())
    assert duplicate.status_code == 200
    assert duplicate.json()["recipe_id"] == recipe["recipe_id"]
    assert duplicate.json()["status"] == "paused"
    client.post("/api/control/pause", json={"paused": False})
    assert work(client, "c")["assignment"] is None


def test_global_pause_suppresses_even_pending_delivery(client):
    make_fleet(client)
    issued = work(client, "b")["assignment"]
    assert client.post("/api/control/pause", json={"paused": True}).status_code == 200
    assert work(client, "b")["assignment"] is None
    assert work(client, "b")["paused"] is True
    # A repair already executing before pause can safely report its final result.
    assert client.post("/api/outcomes", json=outcome(issued)).status_code == 200
    assert work(client, "c")["assignment"] is None
    client.post("/api/control/pause", json={"paused": False})
    assert work(client, "c")["assignment"]["role"] == "follower"


def test_incompatible_environment_is_skipped_and_event_deduplicates(client):
    make_fleet(client)
    env = register(client, "incompatible", version="2.0.0")
    report_failure(client, "incompatible", env)
    assert work(client, "incompatible")["assignment"] is None
    assert work(client, "incompatible")["assignment"] is None
    state = client.get("/api/state").json()
    rejected = [item for item in state["events"] if item["kind"] == "repair_incompatible"]
    assert len(rejected) == 1


def test_incomplete_or_mismatched_outcomes_do_not_promote(client):
    make_fleet(client)
    assignment = work(client, "b")["assignment"]
    result = outcome(assignment)
    result["agent_id"] = "c"
    assert client.post("/api/outcomes", json=result).status_code == 409
    result = outcome(assignment)
    result.update(checks_passed=1, checks_total=1)
    assert client.post("/api/outcomes", json=result).status_code == 422
    result.update(checks_passed=4, checks_total=3)
    assert client.post("/api/outcomes", json=result).status_code == 422
    assert work(client, "c")["assignment"] is None
    assert client.get("/api/state").json()["outcomes"] == []


def test_candidate_requires_recovered_source_and_real_incident(client):
    env = register(client, "a")
    incident = report_failure(client, "a", env)
    proposal = Candidate(
        source_agent_id="a",
        incident_id=incident.incident_id,
        issue_key=issue_key(),
        environment=env,
        explanation="Cache reset",
        evidence_ids=["evidence"],
        verification_count=3,
    )
    assert client.post("/api/candidates", json=proposal.model_dump()).status_code == 409
    client.post("/api/agents/a/heartbeat", json={"health": "healthy"})
    proposal.incident_id = "not-recorded"
    assert client.post("/api/candidates", json=proposal.model_dump()).status_code == 409
    proposal.incident_id = incident.incident_id
    assert client.post("/api/candidates", json=proposal.model_dump()).status_code == 200


def test_reset_invalidates_old_results_and_dispatches_reset_commands(client):
    make_fleet(client)
    assignment = work(client, "b")["assignment"]
    assert client.post("/api/demo/reset", json={}).status_code == 200
    assert client.post("/api/outcomes", json=outcome(assignment)).status_code == 409
    state = client.get("/api/state").json()
    assert state["recipes"] == []
    assert state["outcomes"] == []
    assert len(state["agents"]) == 3
    command = work(client, "a")["commands"][0]
    assert command["kind"] == "reset"
    ack = {"command_id": command["command_id"], "success": True, "detail": "Restored demo"}
    assert client.post("/api/agents/b/commands/ack", json=ack).status_code == 409
    assert client.post("/api/agents/a/commands/ack", json=ack).status_code == 200
    assert client.post("/api/agents/a/commands/ack", json=ack).status_code == 200
    assert work(client, "a")["commands"] == []


def test_timeout_pauses_rollout_and_rejects_late_success(client):
    make_fleet(client)
    assignment = work(client, "b")["assignment"]
    with client.app.state.registry._db() as db:
        db.execute("UPDATE assignments SET expires_at=0")
    state = client.get("/api/state").json()
    assert state["recipes"][0]["status"] == "paused"
    assert state["outcomes"][0]["status"] == "inconclusive"
    assert client.post("/api/outcomes", json=outcome(assignment)).status_code == 409
    assert work(client, "c")["assignment"] is None


def test_persistence_preserves_canary_gate_across_restart(tmp_path):
    path = tmp_path / "fleet.sqlite"
    with TestClient(create_app(path)) as first:
        make_fleet(first)
        assignment = work(first, "b")["assignment"]
    with TestClient(create_app(path)) as second:
        assert work(second, "b")["assignment"]["assignment_id"] == assignment["assignment_id"]
        assert work(second, "c")["assignment"] is None
        assert second.post("/api/outcomes", json=outcome(assignment)).status_code == 200
        assert work(second, "c")["assignment"] is not None


def test_auth_applies_to_all_api_and_demo_routes_require_flag(tmp_path):
    with TestClient(create_app(tmp_path / "fleet.sqlite", token="test-token")) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/state").status_code == 401
        assert client.get("/api/state", headers={"X-Swarm-Token": "wrong"}).status_code == 401
        assert client.get("/api/state", headers={"X-Swarm-Token": "test-token"}).status_code == 200
        assert (
            client.post(
                "/api/demo/reset", json={}, headers={"X-Swarm-Token": "test-token"}
            ).status_code
            == 403
        )


def test_commands_are_retained_until_ack_and_gate_repairs(client):
    make_fleet(client)
    assert client.post("/api/demo/fail-verification", json={"agent_ids": ["b"]}).status_code == 200
    pending = work(client, "b")
    assert pending["assignment"] is None
    command = pending["commands"][0]
    assert work(client, "b")["commands"][0] == command
    ack = {"command_id": command["command_id"], "success": True}
    assert client.post("/api/agents/b/commands/ack", json=ack).status_code == 200
    assert work(client, "b")["assignment"] is not None


def test_environment_change_invalidates_pending_assignment(client):
    make_fleet(client)
    pending = work(client, "b")["assignment"]
    register(client, "b", version="2.0.0")
    assert client.post("/api/outcomes", json=outcome(pending)).status_code == 409
    assert client.get("/api/state").json()["recipes"][0]["status"] == "paused"


def test_unknown_target_is_atomic_and_does_not_queue_partial_command(client):
    register(client, "a")
    assert client.post("/api/demo/fault", json={"agent_ids": ["a", "missing"]}).status_code == 404
    assert work(client, "a")["commands"] == []


def test_late_outcome_alone_commits_timeout_decision(client):
    make_fleet(client)
    assignment = work(client, "b")["assignment"]
    with client.app.state.registry._db() as db:
        db.execute("UPDATE assignments SET expires_at=0")
    assert client.post("/api/outcomes", json=outcome(assignment)).status_code == 409
    with client.app.state.registry._db() as db:
        assert db.execute("SELECT state FROM assignments").fetchone()[0] == "complete"
        assert db.execute("SELECT count(*) FROM outcomes").fetchone()[0] == 1


def test_origin_can_reuse_independently_validated_recipe_for_later_incident(client):
    incidents, _, _ = make_fleet(client)
    canary = work(client, "b")["assignment"]
    assert client.post("/api/outcomes", json=outcome(canary)).status_code == 200
    later = report_failure(client, "a", incidents["a"].environment)
    assignment = work(client, "a")["assignment"]
    assert assignment["role"] == "follower"
    assert assignment["incident_id"] == later.incident_id
