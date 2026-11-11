"""Small, native process launcher. No Docker, VM, sudo or cloud account required."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import TextIO

import httpx
from fastapi import FastAPI


class ProcessGroup:
    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = log_dir
        self.children: list[tuple[subprocess.Popen[bytes], TextIO, str]] = []

    def start(self, name: str, args: list[str]) -> None:
        log = (self.log_dir / f"{name}.log").open("a", encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-m", "swarm", *args], stdout=log, stderr=log)
        self.children.append((process, log, name))

    def check(self) -> None:
        for process, _, name in self.children:
            if process.poll() is not None:
                raise RuntimeError(
                    f"{name} exited ({process.returncode}). See {self.log_dir / (name + '.log')}"
                )

    def wait_ready(self, url: str, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.check()
            try:
                if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.15)
        raise RuntimeError(f"Service not ready at {url}. See {self.log_dir}")

    def wait(self) -> None:
        while True:
            self.check()
            time.sleep(0.3)

    def close(self) -> None:
        for process, _, _ in reversed(self.children):
            if process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 5
        for process, log, _ in reversed(self.children):
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            log.close()


def _serve(app: FastAPI, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


def _check_bind(host: str, token: str | None) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"} and (not token or len(token) < 16):
        raise ValueError(
            "LAN binding requires SWARM_TOKEN of at least 16 characters. Bind 127.0.0.1 for a token-free local rehearsal."
        )


def _agent_args(args: argparse.Namespace, *, demo_url: str, mode: str = "native") -> list[str]:
    result = [
        "agent",
        "--id",
        args.id,
        "--label",
        args.label or args.id,
        "--coordinator",
        args.coordinator,
        "--data-dir",
        str(Path(args.data_dir).expanduser().resolve()),
        "--demo-url",
        demo_url,
        "--app-version",
        args.app_version,
        "--interval",
        str(args.interval),
        "--mode",
        mode,
    ]
    # Credentials remain in the process environment, never command-line arguments or URLs.
    # Explicit empty values override inherited parser defaults for rehearsal
    # peers whose local-model narration was deliberately disabled by the parent.
    result += ["--llm-base-url", args.llm_base_url or ""]
    result += ["--llm-model", args.llm_model or ""]
    return result


def _supervise(group: ProcessGroup, launch: Callable[[], None]) -> None:
    previous = signal.getsignal(signal.SIGTERM)

    def terminate(signum: int, frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        launch()
        group.wait()
    except KeyboardInterrupt:
        print("\nStopping SWARM processes…", flush=True)
    finally:
        group.close()
        signal.signal(signal.SIGTERM, previous)


def run_demo(args: argparse.Namespace) -> None:
    root = Path(args.data_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    group = ProcessGroup(root / "logs")
    base = f"http://127.0.0.1:{args.port}"

    def launch() -> None:
        group.start(
            "coordinator",
            [
                "coordinator",
                "--port",
                str(args.port),
                "--db",
                str(root / "coordinator.sqlite"),
                "--demo-mode",
            ],
        )
        group.wait_ready(base + "/health")
        count = args.nodes + (1 if args.include_incompatible else 0)
        for i in range(count):
            agent_id = f"mac-{chr(97 + i)}"
            version = "2.0.0" if i >= args.nodes else "1.0.0"
            data = root / agent_id
            app_port = args.app_port + i
            url = f"http://127.0.0.1:{app_port}"
            group.start(
                f"{agent_id}-app",
                ["app", "--data-dir", str(data), "--port", str(app_port), "--app-version", version],
            )
            group.wait_ready(url + "/health")
            node = argparse.Namespace(
                id=agent_id,
                label=f"Mac {chr(65 + i)}",
                coordinator=base,
                data_dir=data,
                app_version=version,
                interval=args.interval,
                llm_base_url=args.llm_base_url if i == 0 else None,
                llm_model=args.llm_model if i == 0 else None,
            )
            group.start(f"{agent_id}-agent", _agent_args(node, demo_url=url, mode="simulated"))
        print(
            f"\nSWARM running → {base}\n{count} isolated endpoints on this machine (local rehearsal).\nData and logs: {root}\nUse the dashboard: Inject fault → Repair one device → watch the canary.\nCtrl+C stops all processes.\n",
            flush=True,
        )

    _supervise(group, launch)


def run_node(args: argparse.Namespace) -> None:
    from swarm.repairs import initialize_root

    root = initialize_root(Path(args.data_dir).expanduser())
    group = ProcessGroup(root / "process-logs")
    url = f"http://127.0.0.1:{args.app_port}"

    def launch() -> None:
        group.start(
            "app",
            [
                "app",
                "--data-dir",
                str(root),
                "--port",
                str(args.app_port),
                "--app-version",
                args.app_version,
            ],
        )
        group.wait_ready(url + "/health")
        group.start("agent", _agent_args(args, demo_url=url))
        print(
            f"\nEndpoint {args.id} running.\nDemo app: {url}\nCoordinator: {args.coordinator}\nData: {root}\nCtrl+C stops this endpoint.\n",
            flush=True,
        )

    _supervise(group, launch)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="swarm", description="A native, evidence-driven repair swarm. Start with: swarm demo"
    )
    p.add_argument("--version", action="version", version="SWARM 0.1.0")
    sub = p.add_subparsers(dest="command", required=True)

    def model_flags(s: argparse.ArgumentParser) -> None:
        s.add_argument(
            "--llm-base-url",
            default=os.getenv("SWARM_LLM_BASE_URL"),
            help="Optional local OpenAI-compatible URL ending /v1",
        )
        s.add_argument(
            "--llm-model",
            default=os.getenv("SWARM_LLM_MODEL"),
            help="Model name served at the local URL",
        )

    demo = sub.add_parser("demo", help="Launch coordinator plus isolated endpoints on one machine")
    demo.add_argument("--data-dir", default=".swarm/local")
    demo.add_argument("--port", type=int, default=8765)
    demo.add_argument("--app-port", type=int, default=8771, help="First demo app port")
    demo.add_argument("--nodes", type=int, choices=range(2, 9), default=3)
    demo.add_argument("--include-incompatible", action="store_true")
    demo.add_argument("--interval", type=float, default=1.0)
    model_flags(demo)
    server = sub.add_parser("coordinator", help="Run the shared repair registry and dashboard")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--db", default=".swarm/coordinator.sqlite")
    server.add_argument(
        "--demo-mode", action="store_true", help="Enable explicit fault/repair/reset demo controls"
    )
    for name in ("node", "agent"):
        s = sub.add_parser(
            name,
            help="Run an endpoint and its app" if name == "node" else "Run just the endpoint agent",
        )
        s.add_argument("--id", required=True)
        s.add_argument("--label")
        s.add_argument("--coordinator", default="http://127.0.0.1:8765")
        s.add_argument("--data-dir", required=True)
        s.add_argument("--app-version", default="1.0.0")
        s.add_argument("--interval", type=float, default=1.0)
        model_flags(s)
        if name == "node":
            s.add_argument("--app-port", type=int, default=8771)
        else:
            s.add_argument("--demo-url", required=True)
            s.add_argument("--mode", choices=("native", "simulated"), default="native")
    app = sub.add_parser("app", help="Run the intentionally breakable demo application")
    app.add_argument("--data-dir", required=True)
    app.add_argument("--port", type=int, default=8771)
    app.add_argument("--app-version", default="1.0.0")
    for name in ("fault", "repair", "reset"):
        s = sub.add_parser(name, help=f"Perform a local demo {name} in a marked SWARM directory")
        s.add_argument("--data-dir", required=True)
    status = sub.add_parser("status", help="Print the current fleet snapshot")
    status.add_argument("--coordinator", default="http://127.0.0.1:8765")
    status.add_argument(
        "--expect-agents",
        nargs="+",
        metavar="ID",
        help="Exit unsuccessfully if any listed endpoint is missing or has no recent heartbeat",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    token = os.getenv("SWARM_TOKEN") or None
    try:
        if hasattr(args, "interval") and args.interval < 0.1:
            raise ValueError("--interval must be at least 0.1 seconds")
        if args.command == "demo":
            run_demo(args)
        elif args.command == "node":
            run_node(args)
        elif args.command == "coordinator":
            from swarm.coordinator import create_app as create_coordinator_app

            _check_bind(args.host, token)
            _serve(
                create_coordinator_app(Path(args.db), token=token, demo_mode=args.demo_mode),
                args.host,
                args.port,
            )
        elif args.command == "app":
            from swarm.demo_app import create_app as create_demo_app

            _serve(
                create_demo_app(Path(args.data_dir).expanduser(), app_version=args.app_version),
                "127.0.0.1",
                args.port,
            )
        elif args.command == "agent":
            from swarm.agent import AgentConfig, EndpointAgent

            config = AgentConfig(
                agent_id=args.id,
                label=args.label or args.id,
                data_dir=Path(args.data_dir).expanduser(),
                coordinator_url=args.coordinator,
                demo_url=args.demo_url,
                token=token,
                app_version=args.app_version,
                interval=args.interval,
                mode=args.mode,
                llm_base_url=args.llm_base_url,
                llm_model=args.llm_model,
            )
            asyncio.run(EndpointAgent(config).run())
        elif args.command in {"fault", "repair", "reset"}:
            from swarm.repairs import inject_fault, quarantine_cache, reset_demo

            root = Path(args.data_dir).expanduser()
            if args.command == "fault":
                inject_fault(root)
            elif args.command == "repair":
                quarantine_cache(root)
            else:
                reset_demo(root)
            print(f"Demo {args.command} complete: {root}")
        elif args.command == "status":
            headers = {"X-Swarm-Token": token} if token else {}
            response = httpx.get(
                args.coordinator.rstrip("/") + "/api/state",
                headers=headers,
                timeout=5,
                trust_env=False,
            )
            response.raise_for_status()
            snapshot = response.json()
            print(json.dumps(snapshot, indent=2))
            if args.expect_agents:
                agents = {agent["agent_id"]: agent for agent in snapshot["agents"]}
                missing = [name for name in args.expect_agents if name not in agents]
                offline = [
                    name
                    for name in args.expect_agents
                    if name in agents and not agents[name]["online"]
                ]
                if missing or offline:
                    raise RuntimeError(
                        "Expected fleet is not fully online. "
                        f"Missing: {', '.join(missing) or 'none'}. "
                        f"Offline: {', '.join(offline) or 'none'}."
                    )
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, RuntimeError, httpx.HTTPError) as exc:
        print(f"SWARM: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
