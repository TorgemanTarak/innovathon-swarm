"""Measure synthetic peers against an isolated coordinator over real loopback HTTP.

Run: uv run scripts/benchmark_fleet.py --sizes 32 128 512 --output report.json
No endpoint agents or demo apps run here. Fixture health/repair outcomes are
synthetic; this measures coordinator transport and lookup costs, not real repair,
macOS behavior, network-device connectivity, or a production capacity guarantee.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from swarm.models import (
    Candidate,
    CommandAck,
    Environment,
    Heartbeat,
    IssueReport,
    Outcome,
    Registration,
    Work,
    issue_key,
)
from swarm.registry import Registry


def seed_history(path: Path, size: int) -> list[str]:
    """Use typed registry operations; deliberately do not simulate app execution."""
    registry = Registry(path)
    environment = Environment(platform="linux")
    names = [f"synthetic-{i:05d}" for i in range(size)]
    incidents = {}
    for name in names:
        registry.register(
            Registration(agent_id=name, label=name, mode="simulated", environment=environment)
        )
        registry.heartbeat(name, Heartbeat(health="unhealthy", issue_key=issue_key()))
        incidents[name] = IssueReport(
            agent_id=name,
            issue_key=issue_key(),
            summary="Synthetic benchmark failure; no app operation",
            environment=environment,
            evidence_ids=["synthetic-failure"],
        )
        registry.report_incident(incidents[name])
    registry.enqueue("inject_fault", [])
    for name in names:
        command = registry.work(name).commands[0]
        registry.acknowledge(
            name, CommandAck(command_id=command.command_id, success=True, detail="Synthetic ack")
        )
    registry.heartbeat(names[0], Heartbeat(health="healthy"))
    registry.propose(
        Candidate(
            source_agent_id=names[0],
            incident_id=incidents[names[0]].incident_id,
            issue_key=issue_key(),
            environment=environment,
            evidence_ids=["synthetic-failure", "synthetic-change", "synthetic-health"],
            explanation="Synthetic benchmark fixture; no functional probes were executed",
            verification_count=3,
        )
    )

    def current_work(name: str) -> Work:
        # Setup can exceed the freshness window on slow storage or large fleets.
        registry.heartbeat(name, Heartbeat(health="unhealthy", issue_key=issue_key()))
        return registry.work(name)

    canary = current_work(names[1]).assignment
    assert canary is not None and canary.role == "canary"
    assert current_work(names[2]).assignment is None, "Follower bypassed the canary gate"
    for name in names[1:]:
        assignment = canary if name == names[1] else current_work(name).assignment
        assert assignment is not None
        if name != names[1]:
            assert assignment.role == "follower"
        registry.record_outcome(
            Outcome(
                assignment_id=assignment.assignment_id,
                agent_id=name,
                recipe_id=assignment.recipe.recipe_id,
                incident_id=assignment.incident_id,
                status="verified",
                checks_passed=3,
                detail="Synthetic benchmark outcome; no real repair or app verification",
            )
        )
        registry.heartbeat(name, Heartbeat(health="healthy"))
    return names


@dataclass
class Measurements:
    latencies: list[float] = field(default_factory=list)
    request_body_bytes: int = 0
    response_body_bytes: int = 0
    min_response_bytes: int | None = None
    max_response_bytes: int = 0
    errors: list[str] = field(default_factory=list)

    async def request(
        self, client: httpx.AsyncClient, method: str, path: str, payload: dict | None = None
    ) -> httpx.Response | None:
        started = time.perf_counter()
        try:
            response = await client.request(method, path, json=payload)
            self.request_body_bytes += len(response.request.content)
            length = len(response.content)
            self.response_body_bytes += length
            self.min_response_bytes = (
                length if self.min_response_bytes is None else min(self.min_response_bytes, length)
            )
            self.max_response_bytes = max(self.max_response_bytes, length)
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            self.errors.append(f"{method} {path}: {type(exc).__name__}")
            return None
        finally:
            self.latencies.append((time.perf_counter() - started) * 1000)

    def report(self) -> dict[str, Any]:
        ordered = sorted(self.latencies)
        return {
            "requests": len(ordered),
            "errors": len(self.errors),
            "error_examples": self.errors[:10],
            "request_body_bytes": self.request_body_bytes,
            "response_body_bytes": self.response_body_bytes,
            "min_response_body_bytes": self.min_response_bytes,
            "max_response_body_bytes": self.max_response_bytes,
            "latency_ms_p50": round(ordered[math.ceil(len(ordered) * 0.5) - 1], 3),
            "latency_ms_p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        }


async def measure(size: int, rounds: int, concurrency: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="swarm-http-benchmark-") as directory:
        root = Path(directory)
        database = root / "registry.sqlite"
        names = seed_history(database, size)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        token = secrets.token_urlsafe(24)
        env = {key: value for key, value in os.environ.items() if not key.startswith("SWARM_")}
        env["SWARM_TOKEN"] = token
        with (root / "coordinator.log").open("w") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "swarm",
                    "coordinator",
                    "--port",
                    str(port),
                    "--db",
                    str(database),
                ],
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}",
                    headers={"X-Swarm-Token": token},
                    timeout=15,
                    trust_env=False,
                    limits=httpx.Limits(
                        max_connections=concurrency, max_keepalive_connections=concurrency
                    ),
                ) as client:
                    deadline = time.monotonic() + 20
                    while True:
                        if process.poll() is not None:
                            raise RuntimeError((root / "coordinator.log").read_text()[-2000:])
                        try:
                            response = await client.get("/health")
                            response.raise_for_status()
                            break
                        except httpx.HTTPError:
                            if time.monotonic() >= deadline:
                                raise RuntimeError("Coordinator did not become ready") from None
                            await asyncio.sleep(0.1)
                    heartbeat, work = Measurements(), Measurements()
                    semaphore = asyncio.Semaphore(concurrency)
                    contract_errors = []

                    async def poll(name: str) -> None:
                        async with semaphore:
                            await heartbeat.request(
                                client,
                                "POST",
                                f"/api/agents/{name}/heartbeat",
                                {
                                    "health": "healthy",
                                    "detail": "Synthetic benchmark heartbeat; no app probe",
                                },
                            )
                            response = await work.request(client, "GET", f"/api/agents/{name}/work")
                            if response is not None:
                                try:
                                    payload = Work.model_validate(response.json())
                                    if payload.commands or payload.assignment or payload.paused:
                                        contract_errors.append(f"Unexpected work for {name}")
                                except ValueError:
                                    contract_errors.append(f"Invalid work contract for {name}")

                    started = time.perf_counter()
                    for _ in range(rounds):
                        await asyncio.gather(*(poll(name) for name in names))
                    elapsed = time.perf_counter() - started
                    response = await client.get("/api/state")
                    response.raise_for_status()
                    snapshot = response.json()
                    observed = {agent["agent_id"] for agent in snapshot["agents"]}
                    online = {agent["agent_id"] for agent in snapshot["agents"] if agent["online"]}
                    checks = {
                        "canary_gate_checked_during_synthetic_seeding": True,
                        "all_expected_ids_present": observed == set(names),
                        "all_expected_ids_online": online == set(names),
                        "heartbeat_and_work_counts_match": len(heartbeat.latencies)
                        == len(work.latencies)
                        == size * rounds,
                        "all_work_responses_empty_and_valid": not contract_errors
                        and not work.errors,
                        "work_response_size_bounded": work.max_response_bytes
                        <= len(Work().model_dump_json().encode()),
                        "no_http_errors": not heartbeat.errors and not work.errors,
                    }
                    return {
                        "synthetic_peers": size,
                        "completed_synthetic_assignments": size - 1,
                        "acknowledged_synthetic_commands": size,
                        "rounds": rounds,
                        "concurrent_peer_limit": concurrency,
                        "measured_seconds_excluding_setup": round(elapsed, 3),
                        "heartbeat": heartbeat.report(),
                        "work": work.report(),
                        "snapshot_body_bytes": len(response.content),
                        "online_peers": len(online),
                        "contract_errors": contract_errors[:10],
                        "checks": checks,
                        "passed": all(checks.values()),
                    }
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=positive, nargs="+", default=[32, 128, 512])
    parser.add_argument("--rounds", type=positive, default=2)
    parser.add_argument("--concurrency", type=positive, default=16)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if min(args.sizes) < 3:
        parser.error("sizes must be at least 3 to exercise a canary and follower")
    results = [asyncio.run(measure(size, args.rounds, args.concurrency)) for size in args.sizes]
    report = {
        "runtime": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
        },
        "scope": "Real loopback HTTP to isolated coordinator processes; synthetic peer registrations, heartbeats, evidence and outcomes. No endpoint agents, app probes, macOS or peer-to-peer testing.",
        "schedule": "Bounded concurrent batches, not the production agent polling loop. Timings exclude fixture seeding/startup and are descriptive, not capacity guarantees. Byte counts are HTTP bodies, not total wire traffic.",
        "results": results,
    }
    rendered = json.dumps(report, indent=2) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
