"""Four native launchers reconnect through real HTTP and a persistent coordinator.

These are separate local OS processes, not separate machines or macOS VMs.
The stale-heartbeat check waits the actual thirty-second production threshold.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest

from swarm.models import now_iso


def _ports(count: int) -> list[int]:
    with ExitStack() as stack:
        sockets = [stack.enter_context(socket.socket()) for _ in range(count)]
        for sock in sockets:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in sockets]


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def test_four_native_nodes_reconnect_without_duplicate_members_or_false_discovery(tmp_path: Path):
    coordinator_port, *app_ports = _ports(5)
    ids = [f"native-{letter}" for letter in "abcd"]
    token = "connectivity-test-shared-token"
    env = {key: value for key, value in os.environ.items() if not key.startswith("SWARM_")}
    env["SWARM_TOKEN"] = token
    children: list[subprocess.Popen] = []
    base_url = f"http://127.0.0.1:{coordinator_port}"

    def diagnostics() -> str:
        return "\n".join(
            f"{p.relative_to(tmp_path)}: {p.read_text()[-2000:]}" for p in tmp_path.rglob("*.log")
        )

    with ExitStack() as stack:
        client = stack.enter_context(
            httpx.Client(
                base_url=base_url,
                headers={"X-Swarm-Token": token},
                timeout=2,
                trust_env=False,
            )
        )

        def start(label: str, args: list[str]) -> subprocess.Popen:
            log = stack.enter_context((tmp_path / f"{label}.log").open("w"))
            process = subprocess.Popen(
                [sys.executable, "-m", "swarm", *args], stdout=log, stderr=log, env=env
            )
            children.append(process)
            return process

        def start_coordinator(label: str) -> subprocess.Popen:
            return start(
                label,
                [
                    "coordinator",
                    "--port",
                    str(coordinator_port),
                    "--db",
                    str(tmp_path / "coordinator.sqlite"),
                ],
            )

        def start_node(index: int, label: str) -> subprocess.Popen:
            return start(
                label,
                [
                    "node",
                    "--id",
                    ids[index],
                    "--label",
                    f"Native {index + 1}",
                    "--coordinator",
                    base_url,
                    "--data-dir",
                    str(tmp_path / ids[index]),
                    "--app-port",
                    str(app_ports[index]),
                    "--interval",
                    "0.15",
                ],
            )

        def wait(predicate: Callable[[dict], bool], timeout: float = 20) -> dict:
            deadline = time.monotonic() + timeout
            state = {}
            while time.monotonic() < deadline:
                try:
                    response = client.get("/api/state")
                    response.raise_for_status()
                    state = response.json()
                    if predicate(state):
                        return state
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            pytest.fail(f"Connectivity wait timed out: {state}\n{diagnostics()}")

        def healthy_four(state: dict) -> bool:
            return len(state["agents"]) == 4 and all(
                a["online"] and a["health"] == "healthy" for a in state["agents"]
            )

        try:
            coordinator = start_coordinator("coordinator-first")
            wait(lambda s: s["agents"] == [])
            nodes = [start_node(i, f"node-{i}-first") for i in range(4)]
            state = wait(healthy_four)
            assert {a["agent_id"] for a in state["agents"]} == set(ids)
            assert all(a["mode"] == "native" for a in state["agents"])

            _stop(nodes[3])
            # Wait for real heartbeat expiry; preserve the offline member in the registry.
            stale = wait(
                lambda s: not next(a for a in s["agents"] if a["agent_id"] == ids[3])["online"],
                timeout=40,
            )
            assert len(stale["agents"]) == 4
            assert sum(a["online"] for a in stale["agents"]) == 3
            assert all(a["health"] == "healthy" for a in stale["agents"])
            nodes[3] = start_node(3, "node-3-restarted")
            state = wait(healthy_four)
            assert {a["agent_id"] for a in state["agents"]} == set(ids)

            # Require every node to actually observe the outage before restarting the hub.
            log_paths = [tmp_path / identity / "process-logs/agent.log" for identity in ids]
            offsets = [path.stat().st_size for path in log_paths]
            _stop(coordinator)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if all(
                    "Coordinator unavailable" in path.read_text()[offset:]
                    or "Delivery deferred" in path.read_text()[offset:]
                    for path, offset in zip(log_paths, offsets, strict=True)
                ):
                    break
                time.sleep(0.1)
            else:
                pytest.fail(f"Nodes did not all observe coordinator outage\n{diagnostics()}")
            assert all(node.poll() is None for node in nodes)
            restart_after = now_iso()
            coordinator = start_coordinator("coordinator-restarted")
            recovered = wait(
                lambda s: (
                    healthy_four(s) and all(a["last_seen"] >= restart_after for a in s["agents"])
                )
            )
            assert {a["agent_id"] for a in recovered["agents"]} == set(ids)
            assert sum(e["kind"] == "agent_joined" for e in recovered["events"]) == 4
            assert all(a["incident_id"] is None for a in recovered["agents"])
            assert recovered["recipes"] == recovered["outcomes"] == []
        finally:
            for process in reversed(children):
                _stop(process)
            for port in [coordinator_port, *app_ports]:
                with socket.socket() as sock:
                    sock.settimeout(1)
                    assert sock.connect_ex(("127.0.0.1", port)) != 0, (
                        f"Port {port} was left listening"
                    )
