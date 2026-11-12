// Optional dashboard logic checks: node --test tests/dashboard.test.cjs
// No browser or npm dependencies; this checks DOM writes, not visual layout.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const web = path.join(__dirname, "../src/swarm/web");
const markup = fs.readFileSync(path.join(web, "index.html"), "utf8");
const source = fs.readFileSync(path.join(web, "app.js"), "utf8");
const flush = () => new Promise(setImmediate);

function agent(id = "a") {
  return {
    agent_id: id, label: `Mac ${id.toUpperCase()}`, mode: "simulated",
    online: true, health: "healthy", incident_id: `${id}-incident-1`,
    demo_url: "http://127.0.0.1:8771",
    environment: {platform: "darwin", app_version: "1.0.0"},
  };
}

function snapshot() {
  return {agents: [], recipes: [], outcomes: [], events: [], demo_mode: true, paused: false};
}

function recipe(status = "validated") {
  return {
    recipe_id: "recipe-1", revision: 1, status, canary_agent_id: "b",
    successes: 1, failures: 0,
    candidate: {
      source_agent_id: "a", incident_id: "a-incident-1", explanation: "Observed recovery",
      environment: {platform: "darwin", app_version: "1.0.0"},
      evidence_ids: ["evidence-1"], verification_count: 3,
    },
  };
}

function evidence(kind, fields = {}) {
  return {evidence_id: `evidence-${kind}`, observed_at: "2026-01-01T12:00:01Z", kind, ...fields};
}

function outcome(fields = {}) {
  return {
    agent_id: "b", recipe_id: "recipe-1", incident_id: "b-incident-1", status: "verified",
    checks_passed: 3, checks_total: 3, detail: "Three functional operations passed.",
    observed_at: "2026-01-01T12:00:09Z", role: "canary", ...fields,
  };
}

function eligibility(agent_id, status, reason, reason_detail, role = null) {
  return {agent_id, status, reason, reason_detail, role};
}

async function dashboard(initial) {
  let current = initial;
  let response = {ok: true, status: 200};
  const elements = Object.fromEntries([...markup.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    dataset: {}, value: "", textContent: "", hidden: false, children: [], markupWrites: 0,
    disabled: false, listeners: {}, classList: {toggle() {}},
    addEventListener(kind, callback) { this.listeners[kind] = callback; },
    focus() { document.activeElement = this; },
    contains(node) { return this.children.includes(node); },
    querySelectorAll(selector) {
      if (selector === "a[data-app-agent]") return this.children.filter((node) => node.dataset.appAgent !== undefined);
      if (selector === "details[data-panel-key]") return this.children.filter((node) => node.dataset.panelKey !== undefined);
      if (selector === "[data-focus-key]") return this.children.filter((node) => node.dataset.focusKey !== undefined);
      throw new Error(`Unmodelled selector: ${selector}`);
    },
    showModal() { this.open = true; }, close() { this.open = false; },
  }]));
  const listeners = {};
  const document = {
    hidden: false, activeElement: null,
    getElementById: (id) => elements[id],
    addEventListener(kind, callback) { listeners[kind] = callback; },
  };
  // Model keyed links, summaries and panels only; this is not a browser DOM parser.
  for (const element of Object.values(elements)) {
    let html = "";
    Object.defineProperty(element, "innerHTML", {
      get() { return html; },
      set(value) {
        html = value;
        element.markupWrites++;
        if (element.contains(document.activeElement)) document.activeElement = null;
        element.children = [...value.matchAll(/<(?:a|details|summary|div)\b[^>]*\bdata-(app-agent|panel-key|focus-key)="([^"]+)"/g)].map(([, kind, id]) => ({
          dataset: {[{"app-agent": "appAgent", "panel-key": "panelKey", "focus-key": "focusKey"}[kind]]: id},
          open: false, focus() { document.activeElement = this; },
        }));
      },
    });
  }
  vm.runInNewContext(source, {
    document,
    sessionStorage: {getItem() { return null; }},
    URL, AbortSignal, Date,
    setTimeout() { return 1; }, clearTimeout() {},
    async fetch(url) {
      assert.equal(url, "/api/state");
      return {...response, async json() {
        if (response.malformed) throw new SyntaxError("Invalid JSON");
        return structuredClone(current);
      }};
    },
  });
  await flush();
  assert.equal(elements.connection.className, "connection live");
  return {
    elements, document,
    async refresh(value, {malformed = false, connected = true} = {}) {
      current = value;
      response = {ok: true, status: 200, malformed};
      listeners.visibilitychange();
      await flush();
      assert.equal(elements.connection.className, connected ? "connection live" : "connection offline");
    },
  };
}

