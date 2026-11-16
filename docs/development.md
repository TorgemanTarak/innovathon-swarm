# Development and verification

[Handbook](README.md) · [Module map](modules/README.md) · [Contributor rules](../AGENTS.md)

## Local workflow

Run commands from the repository root with Python 3.11+ and `uv` installed:

```bash
uv sync --frozen --extra dev
make check
make demo
```

`make check` runs Ruff lint, Ruff format checks, mypy over the source package and
the Python tests. It does not run the Node tests or optional browser suite.
Use `make format` to apply formatting when changing Python code. Commit dependency
changes through `pyproject.toml` and `uv.lock` together; do not silently replace
the locked dependency set with unpinned installs.

For coordinator-only iteration, `make dev` starts a reloading Uvicorn process on
loopback. It enables demo routes and uses `.swarm/dev-coordinator.sqlite`. Start
`swarm node` separately if that view needs real endpoints. For JavaScript/CSS
changes, refresh the browser; no bundler or Node build is needed.

## Choose the right test layer

| Layer | Command | What it establishes | What it does not establish |
|---|---|---|---|
| Python checks and tests | `make check` | Types/style plus contracts, filesystem behavior, registry policy, HTTP integration and isolated process scenarios. | A rendered browser, actual teammate Macs or large production fleets. |
| Node tests | `make test-dashboard` | Live dashboard logic in a simulated DOM and pure synthetic graph/routing behavior. | Browser layout, actual browser input or operating-system integration. |
| Real Chromium | `make test-browser` | Rendered controls, real browser events, auth/error flows, live recovery scenarios and mock-page isolation. | Physical device count, native macOS verification when run on Linux, or a real peer network. |
| HTTP benchmark | `make benchmark-fleet` | Coordinator requests and response sizes under a bounded synthetic workload. | Real repair execution for every fixture or production capacity. |
| Multi-Mac rehearsal | Follow [getting started](getting-started.md) on actual Macs. | Those hosts, network paths and configurations, when exercised. | Arbitrary enterprise environments or Windows support. |

Node 22+ is needed only for `make test-dashboard`. The tests use Node's built-in
runner, with no npm dependency installation. The Makefile includes both
`tests/dashboard.test.cjs` and `tests/mock-fleet-model.test.cjs`.

For optional browser dependencies:

```bash
uv sync --frozen --extra dev --extra browser
uv run --extra browser playwright install chromium
make test-browser
```

Chromium also needs its usual system libraries on Linux. An already installed
browser can be selected with `SWARM_BROWSER_EXECUTABLE`; the default fixture is
headless Chromium. To retain screenshots and traces:

```bash
SWARM_BROWSER_ARTIFACTS=.swarm/browser-artifacts make test-browser
```

Each browser test starts its own temporary coordinator/apps/agents and uses an
explicit test token. Successful repair scenarios use the real backend; error
tests intercept requests specifically to inject failure conditions. A mock-page
test checks synthetic rendering and verifies that the page makes no fleet API
requests. Traces and screenshots are diagnostics, not additional endpoint evidence.

## Where to find behavioral coverage

| Area | Tests to read first |
|---|---|
| Functional cache failure, marked roots and rollback | [`test_demo_app.py`](../tests/test_demo_app.py), [`test_repairs.py`](../tests/test_repairs.py) |
| Observation, discovery, retry, interrupted attempts and concurrent human edits | [`test_agent.py`](../tests/test_agent.py) |
| Canary admission, outcomes, pause/reset, auth and persistence | [`test_coordinator.py`](../tests/test_coordinator.py) |
| Shared evidence fields and legacy behavior | [`test_evidence.py`](../tests/test_evidence.py) |
| Eligibility reasons, replay roles and bounded preview work | [`test_eligibility.py`](../tests/test_eligibility.py) |
| SQL history growth and index upgrades | [`test_registry_scaling.py`](../tests/test_registry_scaling.py) |
| CLI defaults, process lifetime and real fleet flow | [`test_cli.py`](../tests/test_cli.py), [`test_integration.py`](../tests/test_integration.py) |
| Reconnect and heartbeat freshness | [`test_connectivity.py`](../tests/test_connectivity.py) |
| Local-model boundaries, malformed results and deadlines | [`test_investigator.py`](../tests/test_investigator.py) |
| Simulated DOM and pure graph model | [`dashboard.test.cjs`](../tests/dashboard.test.cjs), [`mock-fleet-model.test.cjs`](../tests/mock-fleet-model.test.cjs) |
| Rendered live and mock dashboards | [`test_dashboard.py`](../browser_tests/test_dashboard.py), [`test_mock_dashboard.py`](../browser_tests/test_mock_dashboard.py) |

