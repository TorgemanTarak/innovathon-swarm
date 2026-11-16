# Configuration reference

[Documentation index](README.md) · [Getting started](getting-started.md) · [Operations](operations.md)

There is no YAML/JSON configuration file. The command parser in
[`cli.py`](../src/swarm/cli.py) supplies flags and defaults; a few environment
variables apply to particular entry points. Run commands from the repository root
so relative paths are predictable.

## CLI commands and defaults

| Command | Flags and defaults | What it starts or changes |
|---|---|---|
| `swarm demo` | `--data-dir .swarm/local`, `--port 8765`, `--app-port 8771`, `--nodes 3`, `--include-incompatible` off, `--interval 1.0`, model flags below | Coordinator plus app/agent pairs on loopback. `--nodes` accepts 2–8 compatible nodes; the incompatible flag adds one more. App ports increase from `--app-port`. |
| `swarm coordinator` | `--host 127.0.0.1`, `--port 8765`, `--db .swarm/coordinator.sqlite`, `--demo-mode` off | Registry, API, live dashboard and topology lab. The flag enables fault/repair/failure/reset demo endpoints. |
| `swarm node` | Required `--id`, `--data-dir`; `--label` defaults to ID; `--coordinator http://127.0.0.1:8765`, `--app-version 1.0.0`, `--interval 1.0`, `--app-port 8771`, model flags | Supervised local app and native-mode agent. |
| `swarm agent` | Same shared node flags; required `--demo-url`; `--mode native` or `simulated`; no `--app-port` | Agent only; its app must already be running and use the same data directory/version. |
| `swarm app` | Required `--data-dir`; `--port 8771`, `--app-version 1.0.0` | Sample app bound to `127.0.0.1`. |
| `swarm fault` | Required `--data-dir` | Corrupts the known cache in an existing marked endpoint directory. |
| `swarm repair` | Required `--data-dir` | Quarantines that directory's known cache. |
| `swarm reset` | Required `--data-dir` | Restores local sample-app resources; does **not** reset coordinator records or the agent journal. |
| `swarm status` | `--coordinator http://127.0.0.1:8765`; optional `--expect-agents ID [ID ...]` | Prints `/api/state`; fails if any expected ID is missing/offline. |

Every command accepts `--help`; `swarm --version` prints the package CLI version.
The console command and `uv run python -m swarm` use the same entry point.

```bash
uv run swarm --help
uv run swarm demo --help
uv run swarm coordinator --help
uv run swarm node --help
uv run swarm agent --help
uv run swarm app --help
uv run swarm fault --help
uv run swarm repair --help
uv run swarm reset --help
uv run swarm status --help
```

CLI polling intervals must be at least 0.1 seconds. They are delays after a tick
finishes, not a guaranteed heartbeat frequency: HTTP requests, verification and
optional model narration add time. `--mode` describes deployment provenance; it
does not select or emulate an operating system. Platform comes from the actual
host; CPU architecture is not currently part of the environment contract.

`--data-dir` supports `~` expansion. For `--db`, prefer a relative path or an
explicit absolute path such as `"$HOME/.swarm-demo/coordinator.sqlite"`; the CLI
does not explicitly expand a quoted `~` in that flag. Each endpoint directory is
bound to its agent ID. The coordinator DB is a separate path, not an endpoint root.

## Environment variables and precedence

| Variable | Consumer | Behavior |
|---|---|---|
| `SWARM_TOKEN` | CLI coordinator, agents, status; Uvicorn server | Empty/unset disables API token checks. Otherwise clients send `X-Swarm-Token`. `demo` and `node` children inherit it. There is no token CLI flag. |
| `SWARM_LLM_BASE_URL` | `demo`, `node`, `agent` parser | Default for `--llm-base-url`; explicit flag wins. Only loopback HTTP(S), with no path or `/v1`, is accepted by the adapter. |
| `SWARM_LLM_MODEL` | Same parser commands | Default for `--llm-model`; explicit flag wins. Both URL and model must be present to enable narration. |
| `SWARM_DB` | `swarm.server:app` only | Coordinator DB path for direct Uvicorn startup; default `.swarm/dev-coordinator.sqlite`. CLI `swarm coordinator` uses `--db` instead. |
| `SWARM_DEMO_MODE` | `swarm.server:app` only | Demo controls enabled only for the exact value `1`. CLI coordinator uses `--demo-mode` instead. |
| `SWARM_BROWSER_EXECUTABLE` | Browser test fixture | Optional Chromium executable path; otherwise Playwright's installed Chromium is used. |
| `SWARM_BROWSER_ARTIFACTS` | Browser test fixture | Optional directory for screenshots and traces. Not an application setting. |

In a local rehearsal only A receives the configured local-model settings; the
supervisor explicitly disables them for other agents. Separate `node` commands
can configure a model on each machine. To override inherited model settings:

```bash
uv run swarm demo --llm-base-url "" --llm-model ""
```

The adapter narrates an already detected candidate and falls back to deterministic
text on failure. It neither downloads a model nor authorizes execution. See
[endpoint modules](modules/endpoint.md).

## Binding, ports and independent rehearsals

CLI coordinator binding outside `127.0.0.1`, `localhost` or `::1` requires a
`SWARM_TOKEN` of at least 16 characters. Direct Uvicorn startup does **not** run
that CLI binding guard; keep development startup on loopback. See
[security](security.md) for the trust model and unimplemented production controls.

For a second local rehearsal, choose a separate data root and free, nonoverlapping
coordinator/app ports:

```bash
uv run swarm demo --include-incompatible \
  --data-dir .swarm/rehearsal-two --port 8865 --app-port 8871
```

`make dev` starts only a reloading coordinator; it does not start endpoint apps or
agents. Its underlying command is:

```bash
SWARM_DEMO_MODE=1 uv run uvicorn swarm.server:app \
  --reload --reload-dir src --host 127.0.0.1 --port 8765
```

Do not run it alongside `swarm demo` on the same coordinator port. SWARM's Python
HTTP clients use `trust_env=False`, so system HTTP proxy variables are not used
for coordinator, app-probe or local-model traffic.

## Fixed runtime policies

These are code defaults, **not** supported CLI/environment tuning knobs:

| Policy | Current value/source |
|---|---|
| Source recovery / replay verification | Three consecutive successful functional checks in [`agent.py`](../src/swarm/agent.py); replay probes are separated by `min(interval, 1.0)` seconds. |
| Agent coordinator requests / app probes | 5-second HTTP timeout / 3-second probe timeout. |
| Assignment deadline | 120 seconds by default in [`Registry`](../src/swarm/registry.py); constructor override exists for callers/tests. |
| Online freshness | Last-seen time within 30 seconds; initialized by registration and refreshed by heartbeats. |
| Local-model deadline | 8 seconds, including a total call deadline. |
| Supervisor startup/shutdown | Up to 20 seconds waiting for each service; termination waits against a shared 5-second deadline before killing remaining children. |
| Dashboard polling | 1 second after success, 3 seconds after failure; 8-second request timeout. |
| SQLite coordinator lock wait | 10 seconds. |

Changing these policies requires coordinated code review and tests, not an
undocumented environment override. The [API](api.md) and
[data/state guide](data-and-state.md) describe their observable effects.
