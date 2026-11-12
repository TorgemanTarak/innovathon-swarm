"""Native endpoint loop: observe locally, propose evidence, replay typed repairs."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx

from swarm.journal import Journal
from swarm.models import (
    Assignment,
    Candidate,
    CommandAck,
    DemoCommand,
    Environment,
    Heartbeat,
    IssueReport,
    Outcome,
    Registration,
    Work,
    issue_key,
)
from swarm.repairs import (
    cache_is_invalid,
    fingerprint,
    initialize_root,
    inject_fault,
    quarantine_cache,
    reset_demo,
    rollback_cache,
    scoped_path,
    set_verification_fault,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    agent_id: str
    label: str
    data_dir: Path
    coordinator_url: str
    demo_url: str
    token: str | None = None
    app_version: str = "1.0.0"
    interval: float = 1.0
    mode: Literal["native", "simulated"] = "native"
    llm_base_url: str | None = None
    llm_model: str | None = None


class EndpointAgent:
    def __init__(self, config: AgentConfig, client: httpx.AsyncClient | None = None) -> None:
        if config.interval <= 0:
            raise ValueError("Polling interval must be positive")
        self.config = config
        self.root = initialize_root(config.data_dir)
        self.environment = Environment(app_version=config.app_version)
        self.registration = Registration(
            agent_id=config.agent_id,
            label=config.label,
            environment=self.environment,
            mode=config.mode,
            demo_url=config.demo_url,
        )
        self.journal = Journal(scoped_path(self.root, "journal.sqlite"))
        try:
            self.journal.bind_agent(config.agent_id)
        except Exception:
            self.journal.close()
            raise
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=5.0, trust_env=False)
        self._registered = False
        self._lock = asyncio.Lock()
        self._closed = False
        self.journal.set("fingerprint", self.journal.get("fingerprint", fingerprint(self.root)))
        # A coordinator can expire an assignment while this endpoint is offline.
        # Recover local attempts even when the hub never delivers them again.
        for payload in self.journal.unfinished_attempts():
            self._recover_interrupted_attempt(Assignment.model_validate(payload))
        incident_data = self.journal.get("incident")
        if incident_data and incident_data["environment"] != self.environment.model_dump():
            # Re-registering a changed environment invalidates the hub's active
            # incident. Discover a fresh incident under the new environment too.
            self.journal.set("incident", None)
            self.journal.set("resolved", False)
            self.journal.set("candidate_enqueued", False)
            self._reset_learning_baseline()
            self.journal.observe("environment_changed", environment=self.environment.model_dump())

    def _url(self, path: str) -> str:
        return self.config.coordinator_url.rstrip("/") + path

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Swarm-Token": self.config.token} if self.config.token else {}

    async def _request(self, method: str, path: str, payload: dict | None = None) -> httpx.Response:
        response = await self.client.request(
            method, self._url(path), json=payload, headers=self._headers
        )
        response.raise_for_status()
        return response

    async def _work(self) -> Work:
        try:
            if not self._registered:
                await self._request("POST", "/api/agents/register", self.registration.model_dump())
                self._registered = True
            response = await self._request("GET", f"/api/agents/{self.config.agent_id}/work")
            return Work.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            self._registered = False
            logger.warning(
                "Coordinator unavailable for %s: %s", self.config.agent_id, type(exc).__name__
            )
            return Work(paused=True)

    async def _probe(self) -> tuple[bool | None, str, str]:
        try:
            response = await self.client.get(
                self.config.demo_url.rstrip("/") + "/work", timeout=3.0
            )
            body = response.json()
            if response.status_code == 200 and body.get("ok") is True:
                return True, "", "Workspace operation completed"
            code = body.get("error_code", "APPLICATION_ERROR")
            # Only a bounded error code leaves the endpoint, not arbitrary server output.
            if code not in ("CACHE_INVALID", "VERIFICATION_FAILED"):
                code = "APPLICATION_ERROR"
            return False, code, "Workspace operation failed"
        except (httpx.HTTPError, ValueError, AttributeError):
            return None, "UNREACHABLE", "Demo application is unreachable"

    async def _flush(self) -> None:
        for identity, path, payload in self.journal.pending():
            try:
                await self._request("POST", path, payload)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (400, 404, 409, 422):
                    # An operator reset can intentionally invalidate old incident/outcome IDs.
                    self.journal.observe("delivery_rejected", request_type=path, status=status)
                    self.journal.delivered(identity)
                    continue
                raise
            self.journal.delivered(identity)

    async def _command(self, command: DemoCommand) -> None:
        saved = self.journal.command(command.command_id)
        if saved is not None:
            ack = CommandAck.model_validate(saved)
        else:
            try:
                if command.kind == "inject_fault":
                    inject_fault(self.root)
                elif command.kind == "manual_repair":
                    quarantine_cache(self.root, "manual-" + command.command_id)
                    # Intentionally observed by the normal learner, attributed to the demo operator.
                    self.journal.observe("operator_repair", action="quarantine_demo_cache")
                elif command.kind == "reset":
                    reset_demo(self.root)
                    self.journal.clear_runtime()
                    self.journal.set("fingerprint", fingerprint(self.root))
                elif command.kind == "fail_next_verification":
                    self.journal.set("fail_next_verification", True)
                ack = CommandAck(command_id=command.command_id, success=True, detail=command.kind)
            except (OSError, ValueError) as exc:
                ack = CommandAck(
                    command_id=command.command_id, success=False, detail=str(exc)[:1000]
                )
            self.journal.finish_command(command.command_id, ack.model_dump())
            self.journal.observe("demo_command", kind_name=command.kind, success=ack.success)
        self.journal.enqueue(
            f"command:{command.command_id}",
            f"/api/agents/{self.config.agent_id}/commands/ack",
            ack.model_dump(),
        )

    def _outcome(self, assignment: Assignment, **kwargs: Any) -> Outcome:
        return Outcome(
            assignment_id=assignment.assignment_id,
            agent_id=self.config.agent_id,
            recipe_id=assignment.recipe.recipe_id,
            incident_id=assignment.incident_id,
            **kwargs,
        )

    def _reset_learning_baseline(self) -> None:
        self.journal.set("fingerprint", fingerprint(self.root))
        self.journal.set("external_change", None)
        self.journal.set("healthy_streak", 0)

    def _evidence_fingerprint(self) -> str | None:
        """A diagnostic read must not turn unavailable evidence into a repair decision."""
        try:
            return fingerprint(self.root)
        except (OSError, ValueError):
            return None

    def _recover_interrupted_attempt(self, assignment: Assignment) -> None:
        outcome = self._outcome(
            assignment,
            status="inconclusive",
            detail="Agent restarted during repair; automatic replay stopped for inspection",
        )
        # The mutation may have happened before the crash. Never attribute its
        # recovery to an external intervention when observation resumes. Persist
        # suppression before completing recovery, so a second crash is safe too.
        self._reset_learning_baseline()
        self.journal.finish_attempt(assignment.assignment_id, outcome.model_dump())

    async def _execute(self, assignment: Assignment) -> None:
        existing = self.journal.attempt(assignment.assignment_id)
        if existing:
            if existing[0] == "finished":
                self.journal.enqueue(
                    f"outcome:{assignment.assignment_id}", "/api/outcomes", existing[1]
                )
                return
            # We cannot safely infer how far a crashed process got. Never replay blindly.
            self._recover_interrupted_attempt(assignment)
            return

        if not self.journal.claim_attempt(assignment.assignment_id, assignment.model_dump()):
            return
        started = time.monotonic()
        backup: Path | None = None
        fault_injected = False
        checks = 0
        evidence_ids: list[str] = []

        def observe(kind: str, **data: Any) -> None:
            evidence_ids.append(
                self.journal.observe(kind, assignment_id=assignment.assignment_id, **data)
            )

        try:
            incident_data = self.journal.get("incident")
            candidate = assignment.recipe.candidate
            if assignment.agent_id != self.config.agent_id:
                raise ValueError("Assignment belongs to another endpoint")
            if not incident_data or assignment.incident_id != incident_data["incident_id"]:
                raise ValueError("Assignment does not match this endpoint's current incident")
            if (
                candidate.issue_key != issue_key()
                or incident_data["issue_key"] != candidate.issue_key
            ):
                raise ValueError("Issue signature does not match")
            if (
                candidate.environment.platform != self.environment.platform
                or candidate.environment.app_id != self.environment.app_id
                or candidate.environment.app_version != self.environment.app_version
            ):
                raise ValueError("Recipe environment is incompatible")
            if assignment.recipe.status == "paused":
                raise ValueError("Recipe rollout is paused")
            if candidate.action != "quarantine_demo_cache" or not cache_is_invalid(self.root):
                raise ValueError("Cache-repair precondition is not present")
            expected_fingerprint = fingerprint(self.root)

            before, code, _ = await self._probe()
            observe(
                "repair_precondition",
                cache_before=expected_fingerprint,
                cache_after=self._evidence_fingerprint(),
                probe_result="passed"
                if before is True
                else "failed"
                if before is False
                else "unknown",
                error_code=code or None,
            )
            if before is not False or code != "CACHE_INVALID":
                raise ValueError("Functional probe did not reproduce the expected failure")
            if fingerprint(self.root) != expected_fingerprint or not cache_is_invalid(self.root):
                raise ValueError("Cache changed during the functional probe; repair stopped")
            backup = quarantine_cache(self.root, assignment.assignment_id)
            if backup is None:
                raise ValueError("Cache disappeared before execution")
            observe(
                "repair_applied",
                action=candidate.action,
                cache_before=expected_fingerprint,
                cache_after=self._evidence_fingerprint(),
            )
            if self.journal.get("fail_next_verification", False):
                self.journal.set("fail_next_verification", False)
                set_verification_fault(self.root, True)
                fault_injected = True

            for index in range(3):
                if index:
                    await asyncio.sleep(min(self.config.interval, 1.0))
                healthy, code, _ = await self._probe()
                if healthy is True:
                    checks += 1
                else:
                    break
            observe(
                "repair_verified" if checks == 3 else "verification_failed",
                cache_after=self._evidence_fingerprint(),
                probe_result="passed"
                if healthy is True
                else "failed"
                if healthy is False
                else "unknown",
                error_code=code or None,
                checks_passed=checks,
                checks_total=index + 1,
            )
            if checks == 3:
                outcome = self._outcome(
                    assignment,
                    status="verified",
                    checks_passed=3,
                    detail="Cache quarantined; 3 consecutive workspace operations succeeded",
                )
                self.journal.set("resolved", True)
            else:
                rollback_cache(self.root, backup)
                backup = None
                observe("rollback_restored", cache_after=self._evidence_fingerprint())
                outcome = self._outcome(
                    assignment,
                    status="failed",
                    checks_passed=checks,
                    detail="Functional verification failed; previous cache restored and rollout must pause",
                )
        except (OSError, ValueError) as exc:
            detail = str(exc)
            status = "rejected"
            if backup is not None:
                status = "failed"
                try:
                    rollback_cache(self.root, backup)
                    observe("rollback_restored", cache_after=self._evidence_fingerprint())
                    detail += "; previous cache restored"
                except (OSError, ValueError):
                    observe("rollback_failed", cache_after=self._evidence_fingerprint())
                    detail += "; rollback failed, inspect endpoint"
            outcome = self._outcome(
                assignment, status=status, checks_passed=checks, detail=detail[:2000]
            )
        finally:
            if fault_injected:
                set_verification_fault(self.root, False)
            # Agent-generated changes never count as new human repair discoveries.
            self._reset_learning_baseline()
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        outcome.evidence = self.journal.evidence_summaries(evidence_ids)
        self.journal.finish_attempt(assignment.assignment_id, outcome.model_dump())
        self.journal.observe(
            "repair_outcome", status=outcome.status, assignment_id=assignment.assignment_id
        )

    async def _learn(self, incident: IssueReport, healthy_streak: int) -> None:
        if self.journal.get("candidate_enqueued", False):
            return
        change_id = self.journal.get("external_change")
        if not change_id or healthy_streak < 3:
            return
        verification_id = self.journal.observe(
            "recovery_verified",
            success_count=healthy_streak,
            cache_after=self.journal.get("fingerprint"),
        )
        evidence_ids = [*incident.evidence_ids, change_id, verification_id]
        explanation = (
            "A change to the watched application cache preceded recovery. "
            "Three consecutive workspace operations succeeded. Cache quarantine is a candidate repair."
        )
        investigator: Literal["deterministic", "local_model"] = "deterministic"
        if self.config.llm_base_url and self.config.llm_model:
            try:
                from swarm.investigator import explain_candidate

                explanation, investigator = await explain_candidate(
                    self.journal.evidence(evidence_ids),
                    base_url=self.config.llm_base_url,
                    model=self.config.llm_model,
                )
            except Exception:
                logger.warning("Local investigator unavailable; keeping deterministic evidence")
        candidate = Candidate(
            source_agent_id=self.config.agent_id,
            incident_id=incident.incident_id,
            issue_key=incident.issue_key,
            environment=self.environment,
            evidence_ids=evidence_ids,
            explanation=explanation,
            investigator=investigator,
            verification_count=healthy_streak,
            evidence=self.journal.evidence_summaries(evidence_ids),
        )
        self.journal.enqueue(
            f"candidate:{candidate.candidate_id}", "/api/candidates", candidate.model_dump()
        )
        self.journal.set("candidate_enqueued", True)

    async def tick(self) -> None:
        async with self._lock:
            work = await self._work()
            for command in work.commands:
                await self._command(command)
            if work.assignment is not None and not work.paused:
                await self._execute(work.assignment)

            current = fingerprint(self.root)
            previous = self.journal.get("fingerprint", current)
            incident_data = self.journal.get("incident")
            if current != previous:
                observation_id = self.journal.observe(
                    "cache_changed", previous=previous, current=current
                )
                if incident_data and not self.journal.get("resolved", False):
                    self.journal.set("external_change", observation_id)
                    self.journal.set("healthy_streak", 0)

            healthy, error, detail = await self._probe()
            after_probe = fingerprint(self.root)
            # A human can repair the cache while the HTTP operation is in flight.
            # Preserve that observation before advancing the baseline or learning.
            # Missing -> regenerated is an expected effect of the probe itself;
            # any preceding deletion was already captured by the pre-probe check.
            if after_probe != current and current != "missing":
                observation_id = self.journal.observe(
                    "cache_changed", previous=current, current=after_probe
                )
                if incident_data and not self.journal.get("resolved", False):
                    self.journal.set("external_change", observation_id)
                    self.journal.set("healthy_streak", 0)
            self.journal.set("fingerprint", after_probe)
            active_issue: str | None = None
            if healthy is False and error == "CACHE_INVALID":
                active_issue = issue_key(error)
                if not incident_data or self.journal.get("resolved", False):
                    observation_id = self.journal.observe(
                        "functional_failure",
                        error_code=error,
                        probe_result="failed",
                        cache_before=current,
                        cache_after=after_probe,
                    )
                    incident = IssueReport(
                        agent_id=self.config.agent_id,
                        issue_key=active_issue,
                        summary="Workspace cache prevents loading projects",
                        evidence_ids=[observation_id],
                        environment=self.environment,
                    )
                    incident_data = incident.model_dump()
                    self.journal.set("incident", incident_data)
                    self.journal.set("resolved", False)
                    self.journal.set("external_change", None)
                    self.journal.set("candidate_enqueued", False)
                    self.journal.enqueue(
                        f"incident:{incident.incident_id}", "/api/incidents", incident_data
                    )
                self.journal.set("healthy_streak", 0)
            elif healthy is True and incident_data and not self.journal.get("resolved", False):
                streak = self.journal.get("healthy_streak", 0) + 1
                self.journal.set("healthy_streak", streak)
                await self._learn(IssueReport.model_validate(incident_data), streak)
                if streak >= 3:
                    self.journal.set("resolved", True)
            else:
                if healthy is not True:
                    self.journal.set("healthy_streak", 0)

            heartbeat = Heartbeat(
                health="healthy"
                if healthy is True
                else "unhealthy"
                if healthy is False
                else "unknown",
                issue_key=active_issue,
                detail=detail,
            )
            try:
                if self._registered:
                    # Registry validates source recovery against this fresh heartbeat.
                    await self._request(
                        "POST",
                        f"/api/agents/{self.config.agent_id}/heartbeat",
                        heartbeat.model_dump(),
                    )
                    await self._flush()
            except httpx.HTTPError as exc:
                self._registered = False
                logger.warning(
                    "Delivery deferred for %s: %s", self.config.agent_id, type(exc).__name__
                )

    async def run(self) -> None:
        try:
            while True:
                try:
                    await self.tick()
                except (OSError, ValueError, httpx.HTTPError):
                    logger.exception(
                        "Endpoint tick failed; no unvalidated repair will be performed"
                    )
                await asyncio.sleep(self.config.interval)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self.client.aclose()
        self.journal.close()
