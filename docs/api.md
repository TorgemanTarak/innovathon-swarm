# HTTP API reference

[Documentation home](README.md) · [Coordinator module](modules/coordinator.md) · [Data and state](data-and-state.md)

The coordinator normally listens at `http://127.0.0.1:8765`. Its generated
[`/docs`](http://127.0.0.1:8765/docs) and
[`/openapi.json`](http://127.0.0.1:8765/openapi.json) describe request models and
typed responses for the running version. Source of truth:
[`coordinator.py`](../src/swarm/coordinator.py) and
[`models.py`](../src/swarm/models.py). Snapshot responses are assembled
dictionaries; the field guide below covers details OpenAPI cannot infer.

## Authentication and responses

When the coordinator has a token, every `/api/*` request must include
`X-Swarm-Token`. Without a configured token, those endpoints accept requests
without authentication. The token grants membership in one trusted demo fleet;
it is not a per-device identity or a role/permission system.

Successful API requests return HTTP `200`, including registrations and queued
commands. Most writes return `{"ok":true}`. Failures include:

| Status | Meaning |
| --- | --- |
| `401` | Configured token missing or incorrect |
| `403` | Demo control called without coordinator demo mode |
| `404` | Unknown endpoint; register first, or correct a requested target |
| `409` | Conflicting identity/result, missing source incident, unfinished reset, invalidated assignment or another policy conflict |
| `422` | Request validation failure or invalid proof/count/target requirements |

Registry/policy errors return `{"detail":"..."}`. FastAPI validation failures
return a structured `detail` list. Do not parse a human-readable error string as
a durable protocol enum. There is no API version negotiation or rate-limit
contract. Upgrade endpoints and coordinator together.

## Public resources

| Method and path | Response |
| --- | --- |
| `GET /` | Live dashboard HTML, or a JSON fallback if assets are absent |
| `GET /mock` | Browser-only synthetic topology lab; no fleet data or commands |
| `GET /static/{path}` | Packaged JS/CSS/HTML assets |
| `GET /health` | `{"ok":true,"service":"swarm-coordinator"}`; process liveness only |
| `GET /docs`, `GET /redoc`, `GET /openapi.json` | FastAPI API documentation and schema |

## Endpoint protocol

All routes in this table require the configured token.

| Method and path | Request | Successful response / effect |
| --- | --- | --- |
| `POST /api/agents/register` | `Registration` | `{"ok":true}`; insert/update endpoint metadata |
| `POST /api/agents/{agent_id}/heartbeat` | `Heartbeat` | `{"ok":true}`; update functional health and last-seen time |
| `POST /api/incidents` | `IssueReport` | `{"ok":true}`; record an incident and make it current for its endpoint |
| `POST /api/candidates` | `Candidate` | A `Recipe`; create or return the cohort's existing recipe |
| `GET /api/agents/{agent_id}/work` | None | `Work`: optional `assignment`, `commands` array and global `paused` flag |
| `POST /api/outcomes` | `Outcome` | `{"ok":true}`; record one terminal result and update rollout state |
| `POST /api/agents/{agent_id}/commands/ack` | `CommandAck` | `{"ok":true}`; stop redelivering that command |

**A work poll is not a passive diagnostic read.** It can reserve a canary,
allocate a follower assignment or return a command to execute. Use the dashboard
or `swarm status` for inspection; do not poll another endpoint's work queue as a
monitoring check.

Model relationships:

- `Registration` binds an `agent_id` to label, environment, native/simulated mode
  and optional app URL. `Heartbeat.health` is `healthy`, `unhealthy` or `unknown`.
- `IssueReport` supplies `incident_id`, endpoint, `issue_key`, summary,
  environment and local evidence references.
- `Candidate` references that incident and its source, supplies a recognized
  action, explanation, verification count and optional bounded origin evidence.
- `Assignment` embeds a recipe snapshot and identifies the recipient, incident
  and canary/follower role. There is no arbitrary command field.
- `Outcome` must match its assignment's endpoint, recipe and incident. Status is
  `verified`, `failed`, `rejected` or `inconclusive`.
- `CommandAck` identifies a delivered command and records success/detail.

See the [repair lifecycle](repair-lifecycle.md) for ordering and retries. Reusing
the same incident/candidate/outcome/acknowledgement identity with conflicting
content is rejected. A changed environment or reset can invalidate old work.

## Dashboard and operator controls

| Method and path | Request | Successful response / effect |
| --- | --- | --- |
| `GET /api/state` | None | Fleet snapshot described below |
| `POST /api/control/pause` | `{"paused":true}` or `false` | `{"paused":...}`; hold/resume new assignment delivery |
| `POST /api/demo/fault` | `TargetRequest` | Queue `inject_fault` on selected endpoints |
| `POST /api/demo/repair` | `TargetRequest` | Queue `manual_repair` on exactly one endpoint |
| `POST /api/demo/fail-verification` | `TargetRequest` | Arm the selected endpoints' next repair verification failure |
| `POST /api/demo/reset` | No request model required | Clear shared repair history and queue resets on registered endpoints |

The four `/api/demo/*` controls require `demo_mode=True`. `TargetRequest` is
`{"agent_ids":["mac-a"]}`; an omitted/empty `agent_ids` array selects all
registered endpoints. The manual repair control still requires exactly one
resolved target. Target validation is atomic: an unknown target queues nothing.
A `200` acknowledges queuing, not completion; inspect subsequent commands,
events and functional health. Pause does not cancel running repairs, and resume
does not revive paused recipes.

### Snapshot fields

| Field | Contents |
| --- | --- |
| `agents` | Registration metadata plus `health`, `detail`, `issue_key`, `incident_id`, `last_seen` and computed `online`; sorted by ID |
| `recipes` | Newest first; each recipe includes its candidate, state and counters |
| `eligibility_recipe_id` | Newest recipe's ID, or `null` when no recipe exists |
| `recipes[0].eligibility` | One decision per endpoint: `agent_id`, `status`, `reason`, `reason_detail`, `role`; older recipes omit this key |
| `outcomes` | Newest first; adds `role` from the stored assignment to each outcome |
| `events` | Up to 200 recent coordinator events, newest first |
| `paused`, `demo_mode` | Global dispatch flag and whether demo routes are enabled |

`online` means last-seen time is within 30 seconds, independently of app health.
Initial registration (or registration with a changed environment) initializes
freshness; normal heartbeats refresh it. A newly registered endpoint can therefore
be online with unknown app health. Eligibility is a preview, not execution
authorization. Historical roles come from assignments, not today's selected canary.

**`GET /api/state` can change state:** it processes expired assignments, recording
inconclusive outcomes and pausing their recipes. It does not allocate work.
There is no pagination; total recipe/outcome history can grow.

## Inspection examples

With the local coordinator running, these retrieve liveness and schema:

```bash
curl --fail --silent --show-error http://127.0.0.1:8765/health
curl --fail --silent --show-error http://127.0.0.1:8765/openapi.json
```

For fleet inspection, use the CLI so the token comes from your existing
`SWARM_TOKEN` environment variable instead of putting its value in a command:

```bash
uv run swarm status --coordinator http://127.0.0.1:8765 \
  --expect-agents mac-a mac-b mac-c mac-d
```

This reads `/api/state` and checks membership/connectivity; it does not require
the applications to be healthy. It can trigger the expiry processing above.

## Demo application API is separate

Each app has its own port (normally 8771 onward in local rehearsal), no
coordinator token authentication, and only `/`, `/health` and `/work` application
routes. `/health` is liveness. `/work` exercises the actual cache, regenerates it
when absent and writes a local log. It returns `200` on success or `503` with
`CACHE_INVALID`, `VERIFICATION_FAILED` or `STORAGE_UNAVAILABLE` on failure. The
agent maps unsupported app codes to bounded `APPLICATION_ERROR` evidence.
Do not treat repeated `/work` requests as passive reads of the filesystem.
