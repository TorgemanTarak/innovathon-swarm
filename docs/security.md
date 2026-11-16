# Trust and execution boundaries

[Handbook](README.md) · [Architecture](architecture.md) · [Operations](operations.md)

SWARM is currently a trusted-team prototype with a controlled application repair.
The implemented guards make that scope concrete; they do not establish a
production endpoint-management security model.

## What can authorize a repair

A candidate names the preinstalled `quarantine_demo_cache` action and a compatible
environment. The coordinator checks the source incident and recovery, admits an
independent canary, and permits followers only after the canary verifies. The
receiving agent rechecks the assignment, incident, environment and app/cache state
before mutation. The recipe contains no executable script or arbitrary path.

The optional local model only narrates a previously detected hypothesis. Its
input is bounded, its endpoint is constrained to loopback, redirects are disabled,
and malformed/late responses fall back to deterministic narration. Model text
cannot add an action, change eligibility or authorize fleet execution.

Neither a nice explanation, a matching error message nor the absence of new
errors is enough to verify recovery. The agent exercises `/work` and requires
consecutive successful checks. These checks are a short observation window;
recurrence monitoring and independently authenticated evidence are not implemented.

## Filesystem scope

[`repairs.py`](../src/swarm/repairs.py) initializes a marked SWARM data directory
and restricts operations to known relative resources. It rejects unsafe roots,
path traversal and symlink resources, bounds cache reads, and validates cache
structure. Quarantine preserves the previous cache, and rollback refuses to
overwrite an unexpected concurrent cache edit.

Use new empty directories for new endpoints. Do not point SWARM at real user
application caches, employee files or arbitrary existing directories. The marker
is a containment guard, not an OS sandbox: a privileged local user can still
alter data, journals or processes. There is no remote shell or general-purpose
command executor in the project.

## Network and identity

All `/api/*` routes use `X-Swarm-Token` when a coordinator token is configured.
This shared token establishes membership in a trusted demo group; it does not
cryptographically bind a request to one device ID. A holder can impersonate
another member or request group controls. Unique IDs prevent accidental collisions
but are not credentials.

The CLI requires a token of at least sixteen characters when binding a coordinator
beyond loopback. **Direct Uvicorn startup bypasses that CLI guard.** Keep development
servers on loopback; if explicitly configuring another binding, provide and review
the token/network configuration yourself. The project does not configure TLS,
certificate distribution, per-device enrollment, roles or tenant isolation.

The health route, HTML/static assets, mock page and generated API documentation
are readable without the API token. Protected fleet data still requires it when
configured. Demo mutations require `--demo-mode` in addition to API authentication;
global pause is a separate API control and is not disabled by turning off demo mode.
Endpoints make outbound requests to the configured coordinator. The local sample
app binds to loopback and exposes no remote fault/repair control route.

The live dashboard retains its token in tab `sessionStorage`, or memory if storage
is unavailable. Avoid sharing tokens through source files, screenshots or logs.
Native app loopback URLs identify the endpoint's own host, not a remotely accessible
service. The topology lab makes no fleet API calls and has no execution authority.

## Shared information and durability

The coordinator receives registrations/environment metadata, incident summaries,
typed repair candidates, local evidence references, bounded evidence summaries and
outcomes. It does not retrieve raw cache files or application log files. Evidence
summaries allow fixed event/error/probe fields and fingerprints; free-text detail
fields elsewhere in the contracts remain bounded but are still information shared
with the group. A fingerprint can identify matching data and should not be treated
as anonymization.

Endpoint journals and app/process logs stay on the endpoint. Coordinator storage,
local journals, quarantine and logs have no application-managed encryption at rest.
Most history has no retention limit; access control, storage protection and retention
are deployment responsibilities beyond the current prototype.

Durable attempt IDs and reports prevent normal duplicate delivery from applying a
repair again. An interrupted claimed repair is reported inconclusive instead of
blindly replayed. SQLite and filesystem operations are not one distributed atomic
transaction, and the journal is not tamper-resistant. Read
[data and state](data-and-state.md) and [repair lifecycle](repair-lifecycle.md) for
the exact guarantees and reset semantics.

## Before extending deployment scope

Production adoption needs per-device identity, encrypted transport, scoped operator
permissions, action/package authenticity, audit retention and resource budgets.
It also needs rollback procedures for each new action, longer outcome monitoring,
operational recovery and testing on the target operating systems. Adding Nexthink
or native Windows/macOS adapters does not remove those requirements. These are
future engineering tasks, not capabilities implied by the current dashboard.