For a targeted iteration, for example:

```bash
uv run pytest -q tests/test_agent.py tests/test_repairs.py
node --test tests/mock-fleet-model.test.cjs
uv run --frozen --extra dev --extra browser pytest -q browser_tests/test_mock_dashboard.py
```

Choose tests for the behavior you changed, then run required repository checks
before publishing. Do not infer actual browser coverage from an API test or
simulated DOM result. Record failures and subsequent fixes; an account/runner
failure before test steps start is different from failing application tests.

## Adding a second supported repair

This is an extension plan, not an already implemented plugin system. The current
action is wired explicitly through contracts, learning, execution and policy.

1. Define the recognized failure and a functional operation that proves whether
   it is present. Keep observations within an app-owned or explicitly supported
   scope; process liveness alone is insufficient.
2. Define a typed action and precise applicability in `models.py`. Keep action
   selection independent of explanation text. No arbitrary command or file path
   should become executable because a candidate or model supplied it.
3. Implement preconditions, mutation, verification and action-specific rollback
   in the endpoint path. Specify what happens when a human changes the resource
   concurrently or the agent stops halfway through.
4. Extend deterministic observation/learning and reset behavior. Ensure SWARM's
   own action does not create a new independent human-repair candidate.
5. Update coordinator validation, cohort/eligibility policy and delivery if the
   new action needs different capabilities. A dashboard explanation must match
   the same decision used by actual dispatch.
6. Preserve durable attempt and outbox behavior. Exercise duplicate delivery,
   response loss, restart, timeout, failed verification, rollback conflict,
   incompatible devices and global/recipe pause.
7. Add UI labels for the new action and evidence where needed; update the runbook,
   API/data references and architecture. Run process tests as well as unit tests.

Do not assume a generic observer/actuator protocol, Windows backend, Nexthink
executor or terminal tool loop already exists. Those are [roadmap](roadmap.md)
items. A future adapter must retain deterministic authorization and bounded
execution even if investigation or narration becomes more capable.

## Contracts, storage and documentation changes

Strict Pydantic contracts reject unknown fields. Adding an optional field can
preserve readability of old stored JSON while still breaking communication with
an older peer. Update coordinator and agents together until mixed-version
negotiation exists; do not equate default values with fleet wire compatibility.

When changing SQLite behavior, cover opening an existing database and maintaining
ID/attempt semantics. Never edit a live store to make a demo appear successful.
Use isolated temporary data for tests and [operations](operations.md) for supported
reset or backup procedures.

For a documentation-only change, check relative links, source symbols, copied
commands and generated schema/CLI defaults. Follow the existing `make check`
requirement before publishing; no browser result should be claimed unless that
browser run actually occurred.

## CI and review

The [GitHub Actions workflow](../.github/workflows/ci.yml) is configured for pull
requests and pushes to `main`, with Ubuntu and macOS jobs. Each job installs the
locked dev dependencies, runs Ruff/mypy/pytest, then Node tests. The optional
Chromium suite is not part of that workflow.

A runner label is not evidence that a native run completed. Inspect the run's
steps and annotations when reporting CI status. Before merging, the PR should
explain the behavior or documentation changed, the checks actually run and any
remaining validation gap. Keep this handbook in the same PR as the change it
explains so team instructions and code remain reviewable together.
