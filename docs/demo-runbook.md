# Innovathon demo runbook

## Rehearsal

1. Run `make setup`, `make check`, then `make demo`.
2. Open the coordinator at http://127.0.0.1:8765 and a demo app at http://127.0.0.1:8771.
3. Confirm all four agents are online and healthy. D has app version 2.0.0; A/B/C have 1.0.0.
4. Keep endpoint process logs under `.swarm/local/logs/` available for diagnosis.

## Main story (about two minutes)

- Show the working app, then inject a cache fault across the fleet.
- Load the workspace again: it fails. The dashboard records matching incidents.
- Repair A manually using `uv run swarm repair --data-dir .swarm/local/mac-a`, or use the labelled simulated human repair button.
- A observes the external change and verifies three successful operations.
- Point to the candidate's evidence, action, source, and explanation mode.
- Watch one canary repair and verify. Only then does the other compatible device receive the fix.
- D remains broken and reports incompatible applicability. A matching error alone does not authorize an action.
- Show actual recovery counts and the chronological event trail. Do not call three short checks proof of permanent resolution.

## Failure and pause story

1. Reset and wait until every endpoint is healthy.
2. Pause rollout, inject faults and repair A.
3. Wait for the candidate to appear without execution.
4. Arm **Fail next verification** on both B and C so whichever is selected as canary will fail.
5. Resume rollout. The canary really fails its functional operation; its old cache is restored.
6. Show the failed outcome and paused recipe. The remaining devices receive no repair.
7. Reset to prepare another run. Global resume intentionally does not unpause a failed recipe.

## Teammate setup

Use the LAN instructions in README. Every endpoint needs a unique ID and its own data directory; all must use the coordinator's shared token. Agents send requests outbound; they do not accept arbitrary remote terminal connections. Open an app on the Mac where that app runs. A localhost app link from another laptop does not address that laptop's app.

## Troubleshooting

| Symptom | Check |
|---|---|
| Process exits immediately | Read the corresponding `.swarm/.../logs/` file; check port conflicts and Python dependency installation |
| Refuses data directory | Use an empty directory or an existing SWARM-marked directory; do not point at real app caches |
| Dashboard has no endpoints | Check coordinator URL, shared token and laptop connectivity |
| API returns 401 | Enter the same token in the dashboard and export it on the agent |
| Agent says unknown | The app may be unreachable; `/health` and `/work` are different checks |
| No candidate appears | Let a failure be observed before manually changing the cache; allow three successful probes |
| Recipe stays canary | Check the canary app/agent logs; missing verification eventually pauses it |
| Recipe stays paused after resume | Expected: global resume does not revive failed recipes; reset the demo |
| Model falls back | Confirm local server address/model name, JSON support and latency; narration has an 8-second request timeout |
| Reset is not immediate | Wait for endpoint polling and acknowledgement; disconnected endpoints cannot reset until they reconnect |

The prototype intentionally does not manipulate real Outlook/browser caches, drivers, system services, or employee files.