test("old replay, origin and canary roles do not describe a new incident", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  state.recipes = [recipe()];
  state.outcomes = [{agent_id: "b", incident_id: "b-incident-1", status: "verified", recipe_id: "recipe-1"}];
  const ui = await dashboard(state);
  assert.match(ui.elements["agent-list"].innerHTML, /Origin discovery/);
  assert.match(ui.elements["agent-list"].innerHTML, /Verified replay/);
  state.agents.forEach((item) => { item.incident_id = `${item.agent_id}-incident-2`; item.health = "unhealthy"; });
  await ui.refresh(state);
  assert.doesNotMatch(ui.elements["agent-list"].innerHTML, /Origin discovery|Verified replay|Canary endpoint/);
  assert.doesNotMatch(ui.elements.topology.innerHTML, /ORIGIN DISCOVERY|VERIFIED REPLAY|CANARY ENDPOINT/);
  assert.match(ui.elements["recipe-list"].innerHTML, /Observed recovery/);
});

test("an active canary is labelled until verification completes", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  state.recipes = [recipe("canary")];
  const ui = await dashboard(state);
  assert.match(ui.elements["agent-list"].innerHTML, /Canary endpoint/);
  state.recipes[0].status = "validated";
  state.outcomes = [{agent_id: "b", incident_id: "b-incident-1", status: "verified", recipe_id: "recipe-1"}];
  await ui.refresh(state);
  assert.doesNotMatch(ui.elements["agent-list"].innerHTML, /Canary endpoint/);
  assert.match(ui.elements["agent-list"].innerHTML, /Verified replay/);
});

test("a paused recipe does not suppress another cohort's active canary", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b"), agent("c")];
  const stopped = recipe("paused");
  const active = recipe("canary");
  active.recipe_id = "recipe-2";
  active.candidate.environment.app_version = "2.0.0";
  active.canary_agent_id = "c";
  state.agents[2].environment.app_version = "2.0.0";
  state.recipes = [stopped, active];
  const ui = await dashboard(state);
  assert.equal(ui.elements["network-status"].textContent, "1 RECIPE PAUSED · CANARY IN PROGRESS");
  assert.equal((ui.elements.topology.innerHTML.match(/class="flow-line"/g) || []).length, 1);
  assert.equal(ui.elements["pause-label"].textContent, "Pause rollout");
  state.paused = true;
  await ui.refresh(state);
  assert.equal(ui.elements["network-status"].textContent, "ROLLOUT PAUSED");
  assert.doesNotMatch(ui.elements.topology.innerHTML, /class="flow-line"/);
  state.paused = false;
  active.status = "paused";
  await ui.refresh(state);
  assert.equal(ui.elements["network-status"].textContent, "2 RECIPES PAUSED");
  assert.doesNotMatch(ui.elements.topology.innerHTML, /class="flow-line"/);
});

test("renaming an endpoint refreshes cached evidence and event actor labels", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  state.recipes = [recipe()];
  state.events = [{event_id: "event-1", timestamp: "2026-01-01T00:00:00Z", kind: "candidate", message: "Recovery observed", agent_id: "a"}];
  const ui = await dashboard(state);
  assert.match(ui.elements["recipe-list"].innerHTML, /Mac A/);
  assert.match(ui.elements["event-list"].innerHTML, /Mac A/);
  state.agents[0].label = "Renamed endpoint";
  await ui.refresh(state);
  assert.match(ui.elements["recipe-list"].innerHTML, /Renamed endpoint/);
  assert.match(ui.elements["event-list"].innerHTML, /Renamed endpoint/);
  assert.doesNotMatch(ui.elements["recipe-list"].innerHTML, /Mac A/);
  assert.doesNotMatch(ui.elements["event-list"].innerHTML, /Mac A/);
});

