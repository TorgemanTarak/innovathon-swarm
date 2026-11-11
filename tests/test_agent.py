from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from swarm.agent import AgentConfig, EndpointAgent
from swarm.demo_app import create_app
from swarm.models import Assignment, Candidate, DemoCommand, Recipe, Work, issue_key
from swarm.repairs import cache_path, inject_fault, quarantine_cache, write_cache


class HubTransport(httpx.AsyncBaseTransport):
    def __init__(self, root: Path):
        self.demo = httpx.ASGITransport(app=create_app(root))
        self.work = Work()
        self.posts: list[tuple[str, dict]] = []
        self.outcome_failure = False
        self.probe_override: bool | None = None
        self.probes = 0

    async def handle_async_request(self, request):
        if request.url.host == "demo":
            self.probes += 1
            if self.probe_override is True:
                return httpx.Response(200, json={"ok": True, "projects": []})
            return await self.demo.handle_async_request(request)
        path = request.url.path
        if request.method == "GET" and path.endswith("/work"):
            return httpx.Response(200, json=self.work.model_dump())
        payload = json.loads(request.content)
        self.posts.append((path, payload))
        if path == "/api/outcomes" and self.outcome_failure:
            return httpx.Response(503, json={"detail": "temporary delivery failure"})
        if path.endswith("/commands/ack"):
            self.work.commands = [
                c for c in self.work.commands if c.command_id != payload["command_id"]
            ]
        return httpx.Response(200, json={"ok": True})

    def payloads(self, path):
        return [payload for endpoint, payload in self.posts if endpoint == path]


@pytest.fixture
async def endpoint(tmp_path):
    root = tmp_path / "device"
    transport = HubTransport(root)
    client = httpx.AsyncClient(transport=transport)
    config = AgentConfig("mac-b", "Mac B", root, "http://hub", "http://demo", interval=0.001)
    agent = EndpointAgent(config, client=client)
    yield agent, transport
    await agent.close()
    await client.aclose()


async def break_app(agent):
    await agent.tick()
    inject_fault(agent.root)
    await agent.tick()
    return agent.journal.get("incident")


def assignment_for(agent, incident, *, version="1.0.0"):
    environment = agent.environment.model_copy(update={"app_version": version})
    candidate = Candidate(
        source_agent_id="mac-a",
        incident_id="origin-incident",
        issue_key=issue_key(),
        environment=environment,
        explanation="Observed cache recovery",
        verification_count=3,
    )
    recipe = Recipe(candidate=candidate, status="canary", canary_agent_id=agent.config.agent_id)
    return Assignment(
        agent_id=agent.config.agent_id,
        incident_id=incident["incident_id"],
        recipe=recipe,
        role="canary",
    )


async def test_observed_manual_change_requires_three_real_successful_probes(endpoint):
    agent, hub = endpoint
    await break_app(agent)
    quarantine_cache(agent.root, "human")
    await agent.tick()
    await agent.tick()
    assert not hub.payloads("/api/candidates")
    await agent.tick()
    candidates = hub.payloads("/api/candidates")
    assert len(candidates) == 1
    assert candidates[0]["verification_count"] == 3
    assert len(candidates[0]["evidence_ids"]) == 3
    assert candidates[0]["investigator"] == "deterministic"
    await agent.tick()
    assert len(hub.payloads("/api/candidates")) == 1
    heartbeat_index = max(
        i
        for i, (path, _) in enumerate(
            hub.posts[
                : next(i for i, (path, _) in enumerate(hub.posts) if path == "/api/candidates")
            ]
        )
        if path.endswith("/heartbeat")
    )
    assert hub.posts[heartbeat_index][1]["health"] == "healthy"


async def test_recovery_without_observed_change_does_not_invent_candidate(endpoint):
    agent, hub = endpoint
    await break_app(agent)
    hub.probe_override = True
    for _ in range(4):
        await agent.tick()
    assert not hub.payloads("/api/candidates")


