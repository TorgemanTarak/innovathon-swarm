# Endpoint modules

An endpoint consists of an application process and an agent process sharing one
marked data directory. The application owns normal cache regeneration; the agent
observes the application, records evidence, and executes the one preinstalled
repair when assigned. The coordinator never opens the endpoint's files.

Read the [architecture](../architecture.md) first. The
[repair lifecycle](../repair-lifecycle.md) follows these modules through one
incident, while [data and state](../data-and-state.md) explains persistence.

## `agent.py`: observation, learning and execution

Source: [`src/swarm/agent.py`](../../src/swarm/agent.py).

`AgentConfig` supplies identity, label, data directory, coordinator/app URLs,
optional shared token, app version, polling interval, native/simulated mode and
optional local-model settings. `EndpointAgent` builds its environment from the
actual host platform, initializes the marked directory, opens `journal.sqlite`
and binds that journal to its agent ID. Changing the ID requires a different
directory. A changed environment clears the old local incident association.

`run()` repeatedly calls `tick()` and sleeps for the configured interval. A lock
serializes ticks; this is not a scheduler for parallel repairs. Each tick:

1. `_work()` registers if necessary and polls the endpoint's work URL.
2. `_command()` handles returned demo commands before repair execution.
3. `_execute()` processes an assignment only when the work response is not paused.
4. Fingerprints before and after `_probe()` detect cache changes. The probe calls
   the app's `/work`, not its liveness endpoint.
5. A recognized `CACHE_INVALID` failure opens an incident. Recovery following an
   observed change increments a consecutive-success counter and may call `_learn()`.
6. A fresh heartbeat is sent before `_flush()` delivers queued incidents,
   candidates, command acknowledgements and outcomes. The ordering allows the
   registry to check that a candidate's source is currently healthy.

If the coordinator cannot be reached, `_work()` returns paused work and marks the
registration stale. Local observation continues; reports remain queued for a
later successful registration and delivery. A coordinator outage does not grant
permission for a new repair. HTTP clients use explicit local/coordinator URLs
without inherited proxy settings.

`_probe()` distinguishes success, a failed operation, and an unreachable or
unusable response. Only `CACHE_INVALID` starts this repair scenario. Other app
errors become the bounded `APPLICATION_ERROR` code; unreachable probes report
`unknown` health. An online agent can therefore report an unhealthy app.

`_learn()` needs an unresolved incident, an external-change observation, at least
three consecutive successful probes, and no already-enqueued candidate for that
incident. It proposes the fixed `quarantine_demo_cache` action. It does **not**
infer an arbitrary command or establish what a human actually did. Three
successes without an observed change resolve local observation without inventing
a candidate. Expected regeneration after a missing-cache probe is not counted as
a separate external change.

`_execute()` durably claims the assignment before mutation. It checks endpoint and
incident identity, issue signature, platform/app/version, recipe status, invalid
cache state, a reproduced `CACHE_INVALID` probe and an unchanged fingerprint.
It then quarantines the cache and attempts three functional probes. Success
produces `verified`; failed verification attempts guarded rollback. Rejection
before mutation produces `rejected`; interruption produces `inconclusive`.
Every finished attempt queues its outcome. Existing finished attempts resend
that outcome instead of repeating the filesystem action.

The learning baseline is refreshed after agent repairs, including interrupted
attempt recovery, so the agent does not learn its own changes as human repairs.
Startup converts unfinished attempts into inconclusive outcomes even if the
coordinator never redelivers them. This favors inspection over blind retry.

Relevant tests: [`test_agent.py`](../../tests/test_agent.py) covers observation
races, pause, incompatible assignments, actual rollback, restart, identity
binding and report retries; [`test_connectivity.py`](../../tests/test_connectivity.py)
exercises reconnect behavior with processes.

## `journal.py`: local durability and evidence projection

Source: [`src/swarm/journal.py`](../../src/swarm/journal.py).

`Journal` uses SQLite in WAL mode. Its five tables separate observations, runtime
state, repair attempts, completed command acknowledgements and the delivery
outbox. `observe()` stores a timestamped local record and returns its evidence ID;
`get()`/`set()` maintain the incident, fingerprints, counters and related state.

Important operations are:

| Operation | Why it exists |
| --- | --- |
| `bind_agent()` | Keeps endpoint ownership attached to a directory, including after reset. |
| `enqueue()` / `pending()` / `delivered()` | Retains uniquely identified reports and delivers them in insertion order. |
| `claim_attempt()` | Inserts an `executing` record once per assignment before touching files. |
| `finish_attempt()` | Saves the terminal outcome and its outbox entry in one SQLite transaction. |
| `unfinished_attempts()` | Finds ambiguous executions after restart. |
| `finish_command()` / `command()` | Reuses a completed command's acknowledgement on ordinary redelivery. |
| `clear_runtime()` | Clears transient learning state and pending deliveries, while preserving identity, observations, attempt deduplication and command history. |

