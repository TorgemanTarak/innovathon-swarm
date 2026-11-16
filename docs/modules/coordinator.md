# Coordinator, policy and contracts

[Documentation home](../README.md) · [Architecture](../architecture.md) · [HTTP API](../api.md)

The coordinator owns shared knowledge and permission to dispatch work. It does
not repair files itself. Endpoints poll for assignments, enforce local
preconditions, execute a preinstalled action, and report the result.

## `models.py`: the shared vocabulary

Source: [`src/swarm/models.py`](../../src/swarm/models.py).

Every transport model inherits `Contract`, whose Pydantic configuration rejects
extra fields. This catches misspelled fields and prevents a recipe from acquiring
an executable command or arbitrary path. Some fields have literal values,
length/count limits and cross-field validators; this is not a blanket guarantee
that every field is strictly typed or that a peer's evidence is truthful.

| Model | Responsibility |
| --- | --- |
| `Environment` | Platform, fixed `workspace-demo` app ID, app version and agent version |
| `Registration`, `Heartbeat` | Endpoint identity/metadata and its latest functional health |
| `IssueReport` | A specific endpoint incident with an issue signature and local evidence IDs |
| `Candidate` | An observed recovery associated with the allowlisted `quarantine_demo_cache` action |
| `Recipe` | Shared candidate plus rollout status, counters and selected canary |
| `Assignment` | One recipe snapshot, endpoint, incident and immutable canary/follower role |
| `Outcome` | One assignment's terminal result and bounded executor observations |
| `DemoCommand`, `CommandAck`, `Work` | Control delivery, acknowledgement and the endpoint poll response |
| `FleetEvent` | Human-readable coordinator activity |
| `TargetRequest`, `PauseRequest` | Dashboard control payloads |

`issue_key()` hashes the canonical schema/app/operation/error signature. It does
not include app version or platform: those belong to compatibility metadata.
`new_id()` supplies UUIDs; `now_iso()` supplies UTC timestamps.

`EvidenceSummary` carries only defined kinds, UTC-normalized observation times,
bounded cache fingerprints, fixed probe/error codes and paired check counts.
Candidates allow up to three origin summaries and outcomes up to eight executor
summaries. The journal projection removes unsupported local fields. IDs identify
local records; they do not authenticate causality.

## `registry.py`: state and rollout policy

Source: [`src/swarm/registry.py`](../../src/swarm/registry.py).

`Registry(path, assignment_timeout=120.0)` creates its schema and indexes on
startup. Its methods use short SQLite connections, a 10-second busy timeout and
`BEGIN IMMEDIATE` transactions. The write lock makes selecting a canary and
recording its assignment one atomic decision, including when two agents poll
at once. This design favors understandable demo behavior over horizontal
coordinator scaling. See [data and state](../data-and-state.md).

| Entry point | Behavior and important boundary |
| --- | --- |
| `register()` | Insert/update metadata; retain runtime state when the environment is unchanged. An environment change resets current health/incident state and concludes pending assignments as inconclusive. |
| `heartbeat()` | Store functional health and refresh coordinator receipt time; emit an event on health changes. |
| `report_incident()` | Require registration and a completed reset; reject conflicting ID reuse or environment mismatch; supersede pending work for an older incident. |
| `propose()` | Validate the recorded source incident, registration, recent healthy source and nonempty evidence references; create or reuse a cohort's recipe. |
| `work()` | Process expiry, prioritize commands, redeliver eligible pending work, or atomically allocate one fresh assignment. |
| `record_outcome()` | Commit any timeout decisions first, then validate and store the reported result in a second transaction. |
| `pause()`, `enqueue()`, `acknowledge()`, `reset()` | Persist rollout controls, deliver demo commands and enforce reset barriers. |
| `snapshot()` | Return history and a current eligibility preview; process assignment expiry before constructing the response. |

A cohort is `(issue_key, action, app_id, app_version, platform)`. A second
candidate for that cohort is retained as a candidate record but returns the
existing recipe; it does not replace its source candidate, create a new revision
or revive a paused recipe. Full environment equality is required when linking an
incident/candidate to registration. Replay compatibility compares app ID, app
version and platform; it does not currently compare `agent_version`.

## Eligibility and dispatch

The pure `_eligibility()` function explains a device's decision for a recipe.
Both dispatch and the latest-recipe dashboard preview call it. Its result has
`agent_id`, `status`, `reason`, `reason_detail` and optional `role`.

The main gates are:

1. A recorded outcome for this recipe and current incident is `completed`.
2. Global pause, unfinished reset or a pending command holds delivery.
3. Pending work is redelivered only while its recipe permits delivery. Another
   pending assignment blocks fresh work.
4. Fresh work requires a recent heartbeat, an unhealthy functional probe and a
   matching current incident.
5. The originating incident cannot validate itself. Even on a later incident,
   the source cannot act as the independent canary; it may reuse the recipe once
   another endpoint has validated it.
6. Environment mismatch excludes the endpoint. A paused recipe or active canary
   makes compatible peers wait. A prior attempt cannot be repeated for the same
   endpoint/incident/recipe tuple.
7. A `candidate` recipe admits one canary; a `validated` recipe admits followers.

Pending redelivery intentionally precedes fresh health/compatibility gates: an
endpoint may already have recovered and need to resend its durable outcome.
Local execution still rechecks preconditions before any new mutation.

`work()` keeps recipe-independent idle checks cheap and uses indexes for pending
commands/assignments. Eligible dispatch scans recipe history oldest first. The
dashboard previews only the newest recipe, avoiding a preview for every
recipe/device pair; that is not a promise of bounded total history or snapshot
size. [Scaling](../scaling.md) explains the remaining limits.

## Results, expiry and control semantics

`_finish()` checks assignment identity and incident/recipe ownership, enforces a
single terminal result, and requires at least three successful functional probes
for `verified`. Exact duplicate results are accepted without increasing counts.
Conflicting results are rejected. Candidate/outcome duplicates are compared
after model normalization, including defaults for older evidence-free records.

A verified result increments successes and validates an unpaused recipe. Every
other terminal status—`failed`, `rejected` or `inconclusive`—increments failures
and pauses it. A later success cannot unpause it. Expired pending assignments
receive an inconclusive outcome; expiry is checked during work polls, snapshots
and outcome ingestion, not by a dedicated scheduler.

Global pause holds assignment delivery but commands remain deliverable, and an
already running repair may finish. Reset clears shared repair history, retains
registrations and queues endpoint resets. Until an endpoint successfully
acknowledges reset, new incident/candidate ingestion and repairs are blocked.
Neither global resume nor a duplicate/new candidate bypasses a failed recipe.

## `coordinator.py`: HTTP boundary

Source: [`src/swarm/coordinator.py`](../../src/swarm/coordinator.py).

`create_app(db_path, token=None, demo_mode=False)` constructs the registry and
FastAPI routes. Route functions validate models, invoke the registry and return
JSON; they do not embed policy or endpoint filesystem operations. All `/api/*`
routes share optional token authentication, and `/api/demo/*` additionally
requires demo mode. `RegistryError` becomes a JSON `detail` response with its
specified status. Public routes host the live dashboard, static assets, mock
topology, liveness and generated API documentation.

Useful regression references: [coordinator behavior](../../tests/test_coordinator.py),
[eligibility](../../tests/test_eligibility.py),
[indexed polling](../../tests/test_registry_scaling.py) and
[evidence contracts](../../tests/test_evidence.py).
