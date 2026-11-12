# Innovathon demo runbook

## Rehearsal

1. Run `make setup`, `make check`, then `make demo`.
2. Open the coordinator at http://127.0.0.1:8765 and a demo app at http://127.0.0.1:8771.
3. Confirm all four agents are online and healthy. D has app version 2.0.0; A/B/C have 1.0.0.
4. Keep endpoint process logs under `.swarm/local/logs/` available for diagnosis.
5. For repeatable Chromium coverage, follow the optional browser setup in README
   and run `make test-browser`. This starts its own temporary fleet and leaves the
   presentation fleet untouched.

## Main story (about two minutes)

- Show the working app, then inject a cache fault across the fleet.
- Load the workspace again: it fails. The dashboard records matching incidents.
- Repair A manually using `uv run swarm repair --data-dir .swarm/local/mac-a`, or use the labelled simulated human repair button.
- A observes the external change and verifies three successful operations.
- Open **Repair evidence**. Show the recorded failure, external cache change and
  recovery checks with observation times. Fingerprints identify changed samples;
  the UI does not expose raw cache data or claim to identify the human who acted.
- Show the newest recipe's current eligibility reasons. A is the source and
  cannot validate its own discovery; compatible peers can become the canary.
- Watch one canary repair and verify. Only then does the other compatible device receive the fix.
- D remains broken. Its eligibility reason shows app version 2.0.0 versus the
  required 1.0.0. A matching error alone does not authorize an action.
- Expand the canary and follower replay details to show their actual precondition,
  action and verification observations. Legacy records may have no summaries.
- Show actual recovery counts and the chronological event trail. Do not call three short checks proof of permanent resolution.

## Failure and pause story

1. Reset and wait until every endpoint is healthy.
2. Pause rollout, inject faults and repair A.
3. Wait for the candidate to appear without execution.
4. Arm **Fail next verification** on both B and C so whichever is selected as canary will fail.
5. Resume rollout. The canary really fails its functional operation; its old cache is restored.
6. Show the failed verification and **rollback restored** evidence, the paused
   recipe and waiting peers. The remaining devices receive no repair.
7. Reset to prepare another run. Global resume intentionally does not unpause a failed recipe.

## Optional topology illustration

Open **Topology lab** (`/mock`) to show a synthetic fleet of up to 1,024 devices.
Choose source and destination IDs and press **Trace route** to follow prefix
progress through bounded peer contact tables. The central coordinator remains
visually separate from the route. Compare the generated directed contact count
with all-to-all, and toggle peer links or coordinator spokes for clarity.
Use this to explain an architectural possibility: the lab is browser-only,
not a live fleet, full Pastry protocol or distributed scalability benchmark.

## Teammate setup

Use the LAN instructions in README. Every endpoint needs a unique ID and its own data directory; all must use the coordinator's shared token. Agents send requests outbound; they do not accept arbitrary remote terminal connections. Open an app on the Mac where that app runs. A localhost app link from another laptop does not address that laptop's app.

## Troubleshooting

| Symptom | Check |
|---|---|
| Process exits immediately | Read the corresponding `.swarm/.../logs/` file; check port conflicts and Python dependency installation |
| Refuses data directory | Use an empty directory or an existing SWARM-marked directory; do not point at real app caches |
| Data directory belongs to another endpoint | Keep the original `--id`, or choose a new data directory for the new identity |
| Dashboard has no endpoints | Check coordinator URL, shared token and laptop connectivity |
| API returns 401 | Enter the same token in the dashboard and export it on the agent |
| Agent says unknown | The app may be unreachable; `/health` and `/work` are different checks |
| No candidate appears | Let a failure be observed before manually changing the cache; allow three successful probes |
| Recipe stays canary | Check the canary app/agent logs; missing verification eventually pauses it |
| Recipe stays paused after resume | Expected: global resume does not revive failed recipes; reset the demo |
| Model falls back | Confirm local server address/model name, JSON support and latency; narration has an 8-second request timeout |
| Reset is not immediate | Wait for endpoint polling and acknowledgement; disconnected endpoints cannot reset until they reconnect |

The prototype intentionally does not manipulate real Outlook/browser caches, drivers, system services, or employee files.