async def test_successful_repair_is_not_reexecuted_when_outcome_delivery_fails(endpoint):
    agent, hub = endpoint
    incident = await break_app(agent)
    assignment = assignment_for(agent, incident)
    hub.work.assignment = assignment
    hub.outcome_failure = True
    await agent.tick()
    assert hub.payloads("/api/outcomes")[-1]["status"] == "verified"
    backups = list((agent.root / "quarantine").iterdir())
    assert len(backups) == 1
    assert agent.journal.pending()
    hub.outcome_failure = False
    await agent.tick()
    assert list((agent.root / "quarantine").iterdir()) == backups
    assert not agent.journal.pending()
    assert not hub.payloads("/api/candidates")


async def test_failed_functional_verification_rolls_back_actual_cache(endpoint):
    agent, hub = endpoint
    incident = await break_app(agent)
    original = cache_path(agent.root).read_bytes()
    hub.work.assignment = assignment_for(agent, incident)
    hub.work.commands = [DemoCommand(kind="fail_next_verification")]
    await agent.tick()
    result = hub.payloads("/api/outcomes")[-1]
    assert result["status"] == "failed"
    assert result["checks_passed"] == 0
    assert "restored" in result["detail"]
    assert cache_path(agent.root).read_bytes() == original
    assert not (agent.root / ".fail-verification").exists()
    assert not hub.payloads("/api/candidates")


async def test_incompatible_recipe_is_rejected_before_mutation(endpoint):
    agent, hub = endpoint
    incident = await break_app(agent)
    original = cache_path(agent.root).read_bytes()
    hub.work.assignment = assignment_for(agent, incident, version="2.0.0")
    await agent.tick()
    assert hub.payloads("/api/outcomes")[-1]["status"] == "rejected"
    assert cache_path(agent.root).read_bytes() == original
    assert not list((agent.root / "quarantine").iterdir())


async def test_global_pause_defers_an_already_issued_assignment(endpoint):
    agent, hub = endpoint
    incident = await break_app(agent)
    hub.work.assignment = assignment_for(agent, incident)
    hub.work.paused = True
    await agent.tick()
    assert not hub.payloads("/api/outcomes")
    assert not list((agent.root / "quarantine").iterdir())
    hub.work.paused = False
    await agent.tick()
    assert hub.payloads("/api/outcomes")[-1]["status"] == "verified"


async def test_interrupted_attempt_is_inconclusive_and_never_blindly_replayed(endpoint):
    agent, hub = endpoint
    incident = await break_app(agent)
    assignment = assignment_for(agent, incident)
    agent.journal.claim_attempt(assignment.assignment_id, assignment.model_dump())
    hub.work.assignment = assignment
    await agent.tick()
    assert hub.payloads("/api/outcomes")[-1]["status"] == "inconclusive"
    assert not list((agent.root / "quarantine").iterdir())


@pytest.mark.parametrize("redeliver", [False, True])
async def test_restart_does_not_learn_its_own_interrupted_repair(endpoint, redeliver):
    agent, hub = endpoint
    incident = await break_app(agent)
    assignment = assignment_for(agent, incident)
    agent.journal.claim_attempt(assignment.assignment_id, assignment.model_dump())
    quarantine_cache(agent.root, assignment.assignment_id)
    await agent.close()
    # A timed-out assignment may no longer be returned by the coordinator.
    hub.work.assignment = assignment if redeliver else None
    restarted = EndpointAgent(agent.config, client=agent.client)
    try:
        for _ in range(3):
            await restarted.tick()
        assert hub.payloads("/api/outcomes")
        assert all(p["status"] == "inconclusive" for p in hub.payloads("/api/outcomes"))
        assert not hub.payloads("/api/candidates")
        assert len(list((agent.root / "quarantine").iterdir())) == 1
        assert restarted.journal.get("external_change") is None
    finally:
        await restarted.close()


