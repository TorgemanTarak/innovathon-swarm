# Getting started

[Documentation index](README.md) · [Configuration](configuration.md) · [Operations](operations.md)

This guide gets a teammate from checkout to a complete repair flow. SWARM runs a
coordinator, an endpoint agent and a deliberately breakable application. The
local rehearsal launches several independent processes on one machine; it is
not a set of virtual machines or evidence of deployment on several physical Macs.

## Prerequisites

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- macOS or Linux for the current native process and filesystem implementation.
- Free TCP ports 8765 and 8771–8774 for the four-endpoint rehearsal.

The default application needs no Docker, sudo, Node build, cloud account or model.
Node 22+ is useful for JavaScript tests; Chromium/Playwright is an optional testing
extra. See [development and testing](development.md). Actual teammate Macs and
their network still need validation; Linux process tests do not establish that.

## Run a local fleet

From a terminal:

```bash
git clone https://github.com/TorgemanTarak/innovathon-swarm.git
cd innovathon-swarm
uv sync --frozen --extra dev
uv run swarm demo --include-incompatible
```

Leave this terminal running. The supervisor starts the coordinator, then an app
and agent for each endpoint. All child processes use the same Python environment.

| Component | Default address | Purpose |
|---|---|---|
| Coordinator/dashboard | `http://127.0.0.1:8765` | Real fleet state and demo controls |
| Mac A/B/C apps | `http://127.0.0.1:8771` through `:8773` | Compatible app version `1.0.0` |
| Mac D app | `http://127.0.0.1:8774` | Incompatible app version `2.0.0` |
| Topology lab | `http://127.0.0.1:8765/mock` | Separate, browser-only synthetic graph |
| API reference | `http://127.0.0.1:8765/docs` | Generated OpenAPI interface |

The labels “Mac A” through “Mac D” identify rehearsal endpoints; each reports the
actual host platform. If `SWARM_TOKEN` is set in your shell, the children inherit
it and the dashboard asks for that token. An unset token permits a token-free
loopback rehearsal. No local model is required even if the optional adapter is present.

Wait until the dashboard shows four online, healthy endpoints. In another
terminal, from the same checkout, verify the expected registrations:

```bash
uv run swarm status --expect-agents mac-a mac-b mac-c mac-d
```

With authentication enabled, this second terminal needs the same `SWARM_TOKEN`.
The command prints JSON and exits unsuccessfully for missing/offline expected
IDs. It checks agent connectivity, not whether every app is healthy.

## Exercise a real repair

1. Open the app on port 8771 and use **Load workspace**. It should succeed.
2. In the coordinator dashboard, press **Inject cache fault**. Wait for the four
   incidents to appear; loading a workspace now fails because its cache is corrupt.
3. In the second terminal, repair only A's dedicated cache:

   ```bash
   uv run swarm repair --data-dir .swarm/local/mac-a
   ```

4. A observes the external file change and three successful functional probes.
   Its candidate appears in **Repair evidence**.
5. B or C receives the canary assignment. After successful verification, the
   remaining compatible endpoint receives a follower assignment.
6. Confirm three healthy apps and two verified replays. D remains unhealthy:
   its app version is incompatible with the recipe.
7. Expand the replay results to inspect verification evidence. Use **Reset demo**
   and wait for all four apps to become healthy before rehearsing again.

The dashboard's **Simulate human repair** button is an alternative to the terminal
repair. It queues a typed command to quarantine the selected endpoint's cache;
the normal observer still has to detect recovery. The terminal command performs
the local filesystem repair directly. Both operate on SWARM-owned sample data.

The application `/health` route only proves the process is alive. Recovery is
verified using `/work`, which reads the actual cache. The learning mechanism
recognizes this supported repair pattern; it does not infer arbitrary system fixes.
See the [repair lifecycle](repair-lifecycle.md) and [demo runbook](demo-runbook.md)
for the paused and failed-canary flows.

## Run on teammates' Macs

Use the same checkout revision and dependencies on every machine. Pick one Mac
as coordinator and ensure teammates can reach its LAN address. This is a
trusted-team setup; review the [security boundaries](security.md) before exposing
it beyond the local machine.

On the coordinator, generate a shared token without embedding it in source:

```bash
export SWARM_TOKEN="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(24))')"
uv run swarm coordinator --host 0.0.0.0 --port 8765 --demo-mode
```

Distribute that value privately. On each endpoint, set `SWARM_TOKEN` to the same
value, then run the following with a unique ID and label. Replace
`COORDINATOR_LAN_IP` with the coordinator's reachable address:

```bash
uv run swarm node \
  --id teammate-a --label "Teammate A" \
  --coordinator http://COORDINATOR_LAN_IP:8765 \
  --data-dir "$HOME/.swarm-demo/teammate-a" \
  --app-port 8771
```

`node` starts the local app and its agent. Every new data directory must be empty;
SWARM creates its ownership marker. Reuse the same ID when restarting that
directory. A different ID needs a different directory. On separate Macs the app
port can be the same; on one machine each app needs a distinct port.

Open the coordinator dashboard using its LAN address and enter the shared token.
Open each sample app on its own Mac at `http://127.0.0.1:8771`: a localhost link
opened on another laptop addresses that other laptop. Apps listen only on loopback;
agents initiate outbound requests to the coordinator. There is no LAN discovery
or live peer-to-peer routing.

From any participating shell with the shared token, check your actual IDs:

```bash
uv run swarm status \
  --coordinator http://COORDINATOR_LAN_IP:8765 \
  --expect-agents teammate-a teammate-b teammate-c
```

For an incompatible endpoint, launch one node with `--app-version 2.0.0`.
Wi-Fi isolation or firewalls can prevent connections even with correct commands;
use [operations troubleshooting](operations.md) if registrations do not appear.

## Stop and resume

Press Ctrl+C in the `demo` or `node` terminal to stop its children. Data persists.
Restart with the same arguments to reuse it; restarting does not reset incidents
or learned recipes. A standalone coordinator must be stopped separately.
Use [configuration](configuration.md) for alternate ports/directories, and
[operations](operations.md) for coordinated resets, backups and upgrades.
