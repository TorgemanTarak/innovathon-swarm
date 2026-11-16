# SWARM team handbook

SWARM observes a recovery on one endpoint, proposes a known repair, validates it
on a canary, and shares it with compatible endpoints. This handbook explains the
implementation in this repository and how to run, debug and extend it.

The current repair is deliberately narrow: quarantine a corrupt cache in an
app owned by SWARM, then exercise the app to verify recovery. The processes,
files, HTTP requests, SQLite records and rollback are real. General desktop
repair, Nexthink integration and Windows adapters remain future work. The
Pastry-inspired topology lab is a separate browser-only simulation.

## Choose a reading path

| Your goal | Start here | Then read |
|---|---|---|
| Run SWARM for the first time | [Getting started](getting-started.md) | [Demo runbook](demo-runbook.md), [operations](operations.md) |
| Understand the design | [Architecture](architecture.md) | [Repair lifecycle](repair-lifecycle.md), [module map](modules/README.md) |
| Work on the endpoint | [Endpoint modules](modules/endpoint.md) | [Data and state](data-and-state.md), [security boundaries](security.md) |
| Work on policy or HTTP contracts | [Coordinator modules](modules/coordinator.md) | [API reference](api.md), [repair lifecycle](repair-lifecycle.md) |
| Work on the CLI or dashboard | [Launchers and interfaces](modules/interfaces.md) | [Configuration](configuration.md), [development](development.md) |
| Prepare a team demo or diagnose a failure | [Operations](operations.md) | [Configuration](configuration.md), [demo runbook](demo-runbook.md) |
| Evaluate scaling or future integration | [Connectivity and scaling](scaling.md) | [Security boundaries](security.md), [roadmap](roadmap.md) |

## Documentation map

- [Getting started](getting-started.md): prerequisites, local rehearsal, manual
  repair, shared coordinator and teammate Macs.
- [Architecture](architecture.md): process boundaries, data flow, design choices
  and the distinction between live repair and mock topology.
- [Repair lifecycle](repair-lifecycle.md): incident, candidate, recipe, canary,
  replay, rollback, retries and reset, step by step.
- [Module map](modules/README.md): every Python module, browser asset and support
  script, with links to the source and deeper module guides.
- [Configuration](configuration.md): CLI flags, environment variables, defaults,
  ports and timing.
- [API reference](api.md): routes, authentication, typed payloads and responses.
- [Data and state](data-and-state.md): both SQLite stores, IDs, persistent state
  and reset behavior.
- [Operations](operations.md): logs, shutdown, restart, backups and troubleshooting.
- [Development](development.md): test layers, local workflow and how to add a
  supported action without weakening the execution boundary.
- [Security boundaries](security.md): what the prototype enforces and what a
  trusted-team deployment assumes.
- [Demo runbook](demo-runbook.md): presentation sequence, including failed canary.
- [Connectivity and scaling](scaling.md): existing measurements and remaining
  bottlenecks.
- [Visual references](design.md): Nexthink-inspired colors and UI patterns.
- [Roadmap](roadmap.md): work that is planned, not implemented.

## Vocabulary used throughout

| Term | Meaning in SWARM |
|---|---|
| Endpoint / node | An agent and its local application/data. `swarm node` launches both processes. |
| Coordinator / registry | The central HTTP service and its policy/storage layer. Endpoints do not contact each other. |
| Functional probe | An HTTP `GET /work` on the endpoint's app that actually loads its cache. It can regenerate a missing cache. |
| Incident | One endpoint's observed episode of a recognized functional failure. |
| Issue key | A deterministic signature for a supported failure, shared across incidents; environment compatibility is checked separately. |
| Candidate | An endpoint's hypothesis linking an observed external change to recovery using a preinstalled action. |
| Recipe | The coordinator's stored candidate plus rollout state and outcomes. |
| Assignment | One identified delivery of a recipe to an endpoint incident, with a role and deadline. |
| Canary | The first independent endpoint used to validate a candidate before follower dispatch. |
| Outcome | The endpoint's terminal report for an assignment: verified, failed, rejected or inconclusive. |
| Evidence | Local observations and bounded shared summaries; endpoint-reported information, not independently authenticated proof. |
| Online | Contact within 30 seconds: registration initializes freshness and heartbeats refresh it; the app may still be unhealthy or unknown. |
| Simulated endpoint | In a local rehearsal, a real app/agent pair running on the same host as the other pairs. |
| Synthetic device | A generated dot in `/mock` or a benchmark fixture, with no corresponding app repair. |

## Keeping these guides accurate

Commands assume the repository root unless stated otherwise. Source links point
to this checkout's files. The CLI's `--help`, Pydantic models and generated
`/openapi.json` are the detailed contract references; update these guides whenever
their behavior changes. [AGENTS.md](../AGENTS.md) defines contributor requirements.

Test results depend on the host and revision. A Linux process test, a simulated
DOM test, a real Chromium test and a multi-Mac rehearsal establish different
things. Record the command, revision and environment with any validation claim;
do not treat example device labels or synthetic fleet counts as hardware evidence.
