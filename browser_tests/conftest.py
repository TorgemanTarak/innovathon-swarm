"""Optional real-Chromium fixtures, separate from the dependency-free JS tests."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

TOKEN = "swarm-browser-test-token"


def free_port_block() -> int:
    for _ in range(100):
        sockets = []
        try:
            first = socket.socket()
            sockets.append(first)
            first.bind(("127.0.0.1", 0))
            port = first.getsockname()[1]
            if port > 65530:
                continue
            for candidate in range(port + 1, port + 5):
                sock = socket.socket()
                sockets.append(sock)
                sock.bind(("127.0.0.1", candidate))
            return port
        except OSError:
            pass
        finally:
            for sock in sockets:
                sock.close()
    raise RuntimeError("Could not reserve five browser-test ports")


@dataclass
class Fleet:
    root: Path
    url: str
    client: httpx.Client
    process: subprocess.Popen

    def wait(self, predicate: Callable[[dict], bool], timeout: float = 30) -> dict:
        """Supplement browser assertions with real coordinator audit evidence."""
        deadline = time.monotonic() + timeout
        state: dict[str, Any] = {}
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                pytest.fail(f"Demo supervisor exited with {self.process.returncode}")
            try:
                response = self.client.get("/api/state")
                response.raise_for_status()
                state = response.json()
                if predicate(state):
                    return state
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        pytest.fail(f"Timed out waiting for fleet: {json.dumps(state)}")


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[Fleet]:
    port = free_port_block()
    root = tmp_path / "fleet"
    env = {key: value for key, value in os.environ.items() if not key.startswith("SWARM_")}
    env["SWARM_TOKEN"] = TOKEN
    with (tmp_path / "supervisor.log").open("w") as log:
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
                "--include-incompatible",
                "--interval",
                "1.0",
            ],
            env=env,
            stdout=log,
            stderr=log,
        )
        url = f"http://127.0.0.1:{port}"
        with httpx.Client(
            base_url=url, headers={"X-Swarm-Token": TOKEN}, timeout=3, trust_env=False
        ) as client:
            instance = Fleet(root, url, client, process)
            try:
                instance.wait(
                    lambda s: (
                        len(s["agents"]) == 4 and all(a["health"] == "healthy" for a in s["agents"])
                    )
                )
                yield instance
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        options = {}
        if executable := os.environ.get("SWARM_BROWSER_EXECUTABLE"):
            options["executable_path"] = executable
        browser = playwright.chromium.launch(headless=True, **options)
        yield browser
        browser.close()


@pytest.fixture
def page(browser: Browser, request: pytest.FixtureRequest) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    errors: list[str] = []
    context.on("page", lambda tab: tab.on("pageerror", lambda error: errors.append(str(error))))
    page = context.new_page()
    page.set_default_timeout(15_000)
    expect.set_options(timeout=15_000)
    artifacts = os.environ.get("SWARM_BROWSER_ARTIFACTS")
    if artifacts:
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
    try:
        yield page
    finally:
        if artifacts:
            directory = Path(artifacts)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(directory / f"{request.node.name}.png"), full_page=True)
            context.tracing.stop(path=str(directory / f"{request.node.name}.zip"))
        context.close()
        assert not errors, f"Browser JavaScript errors: {errors}"


def connect(page: Page, fleet: Fleet) -> None:
    page.goto(fleet.url)
    expect(page.locator("#access-dialog")).to_be_visible()
    page.locator("#access-token").fill(TOKEN)
    page.locator("#access-form button[type=submit]").click()
    expect(page.locator("#metric-healthy")).to_have_text("4")
    expect(page.locator("#fault-button")).to_be_enabled()
