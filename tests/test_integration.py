"""Exercise actual OS processes, HTTP, file mutations, journals and rollout policy."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def free_port_block(size: int) -> int:
    for _ in range(100):
        with socket.socket() as first:
            first.bind(("127.0.0.1", 0))
            port = first.getsockname()[1]
        if port + size >= 65535:
            continue
        sockets = []
        try:
            for candidate in range(port, port + size):
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(("127.0.0.1", candidate))
            return port
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("Could not reserve a test port range")


@pytest.fixture
def fleet(tmp_path: Path):
    port = free_port_block(5)
    root = tmp_path / "fleet"
    env = {key: value for key, value in os.environ.items() if not key.startswith("SWARM_")}
    log = (tmp_path / "supervisor.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "swarm",
            "demo",
            "--port",
            str(port),
            "--app-port",
            str(port + 1),
            "--data-dir",
            str(root),
            "--nodes",
            "3",
            "--include-incompatible",
            "--interval",
            "0.15",
        ],
        stdout=log,
        stderr=log,
        env=env,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False)

    def wait_for(predicate, timeout=30):
        deadline = time.monotonic() + timeout
        state = {}
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log.flush()
                logs = "\n".join(f"{p.name}: {p.read_text()[-3000:]}" for p in root.rglob("*.log"))
                pytest.fail(
                    f"Supervisor exited: {(tmp_path / 'supervisor.log').read_text()}\n{logs}"
                )
            try:
                response = client.get("/api/state")
                response.raise_for_status()
                state = response.json()
                if predicate(state):
                    return state
            except httpx.HTTPError:
                pass
            time.sleep(0.15)
        logs = "\n".join(f"{p.name}: {p.read_text()[-2000:]}" for p in root.rglob("*.log"))
        pytest.fail(f"Timed out. Last state: {state}\n{logs}")

    def post(path, payload=None):
        response = client.post(path, json=payload or {})
        assert response.status_code == 200, response.text
        return response.json()

    try:
        wait_for(
            lambda s: len(s["agents"]) == 4 and all(a["health"] == "healthy" for a in s["agents"])
        )
        yield root, client, post, wait_for
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        client.close()
        log.close()


def test_real_fleet_discovers_propagates_rejects_and_stops_failed_canary(fleet):
    root, client, post, wait = fleet
    post("/api/demo/fault")
    wait(lambda s: all(a["health"] == "unhealthy" and a["incident_id"] for a in s["agents"]))

    # A real human file operation, independent of coordinator demo commands.
    cache = root / "mac-a" / "cache" / "workspace.json"
    cache.rename(cache.with_name("workspace.manually-quarantined.json"))
    state = wait(
        lambda s: len(s["outcomes"]) == 2 and all(o["status"] == "verified" for o in s["outcomes"])
    )
    state = wait(lambda s: sum(a["health"] == "healthy" for a in s["agents"]) == 3)
    assert state["recipes"][0]["status"] == "validated"
    assert state["recipes"][0]["successes"] == 2
    assert next(a for a in state["agents"] if a["agent_id"] == "mac-d")["health"] == "unhealthy"
    wait(lambda s: any(e["kind"] == "repair_incompatible" for e in s["events"]))
    events = list(reversed(state["events"]))
    canary = next(e for e in events if e["kind"] == "canary_started")
    verified_index = next(
        i
        for i, e in enumerate(events)
        if e["kind"] == "repair_verified" and e["agent_id"] == canary["agent_id"]
    )
    follower_index = next(i for i, e in enumerate(events) if e["kind"] == "repair_dispatched")
    assert verified_index < follower_index
    assert all(o["checks_passed"] == o["checks_total"] == 3 for o in state["outcomes"])

    # Reset must actually restore each endpoint, clear learned mappings, and permit a new story.
    post("/api/demo/reset")
    wait(
        lambda s: (
            not s["recipes"]
            and not s["outcomes"]
            and all(a["health"] == "healthy" and a["incident_id"] is None for a in s["agents"])
        )
    )
    post("/api/control/pause", {"paused": True})
    post("/api/demo/fault")
    wait(lambda s: all(a["health"] == "unhealthy" and a["incident_id"] for a in s["agents"]))
    post("/api/demo/repair", {"agent_ids": ["mac-a"]})
    state = wait(lambda s: len(s["recipes"]) == 1)
    assert state["paused"] is True
    assert state["outcomes"] == []
    assert state["recipes"][0]["status"] == "candidate"

    # Arm both eligible receivers: whichever polls first becomes the canary.
    post("/api/demo/fail-verification", {"agent_ids": ["mac-b", "mac-c"]})
    wait(
        lambda s: (
            sum(
                e["kind"] == "demo_command_completed" and e["message"] == "fail_next_verification"
                for e in s["events"]
            )
            == 2
        )
    )
    post("/api/control/pause", {"paused": False})
    state = wait(lambda s: s["recipes"][0]["status"] == "paused" and len(s["outcomes"]) == 1)
    outcome = state["outcomes"][0]
    assert outcome["status"] == "failed"
    assert "restored" in outcome["detail"]
    assert state["recipes"][0]["successes"] == 0
    assert state["recipes"][0]["failures"] == 1
    failed_cache = root / outcome["agent_id"] / "cache" / "workspace.json"
    assert "CORRUPTED CACHE" in failed_cache.read_text()
    assert not any(e["kind"] == "repair_dispatched" for e in state["events"])

    # Dashboard is served with the actual app, not a separate mock server.
    assert "SWARM" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_native_node_launcher_and_child_cleanup(fleet, tmp_path):
    _, client, _, wait = fleet
    app_port = free_port_block(1)
    env = {key: value for key, value in os.environ.items() if not key.startswith("SWARM_")}
    with (tmp_path / "node.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "swarm",
                "node",
                "--id",
                "native-one",
                "--coordinator",
                str(client.base_url),
                "--data-dir",
                str(tmp_path / "native-data"),
                "--app-port",
                str(app_port),
                "--interval",
                "0.15",
            ],
            stdout=log,
            stderr=log,
            env=env,
        )
        try:
            state = wait(
                lambda s: any(
                    a["agent_id"] == "native-one" and a["health"] == "healthy" for a in s["agents"]
                )
            )
            assert (
                next(a for a in state["agents"] if a["agent_id"] == "native-one")["mode"]
                == "native"
            )
            assert httpx.get(f"http://127.0.0.1:{app_port}/work", trust_env=False).json()["ok"]
        finally:
            process.terminate()
            process.wait(timeout=10)
        with pytest.raises(httpx.ConnectError):
            httpx.get(f"http://127.0.0.1:{app_port}/health", timeout=1, trust_env=False)