test("empty, stale, read-only, native-link and escaped-label states remain usable", async () => {
  const state = snapshot();
  const ui = await dashboard(state);
  assert.equal(ui.elements["fault-button"].disabled, true);
  state.agents = [agent(), agent("b")];
  await ui.refresh(state);
  assert.equal(ui.elements["source-select"].value, "a");
  assert.equal(ui.elements["failure-select"].value, "b");
  ui.elements["source-select"].value = "b";
  state.agents[0].label = '<img src=x onerror="alert(1)">';
  state.agents[0].online = false;
  state.agents[0].mode = "native";
  state.agents[1].demo_url = null;
  state.demo_mode = false;
  await ui.refresh(state);
  assert.equal(ui.elements["source-select"].value, "b");
  assert.equal(ui.elements["metric-healthy"].textContent, 1);
  assert.equal(ui.elements["fault-button"].disabled, true);
  assert.match(ui.elements["agent-list"].innerHTML, /Offline/);
  assert.match(ui.elements["agent-list"].innerHTML, /&lt;img/);
  assert.doesNotMatch(ui.elements["agent-list"].innerHTML, /<img|<a\s/);
});

test("invalid snapshots preserve the last fleet and disable controls until recovery", async () => {
  const state = snapshot();
  state.agents = [agent()];
  const ui = await dashboard(state);
  const cards = ui.elements["agent-list"].innerHTML;
  const invalid = [
    null, [], {}, {...state, paused: "false"}, {...state, demo_mode: null},
    {...state, agents: null}, {...state, recipes: {}}, {...state, outcomes: null}, {...state, events: {}},
    {...state, agents: [null]}, {...state, agents: [{...agent(), label: 3}]},
    {...state, agents: [{...agent(), health: '<img src=x>'}]},
    {...state, agents: [{...agent(), environment: null}]},
    {...state, recipes: [null]}, {...state, recipes: [{...recipe(), candidate: null}]},
    {...state, recipes: [{...recipe(), candidate: {...recipe().candidate, environment: null}}]},
    {...state, outcomes: [null]}, {...state, outcomes: [{agent_id: "a", status: {}}]},
    {...state, events: [null]}, {...state, events: [{event_id: "event-1", kind: 42}]},
  ];
  for (const value of invalid) {
    await ui.refresh(value, {connected: false});
    assert.equal(ui.elements["agent-list"].innerHTML, cards);
    assert.equal(ui.elements["metric-healthy"].textContent, 1);
    assert.equal(ui.elements["error-banner"].hidden, false);
    assert.match(ui.elements["snapshot-time"].textContent, /^Last snapshot/);
    for (const id of ["fault-button", "repair-button", "pause-button", "reset-button"]) {
      assert.equal(ui.elements[id].disabled, true);
    }
    await ui.refresh(state);
    assert.equal(ui.elements["error-banner"].hidden, true);
    assert.equal(ui.elements["fault-button"].disabled, false);
  }
  await ui.refresh(null, {malformed: true, connected: false});
  assert.equal(ui.elements["agent-list"].innerHTML, cards);
  assert.equal(ui.elements["metric-healthy"].textContent, 1);
  assert.equal(ui.elements["fault-button"].disabled, true);
  await ui.refresh(state);
});

test("heartbeat polls preserve app links and genuine card updates retain endpoint focus", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  const ui = await dashboard(state);
  const list = ui.elements["agent-list"];
  const originalLink = list.children[1];
  originalLink.focus();
  const writes = list.markupWrites;
  state.agents[1].last_seen = "2026-01-01T00:00:01Z";
  await ui.refresh(state);
  assert.equal(list.markupWrites, writes);
  assert.equal(ui.document.activeElement, originalLink);

  state.agents[1].health = "unhealthy";
  state.agents.reverse();
  await ui.refresh(state);
  assert.equal(list.markupWrites, writes + 1);
  assert.notEqual(ui.document.activeElement, originalLink);
  assert.equal(ui.document.activeElement.dataset.appAgent, "b");
  assert.match(list.innerHTML, /Issue detected/);

  // If that endpoint disappears, do not move focus to a different app link.
  state.agents = state.agents.filter((item) => item.agent_id !== "b");
  await ui.refresh(state);
  assert.equal(ui.document.activeElement, null);
});

