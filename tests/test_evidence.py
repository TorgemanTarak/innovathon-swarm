"""The shared evidence contract is bounded and cannot transport arbitrary diagnostics."""

import json

import pytest
from pydantic import ValidationError

from swarm.journal import Journal
from swarm.models import Candidate, Environment, EvidenceSummary, Outcome, now_iso


def summary(**changes):
    return {
        "evidence_id": "observation-1",
        "observed_at": "2026-11-12T10:00:00+00:00",
        "kind": "functional_failure",
        "probe_result": "failed",
        "error_code": "CACHE_INVALID",
        "cache_before": "a" * 64,
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"raw_log": "private application log"},
        {"path": "/Users/person/private-cache"},
        {"cache_before": "private cache contents"},
        {"cache_after": "a" * 65},
        {"error_code": "arbitrary server output"},
        {"evidence_id": "x" * 65},
        {"observed_at": "2026-11-12T10:00:00"},
        {"observed_at": "not a date"},
        {"checks_passed": 2, "checks_total": 1},
        {"checks_passed": 1},
        {"checks_passed": True, "checks_total": 3},
        {"checks_passed": 101, "checks_total": 101},
        {"checks_passed": float("nan"), "checks_total": 3},
        {"kind": "recovery_verified", "probe_result": "passed", "error_code": None},
    ],
)
def test_summary_rejects_unbounded_private_or_contradictory_fields(changes):
    with pytest.raises(ValidationError):
        EvidenceSummary.model_validate(summary(**changes))


def test_evidence_time_is_canonical_utc_for_browser_and_durable_roundtrips():
    record = EvidenceSummary.model_validate(summary(observed_at="2026-11-12 11:30:00+01:30"))
    assert record.observed_at == "2026-11-12T10:00:00+00:00"
    assert EvidenceSummary.model_validate_json(record.model_dump_json()) == record


def test_candidate_requires_referenced_origin_evidence_and_bounded_count():
    candidate = Candidate(
        source_agent_id="origin",
        incident_id="incident",
        issue_key="issue",
        environment=Environment(),
        evidence_ids=["observation-1"],
        explanation="Observed recovery",
        verification_count=3,
    ).model_dump()
    assert Candidate.model_validate(candidate).evidence == []  # Old records remain readable.
    candidate["evidence"] = [summary(evidence_id="not-referenced")]
    with pytest.raises(ValidationError, match="local observations"):
        Candidate.model_validate(candidate)
    candidate["evidence"] = [summary()] * 4
    with pytest.raises(ValidationError):
        Candidate.model_validate(candidate)
    candidate["evidence"] = [summary(), summary()]
    with pytest.raises(ValidationError, match="distinct references"):
        Candidate.model_validate(candidate)


def test_outcome_has_bounded_execution_evidence_with_unique_ids():
    outcome = Outcome(
        assignment_id="assignment",
        agent_id="agent",
        recipe_id="recipe",
        incident_id="incident",
        status="failed",
        detail="Verification failed",
    ).model_dump()
    assert Outcome.model_validate(outcome).evidence == []
    outcome["evidence"] = [summary()]
    with pytest.raises(ValidationError, match="executor observations"):
        Outcome.model_validate(outcome)
    outcome["evidence"] = [summary(kind="repair_precondition")] * 2
    with pytest.raises(ValidationError, match="distinct references"):
        Outcome.model_validate(outcome)
    outcome["evidence"] = [
        summary(kind="repair_precondition", evidence_id=f"step-{i}") for i in range(9)
    ]
    with pytest.raises(ValidationError):
        Outcome.model_validate(outcome)


def test_journal_projection_keeps_private_fields_local_and_skips_unknown_records(tmp_path):
    journal = Journal(tmp_path / "journal.sqlite")
    try:
        failure = journal.observe(
            "functional_failure",
            error_code="CACHE_INVALID",
            cache_before="a" * 64,
            probe_result="failed",
            raw_log="SECRET",
            path="/private/SECRET",
        )
        changed = journal.observe("cache_changed", previous="a" * 64, current="missing")
        recovered = journal.observe("recovery_verified", success_count=3, cache_after="b" * 64)
        malformed = journal.observe("recovery_verified")
        unsupported = journal.observe("private_event", content="SECRET")
        result = journal.evidence_summaries([failure, changed, recovered, malformed, unsupported])
        assert [item.kind for item in result] == [
            "functional_failure",
            "cache_changed",
            "recovery_verified",
        ]
        assert result[1].cache_before == "a" * 64
        assert result[1].cache_after == "missing"
        assert result[2].checks_passed == result[2].checks_total == 3
        payload = json.dumps([item.model_dump() for item in result])
        assert "SECRET" not in payload
        assert "/private" not in payload
        assert len(payload.encode()) < 2048
        assert journal.evidence([failure])[0]["raw_log"] == "SECRET"
        assert now_iso() >= result[0].observed_at
    finally:
        journal.close()
