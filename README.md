# SWARM

**One device heals. Every device heals.**

A working, Mac-first prototype for Nexthink Innovathon: observe a local recovery, share a repair hypothesis, validate it on one canary, then let compatible endpoints heal themselves.

The demo is a real distributed application. Each endpoint owns its cache, app process, local journal and repair executor. The coordinator shares typed recipes and measured outcomes. A one-machine rehearsal runs the same components as separate processes and explicitly labels the endpoints simulated.

## Run it

Prerequisites: Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/). No Docker, VM, sudo, Node build, cloud API key or model download required.

```bash
git clone https://github.com/TorgemanTarak/innovathon-swarm.git
cd innovathon-swarm
git switch codex/swarm-foundation
uv sync --frozen --extra dev
uv run swarm demo --include-incompatible
```

Open **http://127.0.0.1:8765**. Three compatible endpoints and one incompatible endpoint run on your machine. Their demo apps use ports 8771–8774. Press Ctrl+C to stop all child processes. State and logs persist under `.swarm/local/`.

1. Wait for all endpoints to report healthy.
2. **Inject fault** to corrupt each app's actual cache. Loading projects now fails.
3. Select **Mac A** and **Repair one device**. This button simulates a human quarantining A's cache.
4. A observes that change and three successful functional operations, then proposes a repair.
5. B or C becomes the canary. The other receives the recipe only after the canary verifies recovery.
6. D stays broken because its app version is incompatible. The activity log explains why.
7. **Reset demo** restores the app resources and clears the shared learned mappings.

For a genuinely manual originating repair, perform this instead of step 3:

```bash
uv run swarm repair --data-dir .swarm/local/mac-a
# Or rename .swarm/local/mac-a/cache/workspace.json yourself.
```

Use each app's **Load workspace** operation to see the fault and recovery. `/health` only checks process liveness; `/work` reads and validates the real cache.

## Run on teammates' Macs

Each teammate checks out this branch and runs `uv sync --frozen --extra dev`.

On the coordinator Mac, choose a shared demo token and export it. The following generates a token in your own shell; distribute it privately to the participating teammates:

```bash
export SWARM_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
uv run swarm coordinator --host 0.0.0.0 --port 8765 --demo-mode
```

On each participating Mac, export that same `SWARM_TOKEN`, then run:

```bash
uv run swarm node \
  --id tarak-mac --label "Tarak's Mac" \
  --coordinator http://COORDINATOR_LAN_IP:8765 \
  --data-dir "$HOME/.swarm-demo/tarak" \
  --app-port 8771
```

Use a unique `--id` on each Mac. Enter the token in the dashboard's **Connection** dialog. `node` starts both the app and the endpoint agent. Data directories must be empty on first use; SWARM creates a marker and only modifies its own known app resources. `~/...` paths work.

Check LAN reachability early: office Wi-Fi isolation and host firewalls can block direct laptop connections. The local rehearsal is the fallback. The shared token is a trusted-team demo mechanism; use this only on an appropriate private network. TLS, per-device identities and production enrollment are future work.

## Developer experience

```bash
make setup       # Install exact dependencies from uv.lock
make demo        # Local rehearsal including incompatible endpoint
make dev         # Uvicorn with source reload on localhost:8765
make check       # Ruff lint + format check + mypy + pytest
make format      # Apply Ruff fixes and formatting
```

`make dev` runs only the coordinator. Launch one or more `swarm node` commands in separate terminals against it. This mode uses `.swarm/dev-coordinator.sqlite`; the complete demo uses `.swarm/local/coordinator.sqlite`.

Equivalent explicit checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
```

Mypy checks every source module, checks typed and untyped function bodies, and requires function annotations. CI runs these checks on Linux and macOS. The process integration test exercises real HTTP services, local files, manual repair discovery, canary ordering, incompatible rejection, reset, pause and failed-verification rollback.

To use Uvicorn directly:

```bash
SWARM_DEMO_MODE=1 uv run uvicorn swarm.server:app \
  --reload --reload-dir src --host 127.0.0.1 --port 8765
```

Interactive API docs: `http://127.0.0.1:8765/docs`. Protected API calls require the `X-Swarm-Token` header when `SWARM_TOKEN` is configured.

## Optional local model

The default learner is deterministic. An optional local model **explains an already detected repair hypothesis**; it does not run a terminal, authorize a repair, or replace health verification. This is deliberately a small extension point for a future bounded terminal investigator.

Start an OpenAI-compatible local server separately, then configure:

```bash
export SWARM_LLM_BASE_URL=http://127.0.0.1:8080/v1
export SWARM_LLM_MODEL=tmax-local
uv run swarm demo
```

Use the exact model name your server exposes. Only loopback model endpoints are accepted. The adapter sends bounded, allowlisted evidence fields, validates JSON and cited evidence IDs, and falls back explicitly to the deterministic explanation on timeout or malformed output. In local rehearsal only A is configured for model explanation; real nodes can each run their own server.

No model weights are bundled or downloaded, no OpenAI API call is made, and no subscription is required to run the demo. TMax checkpoint/runtime compatibility and latency still need to be tested on your Macs.

## What is real, and what is scoped

| Implemented | Deliberate scope / future adapter |
|---|---|
| Actual cache failure and successful functional operation | A purpose-built demo application |
| External file-change observation and recovery association | One watched cache; polling rather than whole-machine monitoring |
| Durable local SQLite evidence and delivery outbox | Production retention and telemetry budgets remain future work |
| Typed recipes and compatibility checks | One preinstalled `quarantine_demo_cache` action |
| One canary followed by verified propagation | Static trusted-team policy |
| Three active probes, failed-action rollback and rollout pause | Short verification window, not a claim of permanent resolution |
| Persistent registry, HTTP transport and actual dashboard state | Central coordinator; no peer-to-peer protocol |
| Local model explanation adapter | No trained model, terminal tool loop or federated learning yet |
| Native processes on macOS/Linux | Windows Event Log/WER and Nexthink adapters are not implemented |

Raw application logs and cache contents stay at the endpoint. The registry receives signatures, environment metadata, local evidence references, hypotheses and outcomes. Evidence references are audit pointers, not independently authenticated proof.

## Repository map

- `src/swarm/models.py` — versioned wire contracts and deterministic issue identity.
- `src/swarm/agent.py` — endpoint observation, candidate discovery, execution and delivery.
- `src/swarm/journal.py` — local evidence, durable attempt records and outbox.
- `src/swarm/repairs.py` — bounded cache operations and rollback.
- `src/swarm/registry.py` — persistent fleet state and rollout policy.
- `src/swarm/coordinator.py` — HTTP API and dashboard hosting.
- `src/swarm/demo_app.py` — intentionally breakable app and functional check.
- `src/swarm/investigator.py` — optional local-model explanation.
- `src/swarm/cli.py` — native launcher, separate-node mode and demo commands.
- `src/swarm/web/` — dependency-free dashboard.
- `tests/` — policy, endpoint, filesystem, model-adapter and process integration tests.

See [architecture](docs/architecture.md), [demo runbook](docs/demo-runbook.md), and [next steps](docs/roadmap.md).