test("membership distinguishes connectivity from app health and labels the topology sample", async () => {
  const state = snapshot();
  const ui = await dashboard(state);
  assert.equal(ui.elements["membership-status"].textContent, "0 online · 0 offline · 0 registered");
  state.agents = Array.from({length: 8}, (_, i) => agent(`endpoint-${i}`));
  state.agents[0].health = "unhealthy";
  state.agents[7].online = false;
  await ui.refresh(state);
  assert.equal(ui.elements["membership-status"].textContent, "7 online · 1 offline · 8 registered");
  assert.equal(ui.elements["metric-healthy"].textContent, 6);
  assert.equal(ui.elements["topology-scope"].textContent, "Showing 6 of 8 endpoints");
  assert.match(ui.elements.topology.innerHTML, /6 of 8 registered endpoints shown/);
  // A device outside the six-node diagram must still change the complete fleet count.
  state.agents[7].online = true;
  await ui.refresh(state);
  assert.equal(ui.elements["membership-status"].textContent, "8 online · 0 offline · 8 registered");
  assert.match(ui.elements.topology.innerHTML, /8 endpoints have a recent heartbeat/);
  await ui.refresh(null, {malformed: true, connected: false});
  assert.equal(ui.elements["membership-status"].textContent, "Last snapshot · 8 online · 0 offline · 8 registered");
  await ui.refresh(state);
  assert.equal(ui.elements["membership-status"].textContent, "8 online · 0 offline · 8 registered");
});

test("origin observations and replay evidence retain actors, role and final report time", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b"), agent("c")];
  const learned = recipe();
  const hash = "a".repeat(64);
  learned.candidate.evidence = [
    evidence("functional_failure", {cache_after: hash, probe_result: "failed", error_code: "CACHE_INVALID"}),
    evidence("cache_changed", {cache_before: hash, cache_after: "missing"}),
    evidence("recovery_verified", {probe_result: "passed", checks_passed: 3, checks_total: 3}),
  ];
  state.recipes = [learned];
  state.outcomes = [outcome({evidence: [
    evidence("repair_precondition", {probe_result: "failed"}),
    evidence("repair_applied", {cache_before: hash, cache_after: "missing"}),
    evidence("repair_verified", {probe_result: "passed", checks_passed: 3, checks_total: 3}),
  ]}), outcome({agent_id: "c", role: "follower", status: "failed", checks_passed: 0, evidence: [
    evidence("verification_failed", {error_code: "VERIFICATION_FAILED", checks_passed: 0, checks_total: 3}),
    evidence("rollback_restored", {cache_after: hash}),
  ]})];
  const ui = await dashboard(state);
  const html = ui.elements["recipe-list"].innerHTML;
  assert.match(html, /Origin observations<\/h4><span>Mac A · macOS · app 1\.0\.0/);
  assert.match(html, /Functional failure[\s\S]*Cache changed[\s\S]*Origin recovery verified/);
  assert.match(html, /Before: <code title="a{64}">SHA256 a{12}…<\/code>/);
  assert.match(html, /After: <code title="missing">missing<\/code>/);
  assert.match(html, /Mac B<\/strong><span class="attempt-role">Canary/);
  assert.match(html, /Mac C<\/strong><span class="attempt-role">Follower/);
  assert.match(html, /Replay recovery verified/);
  assert.match(html, /Verification failed[\s\S]*Previous cache restored/);
  assert.match(html, /Final report <time datetime="2026-01-01T12:00:09Z"/);
  assert.match(html, /Reported by endpoints\. An observed change followed by recovery does not prove the cause\./);
});

test("legacy recipes and outcomes show unavailable summaries without invented timeline or roles", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  state.recipes = [recipe()];
  state.outcomes = [outcome({role: undefined, observed_at: "legacy unparseable timestamp"})];
  const ui = await dashboard(state);
  const html = ui.elements["recipe-list"].innerHTML;
  assert.equal((html.match(/Structured evidence summary unavailable/g) || []).length, 2);
  assert.match(html, /Eligibility summary unavailable/);
  assert.match(html, /attempt-role">Replay/);
  assert.match(html, /Final report time unavailable/);
  assert.doesNotMatch(html, /class="evidence-step"|attempt-role">Canary|attempt-role">Follower/);
  state.recipes[0].candidate.evidence = [];
  state.outcomes[0].evidence = [];
  await ui.refresh(state);
  assert.match(ui.elements["recipe-list"].innerHTML, /Structured evidence summary unavailable/);
});

