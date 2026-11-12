/* Browser-only illustration. No fleet API calls, agents, or repair commands. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const model = window.SwarmMockModel;
  const canvas = $("fleet-graph");
  const ctx = canvas.getContext("2d");
  const colors = { healthy: "#00a49b", issue: "#f77a66", incompatible: "#9262b5", offline: "#9aa9bc" };
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let fleet, positions = [], selected = 17, path = [], width = 0, height = 0;
  let progress = 1, paused = false, frame = null, lastTime = null;

  function text(id, value) { $(id).textContent = value; }
  function selectedIndex(id) { return Number($(id).value); }
  function number(value) { return value.toLocaleString("en-US"); }

  function layout() {
    const bounds = canvas.getBoundingClientRect();
    width = bounds.width;
    height = bounds.height;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const outer = Math.min(width * .41, height * .40);
    const groupSize = fleet.count / 16;
    const columns = Math.ceil(Math.sqrt(groupSize));
    const rows = Math.ceil(groupSize / columns);
    positions = fleet.nodes.map((node) => {
      const group = Math.floor(node.index / groupSize);
      const local = node.index % groupSize;
      const row = Math.floor(local / columns);
      const column = local % columns;
      const angle = -Math.PI / 2 + (group + .5 + (column - (columns - 1) / 2) / columns * .78) * Math.PI / 8;
      const radius = outer * (.72 + .28 * (rows === 1 ? 1 : row / (rows - 1)));
      return { x: width / 2 + Math.cos(angle) * radius, y: height / 2 + Math.sin(angle) * radius, angle };
    });
    draw();
  }

  function line(a, b, color, lineWidth = 1, dashed = false, bend = .18) {
    const dx = b.x - a.x, dy = b.y - a.y;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.quadraticCurveTo((a.x + b.x) / 2 - dy * bend, (a.y + b.y) / 2 + dx * bend, b.x, b.y);
    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.setLineDash(dashed ? [3, 6] : []);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function dot(point, radius, color, border = null) {
    ctx.beginPath();
    ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    if (border) { ctx.strokeStyle = border; ctx.lineWidth = 1.5; ctx.stroke(); }
  }

  function draw() {
    if (!width || !height || !fleet) return;
    ctx.clearRect(0, 0, width, height);
    const center = { x: width / 2, y: height / 2 };
    const outer = Math.min(width * .41, height * .40);
    const groupSize = fleet.count / 16;
    const small = width < 500;
    const radius = fleet.count === 1024 ? (small ? 1.35 : 2.4) : (small ? 2.2 : 3.6);

    for (const scale of [.47, .68, 1.10]) {
      ctx.beginPath(); ctx.arc(center.x, center.y, outer * scale, 0, Math.PI * 2);
      ctx.strokeStyle = "#e4edf6"; ctx.lineWidth = 1; ctx.setLineDash([2, 5]); ctx.stroke(); ctx.setLineDash([]);
    }
    for (let group = 0; group < 16; group++) {
      const angle = -Math.PI / 2 + (group + .5) * Math.PI / 8;
      const start = -Math.PI / 2 + (group + .08) * Math.PI / 8;
      const end = -Math.PI / 2 + (group + .92) * Math.PI / 8;
      ctx.beginPath(); ctx.arc(center.x, center.y, outer * 1.055, start, end);
      ctx.strokeStyle = ["#a9cfed", "#afdcd5", "#c5c8ec", "#b6d4e8"][Math.floor(group / 4)];
      ctx.lineWidth = 3; ctx.stroke();
      if ($("coordinator-links").checked) {
        line(center, { x: center.x + Math.cos(angle) * outer * .68, y: center.y + Math.sin(angle) * outer * .68 }, "#9fb5ce80", 1, true, 0);
      }
      const labelRadius = outer * 1.055 + (small ? 14 : 21);
      ctx.font = `${small ? 9 : 11}px ui-monospace, monospace`;
      ctx.fillStyle = "#63778f"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(`${group.toString(4).padStart(2, "0")}…`, center.x + Math.cos(angle) * labelRadius, center.y + Math.sin(angle) * labelRadius);
    }

    if ($("peer-links").checked) {
      // A readable subset of real model contacts, never an all-to-all mesh.
      for (let index = 0; index < fleet.count; index++) {
        const next = (index + 1) % fleet.count;
        line(positions[index], positions[next], "#b4cfd485", .7, false, 0);
      }
      for (let group = 0; group < 16; group++) {
        const index = group * groupSize;
        for (const peer of fleet.contacts[index]) {
          if (Math.floor(peer / groupSize) !== group) line(positions[index], positions[peer], "#92b9d32b", .65);
        }
      }
      for (const peer of fleet.contacts[selected]) line(positions[selected], positions[peer], "#4c93d880", 1.1);
    }

    for (const node of fleet.nodes) dot(positions[node.index], radius, colors[node.status]);
    const routeEdges = Math.max(path.length - 1, 0);
    for (let index = 0; index < routeEdges; index++) {
      line(positions[path[index]], positions[path[index + 1]], "#1046a82a", 3);
      if (index < Math.floor(progress * routeEdges) || progress === 1) line(positions[path[index]], positions[path[index + 1]], "#1046a8", 2.5);
    }
    for (let index = 0; index < path.length; index++) {
      const point = positions[path[index]];
      dot(point, radius + 3, "#ffffff", "#1046a8");
      dot(point, radius, "#1046a8");
    }
    if (progress < 1 && routeEdges) {
      const traveled = progress * routeEdges;
      const segment = Math.min(Math.floor(traveled), routeEdges - 1);
      const amount = traveled - segment;
      const a = positions[path[segment]], b = positions[path[segment + 1]];
      const control = { x: (a.x + b.x) / 2 - (b.y - a.y) * .18, y: (a.y + b.y) / 2 + (b.x - a.x) * .18 };
      const p = { x: (1 - amount) ** 2 * a.x + 2 * (1 - amount) * amount * control.x + amount ** 2 * b.x,
        y: (1 - amount) ** 2 * a.y + 2 * (1 - amount) * amount * control.y + amount ** 2 * b.y };
      dot(p, 10, "#3194f733"); dot(p, 4, "#1046a8", "white");
    }
    dot(positions[selected], radius + 7, "#ffffff00", "#1046a8");
    canvas.dataset.nodeCount = String(fleet.count);
    canvas.dataset.route = path.map((index) => fleet.nodes[index].id).join(",");
  }

  function inspect(index) {
    selected = index;
    const node = fleet.nodes[index];
    text("selected-label", node.label);
    text("selected-id", node.id);
    text("selected-status", node.status === "issue" ? "Issue detected" : node.status);
    $("selected-status").dataset.status = node.status;
    text("selected-prefix", `${node.prefix}…`);
    text("selected-contacts", fleet.contacts[index].length);
    draw();
  }

  function cancelAnimation() {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null; lastTime = null; paused = false;
    $("pause-route").disabled = true;
    text("pause-route", "Pause trace");
  }

  function routeDescription() {
    const source = fleet.nodes[path[0]], target = fleet.nodes[path[path.length - 1]];
    return `${source.id} → ${target.id}: ${path.length - 1} peer hops. Coordinator is not a routing hop.`;
  }

  function animate(timestamp) {
    if (paused) return;
    if (lastTime !== null) progress = Math.min(1, progress + Math.min(timestamp - lastTime, 100) / 2400);
    lastTime = timestamp; draw();
    if (progress < 1) frame = requestAnimationFrame(animate);
    else { cancelAnimation(); text("route-status", `Illustrated route complete. ${routeDescription()}`); }
  }

  function setRoute(animateRoute = false) {
    cancelAnimation();
    path = model.route(fleet, selectedIndex("route-source"), selectedIndex("route-target"));
    progress = animateRoute && !reducedMotion.matches && path.length > 1 ? 0 : 1;
    text("metric-hops", path.length - 1);
    const items = path.map((index, step) => {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button"; button.textContent = fleet.nodes[index].id;
      button.setAttribute("aria-label", `Inspect route step ${step + 1}, device ${fleet.nodes[index].id}`);
      button.addEventListener("click", () => inspect(index));
      const description = document.createElement("span");
      description.textContent = step === 0 ? "Source" : `${model.matchingPrefix(fleet.nodes[index].id, fleet.nodes[path[path.length - 1]].id)} matching digits${step === path.length - 1 ? " · destination" : ""}`;
      li.append(button, description); return li;
    });
    $("route-steps").replaceChildren(...items);
    text("route-status", progress < 1 ? `Tracing synthetic route. ${routeDescription()}` : `Synthetic route. ${routeDescription()}`);
    inspect(path[0]);
    if (progress < 1) { $("pause-route").disabled = false; frame = requestAnimationFrame(animate); }
    draw();
  }

  function rebuild() {
    cancelAnimation();
    fleet = model.createFleet(Number($("fleet-size").value));
    selected = 17;
    for (const id of ["route-source", "route-target"]) {
      const options = fleet.nodes.map((node) => {
        const option = document.createElement("option");
        option.value = String(node.index); option.textContent = `${node.id} · ${node.label}`; return option;
      });
      $(id).replaceChildren(...options);
    }
    $("route-source").value = "17";
    $("route-target").value = String(fleet.count - 8);
    text("metric-nodes", number(fleet.count));
    text("metric-contacts", fleet.stats.maxContacts);
    text("metric-links", number(fleet.stats.directedContacts));
    text("comparison-note", `${number(fleet.stats.directedContacts)} directed peer contacts in this model, versus ${number(fleet.stats.allToAllContacts)} in an all-to-all topology. Coordinator membership adds ${number(fleet.count)} logical device relationships. Counts describe this generated graph, not measured network traffic.`);
    text("graph-caption", `${number(fleet.count)} synthetic devices · 16 ID-prefix groups · selected contacts and a subset of peer links shown`);
    // Clear the previous size's path before drawing the new fleet.
    path = []; progress = 1; layout(); setRoute();
  }

  canvas.addEventListener("click", (event) => {
    const bounds = canvas.getBoundingClientRect();
    const x = event.clientX - bounds.left, y = event.clientY - bounds.top;
    let closest = null, distance = 18;
    for (let index = 0; index < positions.length; index++) {
      const current = Math.hypot(positions[index].x - x, positions[index].y - y);
      if (current < distance) { closest = index; distance = current; }
    }
    if (closest !== null) inspect(closest);
  });
  $("fleet-size").addEventListener("change", rebuild);
  $("trace-route").addEventListener("click", () => setRoute(true));
  $("reset-view").addEventListener("click", () => {
    $("peer-links").checked = true; $("coordinator-links").checked = true; rebuild();
  });
  $("pause-route").addEventListener("click", () => {
    paused = !paused;
    text("pause-route", paused ? "Resume trace" : "Pause trace");
    text("route-status", `${paused ? "Paused" : "Tracing"} synthetic route. ${routeDescription()}`);
    if (paused) { if (frame !== null) cancelAnimationFrame(frame); frame = null; }
    else { lastTime = null; frame = requestAnimationFrame(animate); }
  });
  for (const id of ["peer-links", "coordinator-links"]) $(id).addEventListener("change", draw);
  for (const id of ["route-source", "route-target"]) $(id).addEventListener("change", () => setRoute());
  $("use-source").addEventListener("click", () => { $("route-source").value = String(selected); setRoute(); });
  $("use-target").addEventListener("click", () => { $("route-target").value = String(selected); setRoute(); });
  reducedMotion.addEventListener("change", () => {
    if (reducedMotion.matches) { cancelAnimation(); progress = 1; draw(); text("route-status", `Synthetic route. ${routeDescription()}`); }
  });
  rebuild();
  new ResizeObserver(layout).observe(canvas);
  window.addEventListener("pagehide", cancelAnimation);
})();