async def test_environment_change_reports_new_incident_after_restart(endpoint):
    agent, hub = endpoint
    incident = await break_app(agent)
    await agent.close()
    restarted = EndpointAgent(replace(agent.config, app_version="2.0.0"), client=agent.client)
    try:
        await restarted.tick()
        reports = hub.payloads("/api/incidents")
        assert len(reports) == 2
        assert reports[-1]["incident_id"] != incident["incident_id"]
        assert reports[-1]["environment"]["app_version"] == "2.0.0"
        assert restarted.journal.get("incident") == reports[-1]
    finally:
        await restarted.close()


async def test_healthy_directory_rejects_another_endpoint_identity(endpoint):
    agent, _ = endpoint
    await agent.tick()
    before = cache_path(agent.root).read_bytes()
    with pytest.raises(ValueError, match="belongs to endpoint.*new --data-dir"):
        EndpointAgent(replace(agent.config, agent_id="someone-else"), client=agent.client)
    assert cache_path(agent.root).read_bytes() == before
    assert agent.journal.get("owner_agent_id") == agent.config.agent_id


async def test_owner_binding_survives_reset_and_same_identity_restart(endpoint):
    agent, _ = endpoint
    await break_app(agent)
    await agent._command(DemoCommand(kind="reset"))
    assert agent.journal.get("owner_agent_id") == agent.config.agent_id
    assert agent.journal.get("incident") is None
    await agent.close()
    restarted = EndpointAgent(agent.config, client=agent.client)
    try:
        await restarted.tick()
        assert restarted.journal.get("owner_agent_id") == agent.config.agent_id
        with pytest.raises(ValueError, match="belongs to endpoint"):
            EndpointAgent(replace(agent.config, agent_id="someone-else"), client=agent.client)
    finally:
        await restarted.close()


@pytest.mark.parametrize("legacy_evidence", ["incident", "attempt"])
async def test_legacy_journal_identity_checked_before_attempt_recovery(endpoint, legacy_evidence):
    agent, _ = endpoint
    incident = await break_app(agent)
    assignment = assignment_for(agent, incident)
    if legacy_evidence == "attempt":
        agent.journal.claim_attempt(assignment.assignment_id, assignment.model_dump())
        agent.journal.set("incident", None)
    with agent.journal.connection:
        agent.journal.connection.execute("DELETE FROM state WHERE key='owner_agent_id'")
    with pytest.raises(ValueError, match="belongs to endpoint"):
        EndpointAgent(replace(agent.config, agent_id="someone-else"), client=agent.client)
    assert agent.journal.get("owner_agent_id") is None
    if legacy_evidence == "attempt":
        assert agent.journal.attempt(assignment.assignment_id)[0] == "executing"
    await agent.close()
    restarted = EndpointAgent(agent.config, client=agent.client)
    try:
        assert restarted.journal.get("owner_agent_id") == agent.config.agent_id
    finally:
        await restarted.close()


async def test_demo_reset_discards_stale_pending_deliveries_and_incident(endpoint):
    agent, hub = endpoint
    incident = await break_app(agent)
    agent.journal.enqueue("stale", "/api/candidates", {"stale": True})
    hub.work.commands = [DemoCommand(kind="reset")]
    await agent.tick()
    assert agent.journal.get("incident") is None
    assert not agent.journal.pending()
    assert not hub.payloads("/api/candidates")
    assert incident["incident_id"]


