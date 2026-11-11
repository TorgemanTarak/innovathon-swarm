# Next development steps

## Before the live demo

- Run `make check` on the exact teammate Macs and verify local network reachability.
- Rehearse from a clean directory twice, including canary failure and Ctrl+C cleanup.
- If using TMax, test a pinned converted checkpoint against the local JSON narration contract; no model weights have been benchmarked by this repository.
- Choose whether the presentation uses real Macs or explicitly labelled single-machine simulated endpoints.

## Extend the useful core

1. Add a second failure/action class: a stopped app-owned worker, with functional health verification.
2. Introduce an observer/actuator protocol for native OS signals, retaining the typed recipe boundary.
3. Add bounded local terminal investigation: read-only diagnostic tools, limited turns, evidence citations, no fleet execution authority.
4. Record recurrence after longer observation windows and demote repairs by environment cohort.
5. Add a Nexthink Remote Action executor and Spark handoff/export adapter.
6. Add Windows Event Log/WER and targeted macOS Unified Logging adapters.

## Before any production use

Per-device identity, transport encryption, tenant scoping, approval policy, signed/versioned action packages, endpoint resource limits, evidence retention, production packaging, independent outcome verification, tamper resistance and durable rollout recovery need dedicated engineering. Federated model training is a separate experiment; sharing validated repair knowledge already demonstrates the core product hypothesis.
