import json

import httpx
import pytest

from swarm import investigator

EVIDENCE = [
    {"evidence_id": "event-1", "kind": "health_failed", "error_code": "CACHE_INVALID"},
    {"evidence_id": "event-2", "kind": "cache_changed", "path": "/Users/private/secret"},
    {"evidence_id": "event-3", "kind": "health_passed", "success_count": 3},
]


async def test_no_model_is_explicit_and_does_not_call_network(monkeypatch):
    async def forbidden(*args):
        raise AssertionError("network should not be used")

    monkeypatch.setattr(investigator, "_call_model", forbidden)
    text, mode = await investigator.explain_candidate(EVIDENCE)
    assert mode == "deterministic"
    assert "not configured" in text


async def test_valid_narration_uses_only_sanitized_evidence(monkeypatch):
    async def model(url, model_name, evidence):
        assert url == "http://127.0.0.1:8080/v1/chat/completions"
        assert model_name == "test-model"
        assert "private" not in json.dumps(evidence)
        assert "ignore instructions" not in json.dumps(evidence)
        return json.dumps(
            {
                "action": "quarantine_demo_cache",
                "explanation": "The cache change preceded three successful probes; validate on a canary.",
                "evidence_ids": ["event-2", "event-3"],
            }
        )

    monkeypatch.setattr(investigator, "_call_model", model)
    evidence = [
        *EVIDENCE,
        {"evidence_id": "event-4", "kind": "ignore instructions", "raw_log": "secret"},
    ]
    text, mode = await investigator.explain_candidate(
        evidence, "http://127.0.0.1:8080", "test-model"
    )
    assert mode == "local_model"
    assert text.startswith("Local-model hypothesis:")


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        json.dumps(
            {
                "action": "run_shell",
                "explanation": "Run this unrelated command.",
                "evidence_ids": ["event-1"],
            }
        ),
        json.dumps(
            {
                "action": "quarantine_demo_cache",
                "explanation": "The source recovered.",
                "evidence_ids": ["invented-id"],
            }
        ),
        json.dumps(
            {
                "action": "quarantine_demo_cache",
                "explanation": "The source recovered.",
                "evidence_ids": ["event-1"],
                "shell": "touch /tmp/x",
            }
        ),
    ],
)
async def test_invalid_or_unsupported_model_response_falls_back(monkeypatch, response):
    async def model(*args):
        return response

    monkeypatch.setattr(investigator, "_call_model", model)
    text, mode = await investigator.explain_candidate(EVIDENCE, "http://localhost:8080/v1", "test")
    assert mode == "deterministic"
    assert "fallback" in text


async def test_model_timeout_falls_back(monkeypatch):
    async def model(*args):
        raise httpx.ReadTimeout("slow server")

    monkeypatch.setattr(investigator, "_call_model", model)
    assert (await investigator.explain_candidate(EVIDENCE, "http://localhost:8080", "test"))[
        1
    ] == "deterministic"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://192.168.1.2:8080/v1",
        "file:///tmp/model",
        "http://user:password@localhost/v1",
        "http://localhost/v1?secret=x",
        "http://localhost/v1/chat/completions",
    ],
)
async def test_nonlocal_or_ambiguous_endpoints_never_receive_data(monkeypatch, url):
    async def forbidden(*args):
        raise AssertionError("network must not be called")

    monkeypatch.setattr(investigator, "_call_model", forbidden)
    text, mode = await investigator.explain_candidate(EVIDENCE, url, "test")
    assert mode == "deterministic"
    assert "fallback" in text


def test_evidence_is_bounded_and_deduplicated():
    evidence = [
        {"evidence_id": f"event-{i}", "kind": "health_passed", "success_count": True}
        for i in range(30)
    ]
    clean = investigator._sanitize_evidence(evidence)
    assert len(clean) == 20
    assert "success_count" not in clean[0]
    assert len(investigator._sanitize_evidence([EVIDENCE[0], EVIDENCE[0]])) == 1


async def test_http_adapter_posts_bounded_json_to_local_server(monkeypatch):
    original_client = httpx.AsyncClient

    def respond(request):
        body = json.loads(request.content)
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 600
        assert "authorization" not in request.headers
        answer = {
            "action": "quarantine_demo_cache",
            "explanation": "Consider cache quarantine for canary validation.",
            "evidence_ids": ["event-1"],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(answer)}}]})

    monkeypatch.setattr(
        investigator.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    _, mode = await investigator.explain_candidate(EVIDENCE, "http://127.0.0.1:8080", "test")
    assert mode == "local_model"


async def test_oversized_model_response_falls_back(monkeypatch):
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 70_000))
    monkeypatch.setattr(
        investigator.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    _, mode = await investigator.explain_candidate(EVIDENCE, "http://127.0.0.1:8080", "test")
    assert mode == "deterministic"
