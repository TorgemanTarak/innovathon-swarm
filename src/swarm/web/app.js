/* SWARM dashboard. Every displayed outcome comes from the coordinator. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const safeArray = (value) => Array.isArray(value) ? value : [];
  let token = "";
  try { token = sessionStorage.getItem("swarm-token") || ""; } catch (_) { /* Restricted storage still permits in-memory access. */ }
  let state = null;
  let connected = false;
  let busy = false;
  let awaitingAuth = false;
  let polling = false;
  let timer = null;
  let toastTimer = null;
  let agentMarkup = null;
  let topologyKey = "";
  let recipeKey = "";
  let eventKey = "";
  let agentsById = new Map();
  let rolesByAgent = new Map();
  let outcomesByRecipe = new Map();
  const seenEvents = new Set();

  // Guard the rendering boundary, not the full versioned wire contract.
  // An invalid response must never replace the last usable fleet snapshot.
  function validState(value) {
    const record = (item) => item !== null && typeof item === "object" && !Array.isArray(item);
    const strings = (item, keys) => record(item) && keys.every((key) => typeof item[key] === "string");
    const environment = (item) => strings(item, ["platform", "app_version"]);
    return record(value) && typeof value.paused === "boolean" && typeof value.demo_mode === "boolean"
      && ["agents", "recipes", "outcomes", "events"].every((key) => Array.isArray(value[key]))
      && value.agents.every((agent) => strings(agent, ["agent_id", "label"])
        && ["healthy", "unhealthy", "unknown"].includes(agent.health) && environment(agent.environment))
      && value.recipes.every((recipe) => strings(recipe, ["recipe_id", "status"])
        && strings(recipe.candidate, ["source_agent_id", "incident_id", "explanation"])
        && environment(recipe.candidate.environment)
        && Array.isArray(recipe.candidate.evidence_ids)
        && recipe.candidate.evidence_ids.every((id) => typeof id === "string"))
      && value.outcomes.every((outcome) => strings(outcome, ["agent_id", "recipe_id", "incident_id", "status"]))
      && value.events.every((event) => strings(event, ["event_id", "timestamp", "kind", "message"]));
  }

  function timeLabel(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "—" : date.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
  }
  function isStale(agent) {
    if (typeof agent.online === "boolean") return !agent.online;
    const stamp = new Date(agent.last_seen).getTime();
    return !Number.isFinite(stamp) || Date.now() - stamp > 30000;
  }
  function health(agent) { return isStale(agent) ? "unknown" : (agent.health || "unknown"); }
  function platformLabel(value) { return ({darwin:"macOS",macos:"macOS",windows:"Windows",linux:"Linux"})[value] || value || "Unknown OS"; }
  function labelFor(id) { return agentsById.get(id)?.label || id || "Coordinator"; }
  function short(value) { return String(value || "").slice(0, 12); }
  function demoLink(value) {
    try { const url = new URL(value); return ["http:","https:"].includes(url.protocol) ? url.href : null; } catch (_) { return null; }
  }
  function appAddress(agent) {
    const address = demoLink(agent.demo_url);
    if (!address) return `<span>${esc(short(agent.agent_id))}</span>`;
    const localHost = ["localhost", "127.0.0.1", "[::1]", "::1"].includes(new URL(address).hostname);
    if (agent.mode === "simulated" && localHost) {
      return `<a data-app-agent="${esc(agent.agent_id)}" href="${esc(address)}" target="_blank" rel="noopener noreferrer">Open app <svg class="external-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 12 12 4M4 4h8v8"/></svg></a>`;
    }
    // A native agent's loopback address belongs to that endpoint, not the dashboard viewer.
    return `<span class="endpoint-address" title="Open this address on ${esc(agent.label)}"><span>ENDPOINT-LOCAL APP</span>${esc(address)}</span>`;
  }

  async function api(path, body) {
    const headers = {"Accept":"application/json"};
    if (token) headers["X-Swarm-Token"] = token;
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {method:body === undefined ? "GET" : "POST",headers,body:body === undefined ? undefined : JSON.stringify(body),signal:AbortSignal.timeout(8000),cache:"no-store"});
    let result;
    try { result = await response.json(); } catch (_) {
      if (response.ok) throw new Error("The coordinator returned invalid JSON.");
      result = {};
    }
    if (!response.ok) {
      const error = new Error(typeof result?.detail === "string" ? result.detail : `Coordinator returned HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function setConnection(ok, message) {
    connected = ok;
    const element = $("connection");
    element.className = `connection ${ok ? "live" : "offline"}`;
    element.innerHTML = `<i></i>${ok ? "Live connection" : "Disconnected"}`;
    $("error-banner").hidden = ok;
    if (!ok) $("error-message").textContent = message || "The coordinator is unreachable. Showing the last received snapshot.";
    $("footer-status").textContent = ok ? "Coordinator online" : "Connection interrupted";
    if (!ok && state) $("snapshot-time").textContent = "Last snapshot · " + $("snapshot-time").dataset.time;
    if (!ok && state) {
      const online = state.agents.filter((agent) => !isStale(agent)).length;
      $("membership-status").textContent = `Last snapshot · ${online} online · ${state.agents.length - online} offline · ${state.agents.length} registered`;
    }
    updateControls();
  }

  function toast(message, error = false) {
    const el = $("toast");
    el.textContent = message;
    el.classList.toggle("error", error);
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, error ? 7000 : 4000);
  }

  function accessDialog() {
    $("access-token").value = token;
    if (!$("access-dialog").open) $("access-dialog").showModal();
    setTimeout(() => $("access-token").focus(), 0);
  }

  function updateControls() {
    const hasAgents = !!state?.agents.length;
    const enabled = connected && !busy && !awaitingAuth;
    const demo = enabled && hasAgents && !!state?.demo_mode;
    ["fault-button","repair-button","reset-button","failure-button","source-select","failure-select"].forEach((id) => { $(id).disabled = !demo; });
    $("pause-button").disabled = !enabled || !state;
    $("pause-label").textContent = state?.paused ? "Resume rollout" : "Pause rollout";
    $("pause-icon").innerHTML = `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">${state?.paused ? '<path d="m5 3 8 5-8 5Z"/>' : '<path d="M5 3v10M11 3v10"/>'}</svg>`;
    if (state && !state.demo_mode) {
      $("demo-badge").textContent = "READ ONLY";
      $("control-description").textContent = "Demo mutations are disabled on this coordinator.";
    } else {
      $("demo-badge").textContent = "SANDBOX";
      $("control-description").textContent = "A controlled failure. A real repair loop.";
    }
  }

  function fillSelectors(agents) {
    ["source-select","failure-select"].forEach((id, index) => {
      const select = $(id);
      const old = select.value;
      const key = agents.map((a) => `${a.agent_id}:${a.label}`).join("|");
      if (select.dataset.key === key) return;
      select.dataset.key = key;
      select.innerHTML = agents.length ? agents.map((a) => `<option value="${esc(a.agent_id)}">${esc(a.label)}</option>`).join("") : '<option value="">No endpoints connected</option>';
      select.value = agents.some((a) => a.agent_id === old) ? old : (agents[Math.min(index, agents.length - 1)]?.agent_id || "");
    });
  }

  function indexSnapshot() {
    // Build lookups once per snapshot rather than scan fleet history for each card.
    agentsById = new Map(state.agents.map((agent) => [agent.agent_id, agent]));
    rolesByAgent = new Map();
    outcomesByRecipe = new Map();
    for (const outcome of state.outcomes) {
      if (!outcomesByRecipe.has(outcome.recipe_id)) outcomesByRecipe.set(outcome.recipe_id, []);
      outcomesByRecipe.get(outcome.recipe_id).push(outcome);
      if (!rolesByAgent.has(outcome.agent_id) && agentsById.get(outcome.agent_id)?.incident_id === outcome.incident_id) {
        rolesByAgent.set(outcome.agent_id, outcome.status === "verified" ? "Verified replay" : outcome.status);
      }
    }
    for (const {candidate} of state.recipes) {
      if (!rolesByAgent.has(candidate.source_agent_id) && agentsById.get(candidate.source_agent_id)?.incident_id === candidate.incident_id) {
        rolesByAgent.set(candidate.source_agent_id, "Origin discovery");
      }
    }
    for (const recipe of state.recipes) {
      if (recipe.status === "canary" && !rolesByAgent.has(recipe.canary_agent_id)) rolesByAgent.set(recipe.canary_agent_id, "Canary endpoint");
    }
  }

  function roleFor(agent) {
    // Earlier incidents remain visible in Repair evidence, not as this incident's role.
    return rolesByAgent.get(agent.agent_id) || (agent.mode === "simulated" ? "Simulated endpoint" : "Native endpoint");
  }

  function renderTopology() {
    const agents = state.agents.slice(0, 6);
    const total = state.agents.length;
    const online = state.agents.filter((agent) => !isStale(agent)).length;
    $("membership-status").textContent = `${online} online · ${total - online} offline · ${total} registered`;
    $("topology-scope").textContent = total > agents.length ? `Showing ${agents.length} of ${total} endpoints` : "Shared coordinator";
    const paused = !!state.paused;
    const pausedRecipes = state.recipes.filter((r) => r.status === "paused").length;
    const canaries = state.recipes.filter((r) => r.status === "canary");
    const canaryAgents = new Set(canaries.map((r) => r.canary_agent_id));
    const validated = state.recipes.some((r) => r.status === "validated");
    const candidate = state.recipes.some((r) => r.status === "candidate");
    const activity = canaries.length ? "CANARY IN PROGRESS" : validated ? "VERIFIED KNOWLEDGE" : candidate ? "CANDIDATE DISCOVERED" : "";
    const recipePause = pausedRecipes ? `${pausedRecipes} ${pausedRecipes === 1 ? "RECIPE" : "RECIPES"} PAUSED` : "";
    let status = paused ? "ROLLOUT PAUSED" : [recipePause,activity].filter(Boolean).join(" · ") || "LISTENING FOR EVIDENCE";
    if (!agents.length) status = "AWAITING SIGNAL";
    $("network-status").textContent = status;
    const key = JSON.stringify([agents.map((a) => [a.agent_id,a.label,health(a),roleFor(a)]),total,online,status,state.recipes.length,[...canaryAgents]]);
    if (key === topologyKey) return;
    topologyKey = key;
    if (!agents.length) {
      $("topology").innerHTML = '<div class="network-empty"><span class="empty-orbit" aria-hidden="true">⬡</span><strong>Waiting for the first endpoint</strong><p>Start a SWARM agent to bring this network online.</p></div>';
      return;
    }
    const spacing = Math.min(250, 730 / Math.max(1, agents.length - 1));
    const center = 450;
    const hubColor = paused ? "var(--amber)" : "var(--blue)";
    const nodes = agents.map((agent, index) => {
      const x = center + (index - (agents.length - 1) / 2) * spacing;
      const status = health(agent);
      const color = status === "healthy" ? "var(--teal)" : status === "unhealthy" ? "var(--amber)" : "var(--muted)";
      const active = canaryAgents.has(agent.agent_id) && !paused;
      const line = `M450 102 C450 143 ${x} 132 ${x} 180`;
      const label = agent.label.length > 22 ? `${agent.label.slice(0,20)}…` : agent.label;
      return `<path d="${line}" fill="none" stroke="${color}" stroke-opacity=".18" stroke-width="1.4"/><path d="${line}" fill="none" stroke="${color}" stroke-opacity="${active ? ".7" : ".28"}" stroke-width="1.4" stroke-dasharray="${active ? "4 12" : "2 9"}" ${active ? 'class="flow-line"' : ""}/><circle cx="${x}" cy="195" r="25" fill="var(--panel)" stroke="${color}" stroke-opacity=".45"/><circle cx="${x}" cy="195" r="32" fill="none" stroke="${color}" stroke-opacity=".07"/><g transform="translate(${x - 10} 188)" fill="none" stroke="${color}" stroke-width="1.3"><rect x="1" y="0" width="18" height="12" rx="1.7"/><path d="M-1 15h22M7 12v3M13 12v3"/></g><circle cx="${x + 19}" cy="177" r="4" fill="${color}" stroke="var(--panel)" stroke-width="2"/><text x="${x}" y="241" fill="var(--text)" text-anchor="middle" font-size="12">${esc(label)}</text><text x="${x}" y="259" fill="${color}" text-anchor="middle" font-size="10" letter-spacing=".5">${esc(roleFor(agent).toUpperCase())}</text>`;
    }).join("");
    $("topology").innerHTML = `<svg class="network-svg" viewBox="0 0 900 285" role="img" aria-label="${esc(`${agents.length} of ${total} registered endpoints shown. ${online} endpoints have a recent heartbeat. ${status.toLowerCase()}.`)}"><defs><radialGradient id="hub-halo"><stop stop-color="${hubColor}" stop-opacity=".11"/><stop offset="1" stop-color="${hubColor}" stop-opacity="0"/></radialGradient></defs><circle cx="450" cy="65" r="85" fill="url(#hub-halo)"/>${nodes}<circle cx="450" cy="65" r="43" fill="none" stroke="${hubColor}" stroke-opacity=".12" stroke-dasharray="2 6"/><circle cx="450" cy="65" r="34" fill="var(--blue-tint)" stroke="${hubColor}" stroke-opacity=".45"/><path d="m450 44 18 10.5v21L450 86l-18-10.5v-21L450 44Z" fill="none" stroke="${hubColor}" stroke-width="1.2"/><path d="m450 52 11 6.5v13L450 78l-11-6.5v-13L450 52Z" fill="${hubColor}" opacity=".85"/><text x="510" y="61" fill="var(--text)" font-size="12" letter-spacing=".5">SHARED FIX REGISTRY</text><text x="510" y="80" fill="var(--muted)" font-size="10">${state.recipes.length} ${state.recipes.length === 1 ? "recipe" : "recipes"} · evidence before execution</text></svg>`;
  }

  function renderAgents() {
    const markup = !state.agents.length ? '<div class="empty-state">Endpoint cards will appear here when agents register.</div>' : state.agents.map((agent, index) => {
      const current = health(agent);
      const detail = isStale(agent) ? "No recent heartbeat. Last received state is preserved." : (agent.detail || (current === "healthy" ? "Functional probe passed. Watching for changes." : "Waiting for a functional probe."));
      return `<article class="agent-card ${current}"><div class="agent-top"><span class="agent-monogram">${String(index + 1).padStart(2,"0")}</span><span class="status-pill ${current}">${isStale(agent) ? "Offline" : current === "unhealthy" ? "Issue detected" : current}</span></div><h3 class="agent-label" title="${esc(agent.label)}">${esc(agent.label)}</h3><div class="agent-meta">${esc(platformLabel(agent.environment?.platform))} <span>·</span> app ${esc(agent.environment?.app_version || "—")}</div><p class="agent-detail" title="${esc(detail)}">${esc(detail)}</p><div class="agent-foot"><span>${esc(roleFor(agent))}</span>${appAddress(agent)}</div></article>`;
    }).join("");
    if (markup === agentMarkup) return;
    agentMarkup = markup;
    const list = $("agent-list");
    const focusedAgent = list.contains(document.activeElement) ? document.activeElement?.dataset.appAgent : undefined;
    list.innerHTML = markup;
    if (focusedAgent !== undefined) {
      [...list.querySelectorAll("a[data-app-agent]")].find((link) => link.dataset.appAgent === focusedAgent)?.focus({preventScroll:true});
    }
  }

  function renderRecipes() {
    const key = JSON.stringify([state.recipes,state.outcomes,state.agents.map((a) => [a.agent_id,a.label])]);
    if (key === recipeKey) return;
    recipeKey = key;
    if (!state.recipes.length) {
      $("recipe-list").innerHTML = '<div class="evidence-empty"><span class="empty-symbol" aria-hidden="true">⌘</span><div><h3>No repair learned yet</h3><p>Inject a fault, then repair one endpoint. Its recovery becomes a candidate for the swarm.</p></div><span class="waiting-label">AWAITING DISCOVERY</span></div>';
      return;
    }
    $("recipe-list").innerHTML = state.recipes.map((recipe) => {
      const candidate = recipe.candidate;
      const outcomes = outcomesByRecipe.get(recipe.recipe_id) || [];
      const source = labelFor(candidate.source_agent_id);
      const path = `<span>${esc(source)}</span><b>→</b>${recipe.canary_agent_id ? `<span>${esc(labelFor(recipe.canary_agent_id))}</span>` : "Awaiting canary"}${recipe.status === "validated" ? '<b>→</b><span>Eligible peers</span>' : ""}`;
      return `<article class="recipe"><div class="recipe-title-row"><h3>Quarantine invalid application cache</h3><span class="status-pill ${esc(recipe.status)}">${esc(recipe.status)}</span><span class="recipe-id">RECIPE ${esc(short(recipe.recipe_id))} / REV ${esc(recipe.revision)}</span></div><p class="recipe-description">${esc(candidate.explanation)}</p><div class="recipe-grid"><div class="proof-item"><span class="proof-label">DISCOVERY & PROPAGATION</span><div class="proof-value proof-path">${path}</div><div class="proof-note">${candidate.investigator === "local_model" ? "Local model interpretation" : "Deterministic observation"}</div></div><div class="proof-item"><span class="proof-label">ORIGIN EVIDENCE</span><div class="proof-value"><em>${esc(candidate.verification_count)}</em> successful probes</div><div class="proof-note">${safeArray(candidate.evidence_ids).length} local evidence references</div></div><div class="proof-item"><span class="proof-label">REPLAY OUTCOMES</span><div class="proof-value"><em>${esc(recipe.successes)}</em> verified <span class="slash">/</span> <span class="${recipe.failures ? "bad" : ""}">${esc(recipe.failures)}</span> failed</div><div class="proof-note">${outcomes.length} recorded ${outcomes.length === 1 ? "attempt" : "attempts"} · short observation window</div></div><div class="proof-item"><span class="proof-label">APPLICABILITY</span><div class="proof-value">${esc(platformLabel(candidate.environment.platform))} · ${esc(candidate.environment.app_version)}</div><div class="proof-note">${esc(candidate.issue_key)}</div></div></div><details><summary>Inspect verification results and evidence references</summary><div class="attempt-list">${outcomes.length ? outcomes.map((outcome) => `<div class="attempt"><strong>${esc(labelFor(outcome.agent_id))}</strong><span>${esc(outcome.checks_passed)}/${esc(outcome.checks_total)} probes</span><p>${esc(outcome.detail)}</p><span class="status-pill ${esc(outcome.status)}">${esc(outcome.status)}</span></div>`).join("") : '<p class="muted">No cross-device verification has completed yet.</p>'}</div><p class="proof-note">Local evidence IDs: ${safeArray(candidate.evidence_ids).map(esc).join(" · ") || "None supplied"}</p><p class="proof-note">Raw logs remain on the originating device. A successful short probe does not establish permanent resolution.</p></details></article>`;
    }).join("");
  }

  function renderEvents() {
    const events = state.events.slice(0, 40);
    $("activity-count").textContent = `${state.events.length} EVENTS`;
    const key = JSON.stringify([events,state.agents.map((a) => [a.agent_id,a.label])]);
    if (key === eventKey) return;
    eventKey = key;
    if (!events.length) {
      $("event-list").innerHTML = '<div class="empty-state">The evidence trail starts when an endpoint connects.</div>';
      return;
    }
    $("event-list").innerHTML = events.map((event) => {
      const error = /fail|reject|error|rollback|paused/.test(event.kind);
      const warning = /fault|incident|canary|candidate/.test(event.kind);
      const isNew = seenEvents.size > 0 && !seenEvents.has(event.event_id);
      return `<div class="event-row ${error ? "error" : warning ? "warning" : ""} ${isNew ? "new" : ""}"><time class="event-time" datetime="${esc(event.timestamp)}">${timeLabel(event.timestamp)}</time><span class="event-kind"><i></i>${esc(event.kind.replace(/_/g," "))}</span><span class="event-message">${esc(event.message)}</span><span class="event-agent" title="${esc(labelFor(event.agent_id))}">${esc(labelFor(event.agent_id))}</span></div>`;
    }).join("");
    events.forEach((event) => seenEvents.add(event.event_id));
    if (seenEvents.size > 2000) { seenEvents.clear(); events.forEach((event) => seenEvents.add(event.event_id)); }
  }

  function render() {
    indexSnapshot();
    const agents = state.agents;
    const healthy = agents.filter((a) => health(a) === "healthy").length;
    const issues = agents.filter((a) => health(a) === "unhealthy").length;
    const verified = state.outcomes.filter((o) => o.status === "verified").length;
    const simulated = agents.filter((a) => a.mode === "simulated").length;
    $("metric-healthy").textContent = healthy;
    $("metric-total").textContent = `/ ${agents.length}`;
    $("metric-incidents").textContent = issues;
    $("metric-replays").textContent = verified;
    $("metric-recipes").textContent = state.recipes.length;
    $("metric-health-note").textContent = !agents.length ? "Waiting for endpoint telemetry" : healthy === agents.length ? "All connected endpoints report healthy" : `${agents.length - healthy} endpoint${agents.length - healthy === 1 ? "" : "s"} need attention or telemetry`;
    $("metric-replay-note").textContent = state.outcomes.length ? `${verified} verified / ${state.outcomes.length} recorded attempts` : "Functional checks, not assumed success";
    $("metric-recipe-note").textContent = state.recipes.length ? `${state.recipes.filter((r) => r.status === "validated").length} validated · ${state.recipes.filter((r) => r.status === "paused").length} paused` : "Evidence-backed repair candidates";
    $("mode-label").textContent = simulated ? `LOCAL REHEARSAL · ${simulated} SIMULATED ENDPOINT${simulated === 1 ? "" : "S"}` : agents.length ? "NATIVE ENDPOINT NETWORK" : "ENDPOINT NETWORK · WAITING";
    const time = timeLabel(new Date());
    $("snapshot-time").textContent = `Updated ${time}`;
    $("snapshot-time").dataset.time = time;
    fillSelectors(agents);
    renderTopology();
    renderAgents();
    renderRecipes();
    renderEvents();
    updateControls();
  }

  async function poll() {
    if (polling || awaitingAuth) return;
    polling = true;
    try {
      const result = await api("/api/state");
      if (!validState(result)) throw new Error("The coordinator returned an invalid fleet snapshot.");
      state = result;
      setConnection(true);
      render();
      $("access-error").hidden = true;
    } catch (error) {
      if (error.status === 401 || error.status === 403) {
        awaitingAuth = true;
        setConnection(false, "Authentication required. Enter the coordinator token to view the fleet.");
        $("access-error").textContent = token ? "This token was not accepted. Check it and reconnect." : "This coordinator requires an access token.";
        $("access-error").hidden = false;
        accessDialog();
      } else {
        setConnection(false, state ? "Connection lost. Showing the last received snapshot; controls are disabled until reconnection." : "Cannot reach the coordinator. Start it, then retry the connection.");
      }
    } finally {
      polling = false;
      clearTimeout(timer);
      timer = setTimeout(poll, connected ? 1000 : 3000);
    }
  }

  async function action(path, payload, message) {
    if (busy || !connected) return;
    busy = true;
    updateControls();
    try {
      await api(path, payload);
      toast(message);
      await poll();
    } catch (error) {
      toast(error.message || "The command could not be queued.", true);
      if (error.status === 401 || error.status === 403) {
        awaitingAuth = true;
        setConnection(false, "Authentication required. Enter the coordinator token to continue.");
        $("access-error").textContent = "The coordinator did not accept this request. Check the access token.";
        $("access-error").hidden = false;
        accessDialog();
      } else if (!error.status) {
        // The request may have reached the server. Reconnect before enabling another command.
        setConnection(false, "Connection interrupted during the command. Its result is unknown; reconnect before retrying.");
      }
    } finally {
      busy = false;
      updateControls();
    }
  }

  $("fault-button").addEventListener("click", () => action("/api/demo/fault", {agent_ids:[]}, "Fault injection queued for all endpoints."));
  $("repair-button").addEventListener("click", () => {
    const id = $("source-select").value;
    if (id) action("/api/demo/repair", {agent_ids:[id]}, `Human repair queued for ${labelFor(id)}. Watching for recovery.`);
  });
  $("failure-button").addEventListener("click", () => {
    const id = $("failure-select").value;
    if (id) action("/api/demo/fail-verification", {agent_ids:[id]}, `Verification failure queued for ${labelFor(id)}.`);
  });
  $("reset-button").addEventListener("click", () => action("/api/demo/reset", {}, "Demo reset queued. Waiting for endpoint acknowledgements."));
  $("pause-button").addEventListener("click", () => action("/api/control/pause", {paused:!state?.paused}, state?.paused ? "Rollout resumed." : "Rollout paused. No new repairs will be assigned."));
  $("access-button").addEventListener("click", accessDialog);
  $("close-dialog").addEventListener("click", () => $("access-dialog").close());
  $("retry-button").addEventListener("click", () => { if (awaitingAuth) accessDialog(); else { clearTimeout(timer); poll(); } });
  $("access-form").addEventListener("submit", (event) => {
    event.preventDefault();
    token = $("access-token").value.trim();
    try { if (token) sessionStorage.setItem("swarm-token",token); else sessionStorage.removeItem("swarm-token"); } catch (_) { /* Memory-only fallback. */ }
    awaitingAuth = false;
    $("access-dialog").close();
    clearTimeout(timer);
    poll();
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !awaitingAuth) { clearTimeout(timer); poll(); } });
  poll();
})();
