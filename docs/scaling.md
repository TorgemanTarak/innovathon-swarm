# Connectivity and scaling

## What connects

Each endpoint independently registers with one configured coordinator, polls for
its own work and submits heartbeats, incident evidence and outcomes. Agents never
need the addresses of other endpoints. Computers using the same reachable URL and
token join the same fleet; separate coordinator databases are separate fleets.

The dashboard counts every registered endpoint, distinguishing recent heartbeats
from functional app health. An endpoint is offline after 30 seconds without a
heartbeat. If the browser loses the coordinator, the UI labels connectivity as the
last received snapshot. The topology drawing shows at most six endpoints and labels
that sample; the counters and endpoint cards describe the full snapshot.

Use `swarm status --expect-agents a b c d` with the coordinator URL and token to
check a specific set of participants. Missing and offline IDs cause a nonzero exit.
An online agent with a broken app still passes this connectivity check.

Agents retry registration after transport failures, preserve local evidence for
delivery, and reconnect to a restarted coordinator. This is a heartbeat-based view,
not a guarantee that a device remains reachable after the snapshot. The trusted
demo token is not per-device identity; use unique IDs on participating computers.

## Why no Pastry yet

For N endpoints and one dashboard, steady-state endpoint communication is about
2N coordinator requests per polling interval: one work request and one heartbeat
per endpoint, plus incident/repair traffic when needed. A local functional probe
does not traverse the fleet. Delivering a repair to N affected endpoints requires
N deliveries regardless of whether discovery uses a coordinator or a DHT.

The current network has N logical endpoint-to-coordinator links, rather than the
N(N−1)/2 links of a complete peer mesh. Pastry would address decentralized lookup
and membership, while adding routing-table maintenance, failure handling and new
trust boundaries. The prototype currently benefits more from bounded per-endpoint
responses, measured queries and an explicit coordinator capacity limit.

## Measured improvement

The original work-poll path scanned command history and all assignments when
checking expirations. If history grows with the fleet, N polls each scanning N
records create quadratic database work even with linear network traffic.

Three indexes now cover per-agent commands, pending assignments by agent and
pending assignments by expiration. They are created idempotently when an existing
database opens; repair authorization, canary policy and transaction behavior are
unchanged.

On Linux, a synthetic single-cohort fixture with N acknowledged commands and N−1
completed assignments produced the following SQLite VM instruction counts for
one round of N idle work polls:

| Synthetic peers | Before indexes | With indexes | Work response body |
|---:|---:|---:|---:|
| 32 | 12,890 | 7,903 | 48 bytes per poll |
| 128 | 125,306 | 31,615 | 48 bytes per poll |
| 512 | 1,680,890 | 126,463 | 48 bytes per poll |

These instruction counts isolate database lookup work; they are not throughput or
physical-device measurements. Regression tests grow unrelated history from 100 to
2,500 records and check executed SQL work rather than a flaky elapsed-time limit.
They also test upgrading an existing database while retaining command ordering
and acknowledgements.

For repeatable HTTP measurements:

```bash
make benchmark-fleet
# Or customize the workload:
uv run scripts/benchmark_fleet.py --sizes 32 128 512 --rounds 2 \
  --concurrency 16 --output .swarm/benchmarks/fleet.json
```

The tool seeds typed synthetic registrations, command acknowledgements and repair
outcomes, checks the canary gate, then starts an isolated real coordinator and sends
bounded concurrent heartbeat/work requests over loopback HTTP. It reports runtime
versions, expected/online IDs, errors, request counts, HTTP body bytes, p50/p95
latency and elapsed measurement time. It exits unsuccessfully on failed checks.
Setup time is excluded. The seed outcomes do not exercise an app or repair executor.

The 32/128/512-peer run on 11 November 2026 reported all expected IDs online, zero
HTTP/contract errors and 48-byte idle-work replies at every size. This demonstrates
the measured workload, not production readiness at an arbitrary fleet size.

## Remaining limits

- One coordinator and SQLite still serialize write transactions and remain a
  throughput limit and single point of failure. Automatic failover is not built.
- Unhealthy endpoints still search stored recipes. Many distinct recipe cohorts
  need indexed applicability lookup and a separate benchmark.
- The dashboard downloads all agents, recipes and outcomes. Pagination, bounded
  retention and incremental updates are needed for large fleets and many viewers.
  Indexing browser lookups does not bound snapshot size or DOM size.
- The HTTP benchmark uses concurrent batches, not thousands of running agents or
  real network partitions. Production polling needs workload-specific cadence,
  jitter/backoff and sustained-load measurements.
- Actual process tests cover native node registration, stale detection, endpoint
  restart and coordinator restart on the test host. Linux runs do not prove macOS,
  separate-machine LAN reachability, Windows or a decentralized peer protocol.
