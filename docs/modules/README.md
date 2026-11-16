# Module map

[Handbook](../README.md) · [Architecture](../architecture.md)

Start with the boundary you need to change. HTTP handlers are intentionally thin;
the registry owns fleet policy, and the agent plus filesystem helpers own local
execution. The dashboard presents state and requests controls.

## Python package

| Source | Responsibility | Detailed guide |
|---|---|---|
| [`__init__.py`](../../src/swarm/__init__.py) | Package description and `__version__`. | [Launchers](interfaces.md#entry-points-and-process-supervision) |
| [`__main__.py`](../../src/swarm/__main__.py) | Makes `python -m swarm` invoke the CLI. | [Launchers](interfaces.md#entry-points-and-process-supervision) |
| [`cli.py`](../../src/swarm/cli.py) | Argument parsing, process supervision, local demo commands and fleet status. | [Launchers](interfaces.md#entry-points-and-process-supervision) |
| [`server.py`](../../src/swarm/server.py) | Environment-configured ASGI application for Uvicorn development. | [Launchers](interfaces.md#entry-points-and-process-supervision) |
| [`models.py`](../../src/swarm/models.py) | Strict Pydantic wire contracts, IDs, timestamps and issue signature. | [Coordinator and contracts](coordinator.md) |
| [`coordinator.py`](../../src/swarm/coordinator.py) | FastAPI routes, API token checks, demo guards and static pages. | [Coordinator and contracts](coordinator.md) |
| [`registry.py`](../../src/swarm/registry.py) | SQLite fleet storage, eligibility, canary admission, assignments, outcomes and reset. | [Coordinator and contracts](coordinator.md) |
| [`agent.py`](../../src/swarm/agent.py) | Polling, observations, deterministic discovery, replay and durable delivery. | [Endpoint modules](endpoint.md) |
| [`journal.py`](../../src/swarm/journal.py) | Local observations, learning state, outbox, attempts and command acknowledgements. | [Endpoint modules](endpoint.md), [data and state](../data-and-state.md) |
| [`repairs.py`](../../src/swarm/repairs.py) | Marked data roots, cache validation, quarantine, rollback and demo fault/reset operations. | [Endpoint modules](endpoint.md) |
| [`demo_app.py`](../../src/swarm/demo_app.py) | App UI, liveness route, real cache-dependent operation and local app log. | [Endpoint modules](endpoint.md) |
| [`investigator.py`](../../src/swarm/investigator.py) | Optional bounded local-model narration with deterministic fallback. | [Endpoint modules](endpoint.md) |

## Browser assets

All assets ship from [`src/swarm/web/`](../../src/swarm/web/), without a Node build,
package install or remote asset service.

| Source | Responsibility |
|---|---|
| [`index.html`](../../src/swarm/web/index.html) | Live dashboard structure, accessible controls and connection dialog. |
| [`app.js`](../../src/swarm/web/app.js) | Authenticated polling, response validation, evidence/eligibility rendering and control requests. |
| [`styles.css`](../../src/swarm/web/styles.css) | Shared brand tokens, layout, component states and responsive behavior. |
| [`mock.html`](../../src/swarm/web/mock.html) | Separate synthetic topology page and route controls. |
| [`mock.css`](../../src/swarm/web/mock.css) | Topology lab layout, coordinator overlay, inspector and legends. |
| [`mock-fleet-model.js`](../../src/swarm/web/mock-fleet-model.js) | Pure deterministic graph generation and prefix routing. |
| [`mock-fleet.js`](../../src/swarm/web/mock-fleet.js) | Canvas rendering, selection, route animation and controls; no fleet API calls. |

See [browser interfaces](interfaces.md#live-dashboard) for polling, failure
behavior and the separation of the live and mock views. The endpoint app's small
HTML interface is embedded in `demo_app.py`, not in this asset folder.

## Tooling and verification

| File or directory | Purpose |
|---|---|
| [`pyproject.toml`](../../pyproject.toml) | Package metadata, `swarm` entry point, dependencies, optional extras and Python check configuration. |
| [`uv.lock`](../../uv.lock) | Committed dependency resolution for reproducible `uv sync --frozen`. |
| [`Makefile`](../../Makefile) | Setup, launch, check, browser/Node test and benchmark shortcuts. |
| [`tests/`](../../tests/) | Python unit/API/process coverage and dependency-free Node tests. |
| [`browser_tests/`](../../browser_tests/) | Optional real Chromium tests against isolated app/agent/coordinator processes. |
| [`scripts/benchmark_fleet.py`](../../scripts/benchmark_fleet.py) | Real loopback HTTP measurements with synthetic registrations and outcomes. |
| [CI workflow](../../.github/workflows/ci.yml) | Ubuntu/macOS Python checks and Node tests; the optional browser suite is not run here. |

Read [development](../development.md) before choosing a verification command.
Files under `.swarm/` and `.venv/` are runtime data and local dependencies, not
additional source modules.
