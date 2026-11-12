"""Versioned wire contracts. No executable code or raw log contents cross this boundary."""

from __future__ import annotations

import hashlib
import platform
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid4())


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


CacheFingerprint = Annotated[
    str, Field(max_length=64, pattern=r"^(?:[0-9a-f]{64}|missing|oversized)$")
]


class EvidenceSummary(Contract):
    """Bounded endpoint observations, never raw cache data, paths or log messages."""

    schema_version: Literal[1] = 1
    evidence_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    observed_at: str = Field(max_length=40)
    kind: Literal[
        "functional_failure",
        "cache_changed",
        "recovery_verified",
        "repair_precondition",
        "repair_applied",
        "repair_verified",
        "verification_failed",
        "rollback_restored",
        "rollback_failed",
    ]
    cache_before: CacheFingerprint | None = None
    cache_after: CacheFingerprint | None = None
    probe_result: Literal["passed", "failed", "unknown"] | None = None
    error_code: (
        Literal["CACHE_INVALID", "VERIFICATION_FAILED", "APPLICATION_ERROR", "UNREACHABLE"] | None
    ) = None
    checks_passed: int | None = Field(default=None, ge=0, le=100, strict=True)
    checks_total: int | None = Field(default=None, ge=1, le=100, strict=True)

    @field_validator("observed_at")
    @classmethod
    def timestamp_has_timezone(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("Evidence timestamps require a timezone")
        return parsed.astimezone(UTC).isoformat()

    @model_validator(mode="after")
    def consistent_checks(self) -> Self:
        if (self.checks_passed is None) != (self.checks_total is None):
            raise ValueError("Evidence check counts must be supplied together")
        if (
            self.checks_passed is not None
            and self.checks_total is not None
            and self.checks_passed > self.checks_total
        ):
            raise ValueError("Passed checks cannot exceed attempted checks")
        if self.kind in {"recovery_verified", "repair_verified"} and (
            self.probe_result != "passed"
            or self.error_code is not None
            or self.checks_total is None
            or self.checks_total < 3
            or self.checks_passed != self.checks_total
        ):
            raise ValueError("Verified evidence requires at least three successful checks")
        return self


class Environment(Contract):
    platform: str = Field(default_factory=lambda: platform.system().lower())
    app_id: Literal["workspace-demo"] = "workspace-demo"
    app_version: str = "1.0.0"
    agent_version: str = "0.1.0"


def issue_key(error_code: str = "CACHE_INVALID") -> str:
    canonical = f"v1|workspace-demo|load-workspace|{error_code}"
    return "issue-" + hashlib.sha256(canonical.encode()).hexdigest()[:20]


class Registration(Contract):
    agent_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=80)
    environment: Environment = Field(default_factory=Environment)
    mode: Literal["native", "simulated"] = "native"
    demo_url: str | None = None


class Heartbeat(Contract):
    health: Literal["healthy", "unhealthy", "unknown"]
    issue_key: str | None = None
    detail: str = Field(default="", max_length=1000)


class IssueReport(Contract):
    incident_id: str = Field(default_factory=new_id)
    agent_id: str
    issue_key: str
    summary: str = Field(max_length=1000)
    error_code: str = "CACHE_INVALID"
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    environment: Environment
    observed_at: str = Field(default_factory=now_iso)


class Candidate(Contract):
    candidate_id: str = Field(default_factory=new_id)
    source_agent_id: str
    incident_id: str
    issue_key: str
    action: Literal["quarantine_demo_cache"] = "quarantine_demo_cache"
    environment: Environment
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    explanation: str = Field(max_length=2000)
    investigator: Literal["deterministic", "local_model"] = "deterministic"
    verification_count: int = Field(ge=3, le=100)
    evidence: list[EvidenceSummary] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def evidence_references_match(self) -> Self:
        if any(
            item.evidence_id not in self.evidence_ids
            or item.kind not in {"functional_failure", "cache_changed", "recovery_verified"}
            for item in self.evidence
        ):
            raise ValueError("Origin summaries must reference the candidate's local observations")
        if len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("Evidence summaries must have distinct references")
        if any(
            item.kind == "recovery_verified" and item.checks_passed != self.verification_count
            for item in self.evidence
        ):
            raise ValueError("Recovery evidence count differs from the candidate")
        return self


class Recipe(Contract):
    recipe_id: str = Field(default_factory=new_id)
    revision: int = 1
    candidate: Candidate
    status: Literal["candidate", "canary", "validated", "paused"] = "candidate"
    canary_agent_id: str | None = None
    successes: int = 0
    failures: int = 0
    created_at: str = Field(default_factory=now_iso)


class Assignment(Contract):
    assignment_id: str = Field(default_factory=new_id)
    agent_id: str
    incident_id: str
    recipe: Recipe
    role: Literal["canary", "follower"]
    created_at: str = Field(default_factory=now_iso)


class Outcome(Contract):
    assignment_id: str
    agent_id: str
    recipe_id: str
    incident_id: str
    status: Literal["verified", "failed", "rejected", "inconclusive"]
    checks_passed: int = Field(default=0, ge=0)
    checks_total: int = Field(default=3, ge=1)
    detail: str = Field(max_length=2000)
    duration_ms: int = Field(default=0, ge=0)
    observed_at: str = Field(default_factory=now_iso)
    evidence: list[EvidenceSummary] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def execution_evidence_only(self) -> Self:
        if any(
            item.kind in {"functional_failure", "cache_changed", "recovery_verified"}
            for item in self.evidence
        ):
            raise ValueError("Replay summaries must describe executor observations")
        if len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("Replay summaries must have distinct references")
        return self


class DemoCommand(Contract):
    command_id: str = Field(default_factory=new_id)
    kind: Literal["inject_fault", "manual_repair", "reset", "fail_next_verification"]
    created_at: str = Field(default_factory=now_iso)


class CommandAck(Contract):
    command_id: str
    success: bool
    detail: str = Field(default="", max_length=1000)


class Work(Contract):
    assignment: Assignment | None = None
    commands: list[DemoCommand] = Field(default_factory=list)
    paused: bool = False


class FleetEvent(Contract):
    event_id: str = Field(default_factory=new_id)
    timestamp: str = Field(default_factory=now_iso)
    kind: str
    message: str
    agent_id: str | None = None
    recipe_id: str | None = None


class TargetRequest(Contract):
    agent_ids: list[str] = Field(default_factory=list)


class PauseRequest(Contract):
    paused: bool
