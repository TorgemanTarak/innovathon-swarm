# Operations and troubleshooting

[Documentation index](README.md) · [Getting started](getting-started.md) · [Configuration](configuration.md)

Repairs act only on known resources in marked SWARM directories. Do not repoint
the demo at real application data or edit SQLite rows to force a rollout.

## Confirm the system is ready

1. Verify that all expected IDs appear and are online:

   ```bash
   uv run swarm status --expect-agents mac-a mac-b mac-c mac-d
   ```

2. Check app health separately in the dashboard. An online agent may be reporting
   a broken app; an offline agent may still have a previously healthy report.
3. Open the sample app and use **Load workspace**. `/health` is liveness only;
   `/work` exercises cache parsing and is the functional recovery signal.
4. Inspect **Repair evidence** for current eligibility and recorded outcomes.
   Only the newest recipe has a current fleet eligibility preview; older recipes
   retain their historical evidence.

For a LAN fleet, add `--coordinator` and set `SWARM_TOKEN` in the command shell.
Online means last-seen time is within 30 seconds: registration initializes it and
heartbeats refresh it. It does not mean a direct peer connection or healthy app.

## Pause, reset and shutdown have different effects

| Operation | Effect | What it preserves |
|---|---|---|
| **Pause rollout** | Stops delivery of repair assignments, including pending work not yet picked up. | Observation, reporting and demo-command processing continue. An already executing action can finish. |
| **Resume rollout** | Removes the global pause. | A failed/expired recipe remains paused; global resume does not rehabilitate it. |
| Dashboard **Reset demo** | Clears coordinator incidents, recipes, candidates, assignments, outcomes, old commands and events; unpauses globally; queues a reset for every registered endpoint. | Registrations and endpoint-local observations/quarantine remain. |
| `swarm reset --data-dir ...` | Restores app cache and clears the forced-verification marker locally. A running agent can observe this as an external change. | Coordinator history and journal runtime state remain. |
| Ctrl+C on `demo` / `node` | Stops that supervisor's child processes. | Databases, cache, quarantine and logs remain on disk. |

A coordinated reset initially marks registered devices unknown. Agents restore
their app resources, clear learning/runtime delivery state and acknowledge the
reset. Pre-reset incident/candidate delivery is rejected until successful reset
acknowledgement. An offline endpoint cannot complete its reset; a failed reset
continues blocking new evidence. Resolve the local error and request another
coordinated reset. See [repair lifecycle](repair-lifecycle.md).

The supervisor closes all children if one dies. Normal shutdown sends termination
signals, waits against a shared five-second deadline, then kills remaining
children. Standalone processes need their own shutdown. Prefer pausing and waiting
for active actions before stopping processes.

## Restart and upgrade

Restart with the same endpoint IDs, coordinator DB and endpoint data directories.
Agents re-register and continue observing; durable queued reports can retry.
Completed attempt outcomes are resent instead of repeating their repair. An
attempt interrupted after being claimed produces an inconclusive outcome on
recovery; it is not blindly re-executed. Inspect that endpoint's cache and
quarantine before resetting it. Assignment deadlines continue to matter across
coordinator restarts, and an expired canary can pause the recipe.

Upgrade coordinator and agents together. Existing stored history is supported,
including old records without structured summaries, but mixed-version wire
negotiation is not implemented. Stop the processes, preserve the state, update the
checkout, run `uv sync --frozen --extra dev`, and restart using the same commands.
Older evidence summaries may display “unavailable”; that is preferable to
inventing historical observations.

## Files and logs

For `swarm demo --include-incompatible`, the default layout includes:

```text
.swarm/local/
├── coordinator.sqlite
├── logs/
│   ├── coordinator.log
│   ├── mac-a-app.log
│   └── mac-a-agent.log        # analogous files for B/C/D
└── mac-a/                    # analogous directories for B/C/D
    ├── .swarm-demo-root
    ├── journal.sqlite
    ├── cache/workspace.json
    ├── quarantine/<operation-id>.json
    └── logs/app.jsonl
```

`swarm node` puts supervisor output under its endpoint directory's
`process-logs/app.log` and `process-logs/agent.log`. The app's structured operation
log is still `logs/app.jsonl`. CLI-supervised Uvicorn uses warning-level logging
with access logs disabled, so a quiet process log is normal.

```bash
tail -n 80 .swarm/local/logs/mac-a-agent.log
tail -n 30 .swarm/local/mac-a/logs/app.jsonl
```

The app rotates its JSONL log at approximately 1 MiB and keeps one previous file
as `app.jsonl.1`. Supervisor stdout/stderr logs append without rotation. SQLite
history, quarantine files and supervisor logs have no automatic overall retention
limit. Cache reads are capped at 1 MiB; bounded individual evidence reports do not
bound total storage. More detail: [data and state](data-and-state.md).

## Preserve or clear a rehearsal

For a consistent backup, pause rollout, allow attempts to finish and
stop all participating agents/apps and the coordinator. Copy the whole coordinator
and endpoint data roots, including any SQLite sidecar files. Endpoint journals
use WAL; copying only `journal.sqlite` while processes run can omit committed
state. A live SQLite backup requires a proper SQLite backup mechanism, and still
does not atomically capture every endpoint and the coordinator together.

Use a new empty `--data-dir` and a separate coordinator DB/port for an independent
rehearsal instead of deleting audit history. A new local directory with the same
agent ID against an old coordinator is not a full fleet reset. Do not manually
remove the ownership marker to bypass an identity/path error. Restore coordinated
backups as a set; arbitrary old/new DB combinations can leave stale assignments.

## Troubleshooting

| Symptom | Check and next step |
|---|---|
| Supervisor exits or a port is occupied | Read the named process log. Stop the previous instance or choose a separate coordinator port, consecutive app ports and data root. |
| No endpoints appear | Check coordinator URL, shared token and agent log; verify LAN reachability. Different coordinators are separate fleets. |
| Agent is offline | Check its process and coordinator connectivity. `status --expect-agents` fails for missing/offline IDs even when the dashboard itself loads. |
| API returns 401 | The shell and dashboard must use the coordinator's token. Loading HTML alone does not authenticate API requests. |
| Demo controls disabled / API returns 403 | Start CLI coordinator with `--demo-mode`, or use `SWARM_DEMO_MODE=1` for the Uvicorn entry point. |
| Directory is rejected | Use a new empty or existing marked directory; keep its original endpoint ID. Inspect unexpected symlinks or permissions without bypassing the guard. |
| App health is unknown | Verify the app process and agent `--demo-url`; unknown usually means the functional probe could not reach/parse the app. |
| No candidate appears | Let the fault be observed before repair; then allow three successful agent probes after an external cache change. Only the supported cache pattern is learned. |
| Recipe remains canary | Check canary connectivity, evidence/outbox and assignment deadline. The default deadline is 120 seconds. |
| Rollback was skipped | A concurrent cache change may have made ownership uncertain. Inspect current cache and quarantined original; the executor preserves both rather than overwriting the change. |
| Reset seems incomplete | Wait for each agent's successful reset acknowledgement; reconnect offline agents and resolve local reset errors. |
| Local model falls back | Check loopback URL/model and local server; an eight-second deadline or invalid response causes deterministic fallback. |
| CI fails before any step starts | Inspect job/check annotations for runner, account or platform problems. A job that never started did not run the tests; distinguish that from a test failure. |

The synthetic `/mock` topology cannot diagnose or control the live fleet.