The agent's `_flush()` removes a report after success. HTTP 400, 404, 409 and 422
are treated as terminal delivery rejection: a local `delivery_rejected` record
is written and the item is removed. This includes stale IDs after a coordinator
reset. Other HTTP failures leave the report pending. These are explicit delivery
rules, not a general exactly-once guarantee across files, SQLite and HTTP.

`evidence()` reads local records. `evidence_summaries()` projects only supported
fields into strict `EvidenceSummary` contracts: IDs, UTC observation times,
fingerprints, fixed codes and check counts. Unsupported or malformed historical
records are omitted from summaries, not replaced with invented evidence. Raw
application logs and cache bytes are not uploaded by this projection. Limits
apply per message; the database has no automatic retention policy.

Relevant tests: [`test_evidence.py`](../../tests/test_evidence.py) checks bounded
projection and legacy handling; durability tests live in `test_agent.py`.

## `repairs.py`: the filesystem boundary

Source: [`src/swarm/repairs.py`](../../src/swarm/repairs.py).

This module contains small, synchronous operations on known resources. Recipes
provide neither shell commands nor arbitrary filesystem paths.

- `initialize_root()` accepts a new empty directory or a valid existing SWARM
  directory. It creates `.swarm-demo-root`, `cache/`, `quarantine/` and `logs/`.
- `validate_root()` and `scoped_path()` enforce the marker, containment and
  rejection of symlink resources. Cache reads require a regular file, use
  `O_NOFOLLOW` where available and cap input at 1 MiB.
- `ensure_cache()` validates schema 1 and regenerates the default cache only when
  the cache is missing. Corrupt existing content is not silently replaced.
- `fingerprint()` returns SHA-256, `missing` or `oversized`; `cache_is_invalid()`
  tests whether the cache fails the supported schema/size checks.
- `quarantine_cache()` atomically moves `cache/workspace.json` to a new
  `quarantine/<operation-id>.json`. It rejects unsafe IDs and existing destinations.
- `rollback_cache()` restores that backup only if the current cache is absent or
  equals the known regenerated default. A different current cache is preserved,
  together with the backup, for inspection.
- `inject_fault()`, `set_verification_fault()` and `reset_demo()` implement the
  controlled demonstration. Reset restores known app resources and preserves the
  journal, quarantine and unrelated files.

These checks constrain accidental operations in a trusted demo. They are not an
OS sandbox or complete protection against a hostile privileged local process.
Tests are in [`test_repairs.py`](../../tests/test_repairs.py); concurrent-edit
rollback tests are in `test_agent.py`.

## `demo_app.py`: a functional workload

Source: [`src/swarm/demo_app.py`](../../src/swarm/demo_app.py).

`create_app()` creates a FastAPI application over the endpoint's data directory.
`GET /health` reports process liveness. `GET /work` reads the actual cache and
returns projects on success; corrupt caches produce HTTP 503 with `CACHE_INVALID`.
The `.fail-verification` marker produces a real failed operation with
`VERIFICATION_FAILED`. Storage failures use `STORAGE_UNAVAILABLE`, which the
agent maps to its generic application-error category.

The `/` page invokes `/work` on demand and, by default, every three seconds.
There is no app HTTP mutation endpoint: corruption and repair happen through
local file operations. `_append_log()` writes bounded rotating local JSONL logs
to `logs/app.jsonl` and `logs/app.jsonl.1`. Browser requests also exercise the
workload, so they can regenerate a missing cache.

[`test_demo_app.py`](../../tests/test_demo_app.py) verifies real corruption and
recovery, schema failures, the verification fault, escaped UI content and log-path
guards.

## `investigator.py`: optional narration

Source: [`src/swarm/investigator.py`](../../src/swarm/investigator.py).

`explain_candidate()` is called only after deterministic learning gates pass.
It returns explanatory text and either `deterministic` or `local_model` provenance.
`_sanitize_evidence()` limits the input to 20 records with allowlisted IDs, kinds,
codes and success counts. `_local_chat_url()` accepts only loopback HTTP(S) base
URLs, optionally ending in `/v1`; credentials, other paths, queries and fragments
are rejected.

`_call_model()` requests JSON from `/v1/chat/completions`, disables redirects and
environment proxies, limits the response to 65,536 bytes, and runs within an
eight-second total deadline. `ModelExplanation` fixes the action identifier,
bounds the explanation and requires citations to supplied evidence IDs.
Unavailable or invalid responses produce explicit deterministic fallback text.

Narration has no mutation, shell or rollout authority. No model is bundled or
downloaded, and model-specific hardware/performance validation remains separate.
[`test_investigator.py`](../../tests/test_investigator.py) covers sanitation,
unsupported endpoints, invalid citations, oversized responses and timeouts.

Continue with the [API reference](../api.md) or
[development guide](../development.md) when changing these boundaries.