async def test_manual_repair_during_functional_probe_is_not_lost(tmp_path):
    root = tmp_path / "inflight-device"

    class RepairDuringProbeTransport(HubTransport):
        repair_next_probe = False

        async def handle_async_request(self, request):
            if request.url.host == "demo" and self.repair_next_probe:
                self.repair_next_probe = False
                quarantine_cache(root, "manual-inflight")
            return await super().handle_async_request(request)

    transport = RepairDuringProbeTransport(root)
    async with httpx.AsyncClient(transport=transport) as client:
        agent = EndpointAgent(
            AgentConfig("mac-a", "Mac A", root, "http://hub", "http://demo", interval=0.001),
            client=client,
        )
        try:
            await break_app(agent)
            transport.repair_next_probe = True
            await agent.tick()
            await agent.tick()
            assert not transport.payloads("/api/candidates")
            await agent.tick()
            candidates = transport.payloads("/api/candidates")
            assert len(candidates) == 1
            assert candidates[0]["verification_count"] == 3
            evidence = agent.journal.evidence(candidates[0]["evidence_ids"])
            assert [item["kind"] for item in evidence] == [
                "functional_failure",
                "cache_changed",
                "recovery_verified",
            ]
        finally:
            await agent.close()


async def test_probe_regeneration_alone_is_not_external_repair_evidence(endpoint):
    agent, hub = endpoint
    await break_app(agent)
    quarantine_cache(agent.root, "prior-change-not-observed")
    # The observer's baseline is already absent; regeneration by /work itself
    # must not masquerade as a newly observed external intervention.
    agent.journal.set("fingerprint", "missing")
    for _ in range(3):
        await agent.tick()
    assert not hub.payloads("/api/candidates")
    assert agent.journal.get("external_change") is None


async def test_manual_repair_during_execution_probe_is_preserved(tmp_path):
    root = tmp_path / "inflight-repair-device"
    restored = b'{"schema":1,"projects":[{"name":"Human restored project"}]}'

    class RepairAfterProbeTransport(HubTransport):
        repair_next_probe = False

        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            if request.url.host == "demo" and self.repair_next_probe:
                self.repair_next_probe = False
                # The server already returned the old failure, but a human
                # restores a valid cache before the agent receives that reply.
                write_cache(root, restored)
            return response

    hub = RepairAfterProbeTransport(root)
    async with httpx.AsyncClient(transport=hub) as client:
        agent = EndpointAgent(
            AgentConfig("mac-b", "Mac B", root, "http://hub", "http://demo", interval=0.001),
            client=client,
        )
        try:
            incident = await break_app(agent)
            hub.work.assignment = assignment_for(agent, incident)
            hub.repair_next_probe = True
            await agent.tick()
            outcome = hub.payloads("/api/outcomes")[-1]
            assert outcome["status"] == "rejected"
            assert "changed during" in outcome["detail"]
            assert cache_path(root).read_bytes() == restored
            assert not list((root / "quarantine").iterdir())
        finally:
            await agent.close()


async def test_manual_cache_change_during_failed_verification_is_not_rolled_back(tmp_path):
    root = tmp_path / "inflight-verification-device"
    restored = b'{"schema":1,"projects":[{"name":"Human restored project"}]}'

    class RepairDuringVerificationTransport(HubTransport):
        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            if request.url.host == "demo" and (root / ".fail-verification").exists():
                # The operation failed, then a human wrote useful content while
                # that failure response was still in flight back to the agent.
                write_cache(root, restored)
            return response

    hub = RepairDuringVerificationTransport(root)
    async with httpx.AsyncClient(transport=hub) as client:
        agent = EndpointAgent(
            AgentConfig("mac-b", "Mac B", root, "http://hub", "http://demo", interval=0.001),
            client=client,
        )
        try:
            incident = await break_app(agent)
            corrupt = cache_path(root).read_bytes()
            assignment = assignment_for(agent, incident)
            hub.work.assignment = assignment
            agent.journal.set("fail_next_verification", True)
            await agent.tick()
            outcome = hub.payloads("/api/outcomes")[-1]
            assert outcome["status"] == "failed"
            assert "rollback skipped" in outcome["detail"]
            assert "inspect endpoint" in outcome["detail"]
            assert cache_path(root).read_bytes() == restored
            assert (
                root / "quarantine" / f"{assignment.assignment_id}.json"
            ).read_bytes() == corrupt
            assert not (root / ".fail-verification").exists()
        finally:
            await agent.close()
