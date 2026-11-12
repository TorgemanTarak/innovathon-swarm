# Architecture

SWARM demonstrates the conversion of one observed local recovery into a reusable, empirically tested repair. It learns an association between an issue and a supported action. It does not establish general causality or synthesize arbitrary system commands.

```mermaid
flowchart TD
    subgraph Endpoint[Each endpoint]
        App[Demo app] --> Probe[Functional probe]
        Cache[Watched cache] --> Observe[Local observation]
        Probe --> Journal[SQLite journal]
        Observe --> Journal
        Journal --> Learner[Candidate learner]
        Execute[Typed executor] --> Cache
        Execute --> Probe
    end
    Learner --> Registry[Shared registry]
    Registry --> Canary[Canary policy]
    Canary --> Execute
    Execute -->|Measured outcome| Registry
    Registry --> UI[Live dashboard]
```

## Detection and learning

The canonical issue key hashes a schema version, app identity, operation and bounded error code. Versions/platform are kept in applicability metadata. Application logs and cache contents remain local. The current watcher compares cache fingerprints once per tick; it is intentionally app-scoped.

An incident opens only after `/work` fails with the recognized cache error. A candidate requires an observed external cache change after that incident and three successful subsequent operations. Ordinary silence or process liveness cannot count as recovery. SWARM's own repairs update the observation baseline so they cannot become new independent human discoveries.

The optional local-model adapter narrates this evidence. It cannot alter the action type, issue identity, rollout permissions or verification outcome. Invalid output falls back to a labelled deterministic explanation. A future investigator can collect extra evidence through typed read-only tools without changing the executor's trust boundary.

### Shared evidence summaries

Candidates now carry up to three typed summaries: the recognized functional
failure, the observed external cache change, and the successful recovery checks.
Replay outcomes carry up to eight summaries of the precondition check, applied
action, attempted verification and rollback when applicable. Each summary names
its local evidence ID and endpoint observation time, normalized to UTC. Optional
fields contain only SHA-256 fingerprints (or `missing`/`oversized`), fixed probe
results/error codes and bounded check counts. There are no arbitrary text, path,
file-content or log fields in these summaries. Projection from the journal drops
unsupported fields; malformed historical summaries remain local references.

Observation time is when the endpoint recorded a result, not the exact time a
human changed a file. Cache fingerprints describe the samples taken around that
operation; concurrent changes can occur. An observed change followed by recovery
is an association, not authenticated proof of who acted or what caused recovery.
The dashboard labels the summaries as endpoint-reported. Existing incident,
compatibility, canary and outcome checks remain the execution authority.

Summary records travel in the existing durable candidate/outcome outbox. Delivery
retries preserve their IDs and times. A rejected precondition has no applied-action
record; failed or interrupted execution does not invent a successful verification.
Rollback is reported as restored only after the restore actually completes. If a
concurrent cache edit prevents restoration, the evidence reports rollback failure
and preserves the original outcome explanation.

Bounds apply per candidate/outcome; total journal and registry retention is still
a future production task. Older stored records without summaries remain readable
and are shown as unavailable, without fabricated historical snapshots. Upgrade
the coordinator and agents together: old strict wire contracts do not accept the
new fields. This is stored-data compatibility, not mixed-version fleet support.

## Execution and verification

Recipes contain an allowlisted action ID, an exact environment, originating evidence IDs and a revision. They contain no executable scripts or arbitrary paths. Endpoints recheck the current incident, environment, cache state and failing operation immediately before action.

`quarantine_demo_cache` atomically moves the known cache into a local quarantine directory. The app regenerates absent cache. Three consecutive successful functional operations produce a verified outcome. Failure restores the prior cache and pauses the recipe, unless the current cache has changed beyond the app's expected regeneration. In that case SWARM preserves the current cache and quarantined original for inspection rather than overwriting a concurrent human repair. Rollback is specific to this action; other action classes need their own implementations.

Demo filesystem helpers reject unmarked nonempty directories, traversal and symlink resources. They are a containment guard for controlled app files, not a general OS sandbox or a defense against an actively malicious privileged local user.

## Coordination and durability

The coordinator uses SQLite transactions to admit one canary per issue/action/environment cohort. Matching followers become eligible only after a verified canary outcome. Incompatible devices receive no assignment. Failed or inconclusive results pause the recipe; global resume does not silently reactivate it.

Assignments have IDs, deadlines and terminal outcomes. Duplicate submissions cannot inflate evidence counts. The local journal is bound to its endpoint ID, including across demo resets. The local journal claims an attempt before mutation; after response loss it resends the stored outcome rather than applying the action again. An agent interrupted during a repair reports inconclusive instead of blindly repeating it. Inspect its quarantined file when recovering such an interruption.

Assignment timeout defaults to 120 seconds; an unconfirmed canary pauses the recipe. Global pause prevents new work delivery, including pending work not yet picked up. An action already running may finish. This is not instant remote cancellation.

The dashboard previews current eligibility for the newest recipe, identifying
eligible, waiting, excluded, assigned and completed endpoints with specific
reasons. The snapshot names that recipe with `eligibility_recipe_id`; older
recipes retain their evidence and outcomes. This bounds the preview to one fleet
list instead of generating every recipe/device pair. Preview and dispatch use the
same side-effect-free policy predicate. A preview does not reserve a canary or
authorize a mutation; state can change before work delivery and endpoint checks.
It describes the current incident, while historical replay roles come from their
original assignments. Snapshot reads retain the existing assignment-expiry
processing but do not allocate work or emit incompatibility events just to render
the preview. An offline endpoint may still have an outstanding assignment; its
connectivity is shown separately from the assignment's terminal result.

Reset clears shared incidents, recipes and outcomes, retains registrations and queues explicit reset commands. Agents restore only known app resources and clear runtime association state. Local audit observations/quarantine are preserved. Results from old/reset assignments are rejected. Each endpoint's incident reports and proposals are also rejected until it acknowledges a successful reset, preventing an in-flight tick from restoring pre-reset knowledge. A failed reset leaves that evidence barrier in place; resolve the local error and request another reset.

## Transport and identity

HTTP polling is intentionally simple and sufficient for the demo. All API routes can require `X-Swarm-Token`; the CLI requires a token before binding the coordinator to a LAN address. The token authenticates membership in one trusted demo group, not individual device identity. A peer with this token can impersonate another demo peer; per-device credentials, tenant isolation, signed recipes and authenticated outcomes are required before production deployment.

The dashboard and health endpoint are readable without a token; fleet data and mutations are behind API authentication when configured. No raw logs are uploaded, but device labels and environment metadata are still shared with the group.

## Platform boundaries

The shared core consists of wire contracts, evidence storage, issue matching, candidate association, rollout and verification semantics. The current adapter is a controlled app with file operations and HTTP probes that runs natively on macOS/Linux.

A Windows adapter would provide Event Log/WER observations and invoke known PowerShell/Remote Action implementations. A macOS system adapter could add Unified Logging and crash-report ingestion. Neither broad OS integration is implied by the prototype.