test("evidence and eligibility escape endpoint-supplied text and preserve exact incompatibility", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  state.agents[0].label = '<img src=x onerror="bad()">';
  const learned = recipe();
  learned.recipe_id = 'recipe-1" onclick="bad()';
  learned.candidate.explanation = "<script>bad()</script>";
  learned.candidate.evidence = [evidence("cache_changed", {evidence_id: '<b title="bad">ref</b>', cache_after: "oversized"})];
  learned.eligibility = [eligibility("b", "excluded", 'environment_mismatch" onmouseover="bad()', 'App version 2.0.0 requires 1.0.0; <img src=x>.')];
  state.recipes = [learned];
  state.outcomes = [outcome({recipe_id: learned.recipe_id, detail: '<iframe src="bad">'})];
  const ui = await dashboard(state);
  const html = ui.elements["recipe-list"].innerHTML;
  assert.match(html, /&lt;script&gt;bad\(\)&lt;\/script&gt;/);
  assert.match(html, /Ref &lt;b title=&quot;bad&quot;&gt;ref&lt;\/b&gt;/);
  assert.match(html, /App version 2\.0\.0 requires 1\.0\.0; &lt;img/);
  assert.match(html, /data-panel-key="recipe-1&quot; onclick=&quot;bad\(\)"/);
  assert.match(html, /&lt;iframe src=&quot;bad&quot;&gt;/);
  assert.doesNotMatch(html, /<img|<script|<iframe|<b title=| onclick="| onmouseover="/);
});

test("malformed optional evidence and eligibility preserve the last usable snapshot", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  state.recipes = [recipe()];
  state.recipes[0].candidate.evidence = [evidence("functional_failure")];
  state.recipes[0].eligibility = [eligibility("b", "eligible", "follower_ready", "Canary verified.", "follower")];
  state.outcomes = [outcome({evidence: [evidence("repair_verified")]})];
  const ui = await dashboard(state);
  const previous = ui.elements["recipe-list"].innerHTML;
  const malformedEvidence = [
    null, {}, [null], ["observed"], [{}],
    [evidence("unknown")], [evidence("__proto__")],
    [evidence("cache_changed", {observed_at: "yesterday"})],
    [evidence("cache_changed", {observed_at: "2026-01-01T12:00:00"})],
    [evidence("cache_changed", {cache_before: {}})],
    [evidence("cache_changed", {cache_after: "arbitrary raw cache contents"})],
    [evidence("functional_failure", {probe_result: true})],
    [evidence("functional_failure", {error_code: "UNKNOWN"})],
    [evidence("recovery_verified", {checks_passed: 4, checks_total: 3})],
    [evidence("recovery_verified", {checks_passed: -1, checks_total: 3})],
    [evidence("recovery_verified", {checks_passed: 3})],
    [evidence("recovery_verified", {checks_passed: 0, checks_total: 1.5})],
    [evidence("recovery_verified", {checks_passed: 0, checks_total: 101})],
  ];
  const invalid = malformedEvidence.flatMap((items) => {
    const candidate = structuredClone(state);
    candidate.recipes[0].candidate.evidence = items;
    const replay = structuredClone(state);
    replay.outcomes[0].evidence = items;
    return [candidate, replay];
  });
  for (const [location, length] of [["candidate", 4], ["outcome", 9]]) {
    const value = structuredClone(state);
    const target = location === "candidate" ? value.recipes[0].candidate : value.outcomes[0];
    target.evidence = Array.from({length}, () => evidence("cache_changed"));
    invalid.push(value);
  }
  for (const entries of [null, {}, [null], [{}],
    [eligibility("b", "invalid", "code", "detail")],
    [eligibility("b", "waiting", "code", 3)],
    [eligibility("b", "waiting", "code", "detail", "origin")]]) {
    const value = structuredClone(state);
    value.recipes[0].eligibility = entries;
    invalid.push(value);
  }
  invalid.push({...state, outcomes: [outcome({role: "origin"})]});
  invalid.push({...state, eligibility_recipe_id: {}});
  for (const value of invalid) {
    await ui.refresh(value, {connected: false});
    assert.equal(ui.elements["recipe-list"].innerHTML, previous);
    assert.equal(ui.elements["fault-button"].disabled, true);
    assert.match(ui.elements["snapshot-time"].textContent, /^Last snapshot/);
    await ui.refresh(state);
  }
});

