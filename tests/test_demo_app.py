import json

import pytest
from fastapi.testclient import TestClient

from swarm.demo_app import create_app
from swarm.repairs import cache_path, inject_fault, quarantine_cache


def test_corruption_breaks_actual_operation_and_manual_repair_recovers(tmp_path):
    root = tmp_path / "demo"
    with TestClient(create_app(root)) as client:
        assert client.get("/work").status_code == 200
        original = cache_path(root).read_text()
        inject_fault(root)
        response = client.get("/work")
        assert response.status_code == 503
        assert response.json()["error_code"] == "CACHE_INVALID"
        assert client.get("/health").json()["kind"] == "liveness"
        assert client.get("/health").status_code == 200
        backup = quarantine_cache(root, "manual-repair")
        assert backup is not None and backup.exists()
        assert client.get("/work").status_code == 200
        assert cache_path(root).read_text() == original
        entries = [json.loads(line) for line in (root / "logs/app.jsonl").read_text().splitlines()]
        assert [entry["event"] for entry in entries] == [
            "workspace.load.succeeded",
            "workspace.load.failed",
            "workspace.load.succeeded",
        ]
        assert entries[1]["error_code"] == "CACHE_INVALID"
        assert str(root) not in json.dumps(entries)


@pytest.mark.parametrize(
    "contents",
    ["not JSON", "[]", '{"schema":true,"projects":[]}', '{"schema":1,"projects":"wrong"}'],
)
def test_invalid_cache_schema_is_a_real_failure(tmp_path, contents):
    root = tmp_path / "demo"
    with TestClient(create_app(root)) as client:
        client.get("/work")
        cache_path(root).write_text(contents)
        assert client.get("/work").status_code == 503


def test_verification_fault_is_an_actual_functional_failure(tmp_path):
    root = tmp_path / "demo"
    with TestClient(create_app(root)) as client:
        assert client.get("/work").status_code == 200
        marker = root / ".fail-verification"
        marker.touch()
        response = client.get("/work")
        assert response.status_code == 503
        assert response.json()["error_code"] == "VERIFICATION_FAILED"
        marker.unlink()
        assert client.get("/work").status_code == 200


def test_ui_escapes_version_and_has_no_mutation_route(tmp_path):
    with TestClient(create_app(tmp_path / "demo", '<script>alert("bad")</script>')) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert '<script>alert("bad")</script>' not in response.text
        assert "&lt;script&gt;" in response.text
        assert client.post("/repair").status_code == 404


def test_log_path_redirect_is_rejected(tmp_path):
    root = tmp_path / "demo"
    with TestClient(create_app(root), raise_server_exceptions=False) as client:
        outside = tmp_path / "outside"
        outside.write_text("unchanged")
        (root / "logs").mkdir(exist_ok=True)
        (root / "logs/app.jsonl").symlink_to(outside)
        assert client.get("/work").status_code == 500
        assert outside.read_text() == "unchanged"
