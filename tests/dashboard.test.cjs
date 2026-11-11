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

async function dashboard(initial) {
  let current = initial;
  const elements = Object.fromEntries([...markup.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, {
    dataset: {}, value: "", innerHTML: "", textContent: "", hidden: false,
    disabled: false, listeners: {}, classList: {toggle() {}},
    addEventListener(kind, callback) { this.listeners[kind] = callback; },
    focus() {}, showModal() { this.open = true; }, close() { this.open = false; },
  }]));
  const listeners = {};
  vm.runInNewContext(source, {
    document: {
      hidden: false,
      getElementById: (id) => elements[id],
      addEventListener(kind, callback) { listeners[kind] = callback; },
    },
    sessionStorage: {getItem() { return null; }},
    URL, AbortSignal, Date,
    setTimeout() { return 1; }, clearTimeout() {},
    async fetch(url) {
      assert.equal(url, "/api/state");
      return {ok: true, async json() { return structuredClone(current); }};
    },
  });
  await flush();
  assert.equal(elements.connection.className, "connection live");
  return {
    elements,
    async refresh(value) {
      current = value;
      listeners.visibilitychange();
      await flush();
      assert.equal(elements.connection.className, "connection live");
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
  assert.doesNotMatch(ui.elements["agent-list"].innerHTML, /<img|<a href/);
});