test("eligibility follows server decisions for pause, offline and the current incident", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  state.recipes = [recipe()];
  state.outcomes = [outcome()];
  const learned = state.recipes[0];
  learned.eligibility = [eligibility("b", "completed", "outcome_verified", "Current incident: verified.", "canary")];
  const ui = await dashboard(state);
  assert.match(ui.elements["recipe-list"].innerHTML, /1 completed/);
  state.agents[1].incident_id = "b-incident-2";
  learned.eligibility = [eligibility("b", "eligible", "follower_ready", "Canary verification permits follower repair.", "follower")];
  await ui.refresh(state);
  assert.match(ui.elements["recipe-list"].innerHTML, /1 eligible/);
  assert.doesNotMatch(ui.elements["recipe-list"].innerHTML, /1 completed|Current incident: verified/);
  // The earlier recorded outcome keeps its immutable canary role.
  assert.match(ui.elements["recipe-list"].innerHTML, /attempt-role">Canary/);
  state.paused = true;
  learned.eligibility = [eligibility("b", "waiting", "global_paused", "Fleet dispatch is paused.")];
  await ui.refresh(state);
  assert.match(ui.elements["recipe-list"].innerHTML, /1 waiting/);
  assert.match(ui.elements["recipe-list"].innerHTML, /Fleet dispatch is paused\./);
  state.paused = false;
  state.agents[1].online = false;
  learned.eligibility = [eligibility("b", "waiting", "offline", "No endpoint heartbeat within 30 seconds.")];
  await ui.refresh(state);
  assert.match(ui.elements["recipe-list"].innerHTML, /No endpoint heartbeat within 30 seconds\./);
  await ui.refresh(null, {connected: false, malformed: true});
  assert.match(ui.elements["recipe-list"].innerHTML, /No endpoint heartbeat within 30 seconds\./);
  assert.match(ui.elements["snapshot-time"].textContent, /^Last snapshot/);
});

test("evidence expansion and focus survive updates while unchanged decisions avoid writes", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  state.recipes = [recipe()];
  state.recipes[0].eligibility = [eligibility("b", "waiting", "canary_in_progress", "Waiting for verification.", "canary")];
  const ui = await dashboard(state);
  const list = ui.elements["recipe-list"];
  const panel = () => list.querySelectorAll("details[data-panel-key]")[0];
  const focusable = (key) => list.querySelectorAll("[data-focus-key]").find((node) => node.dataset.focusKey === key);
  panel().open = true;
  const summary = focusable("replays:recipe-1");
  summary.focus();
  const writes = list.markupWrites;
  state.agents[1].last_seen = "2026-01-01T12:00:02Z";
  await ui.refresh(state);
  assert.equal(list.markupWrites, writes);
  assert.equal(panel().open, true);
  assert.equal(ui.document.activeElement, summary);

  state.outcomes = [outcome()];
  state.recipes[0].eligibility = [eligibility("b", "completed", "outcome_verified", "Recovery verified.", "canary")];
  await ui.refresh(state);
  assert.equal(list.markupWrites, writes + 1);
  assert.equal(panel().open, true);
  assert.notEqual(ui.document.activeElement, summary);
  assert.equal(ui.document.activeElement.dataset.focusKey, "replays:recipe-1");

  panel().open = false;
  focusable("eligibility:recipe-1").focus();
  focusable("eligibility:recipe-1").scrollTop = 120;
  state.agents[1].label = "Renamed Mac";
  await ui.refresh(state);
  assert.equal(panel().open, false);
  assert.equal(ui.document.activeElement.dataset.focusKey, "eligibility:recipe-1");
  assert.equal(focusable("eligibility:recipe-1").scrollTop, 120);
  state.recipes = [];
  state.outcomes = [];
  await ui.refresh(state);
  assert.equal(ui.document.activeElement, null);
  assert.match(list.innerHTML, /No repair learned yet/);
});

test("historical evidence explains that current eligibility belongs to the latest recipe", async () => {
  const state = snapshot();
  state.agents = [agent(), agent("b")];
  const latest = recipe("candidate");
  latest.recipe_id = "recipe-2";
  latest.eligibility = [eligibility("b", "eligible", "canary_available", "Matching incident ready for canary.", "canary")];
  state.recipes = [latest, recipe()];
  state.eligibility_recipe_id = latest.recipe_id;
  const ui = await dashboard(state);
  const html = ui.elements["recipe-list"].innerHTML;
  assert.equal((html.match(/class="eligibility-row"/g) || []).length, 1);
  assert.equal((html.match(/Current eligibility is shown on the latest recipe\./g) || []).length, 1);
  assert.match(html, /Matching incident ready for canary/);
  assert.doesNotMatch(html, /Eligibility summary unavailable/);
  delete state.eligibility_recipe_id;
  await ui.refresh(state);
  assert.match(ui.elements["recipe-list"].innerHTML, /Eligibility summary unavailable/);
  assert.doesNotMatch(ui.elements["recipe-list"].innerHTML, /Current eligibility is shown on the latest recipe/);
});
