/* Synthetic, static membership for the scale lab. Inspired by Pastry's prefix
 * routing and leaf sets; not a Pastry implementation or a production benchmark.
 * No sockets, live fleet state, discovery, churn handling or repair execution. */
(function (root, factory) {
  const model = factory();
  if (typeof module === "object" && module.exports) module.exports = model;
  else root.SwarmMockModel = model;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  const SIZES = Object.freeze([64, 256, 1024]);

  function matchingPrefix(left, right) {
    let length = 0;
    while (length < Math.min(left.length, right.length) && left[length] === right[length]) length += 1;
    return length;
  }

  function createFleet(count = 256) {
    if (!SIZES.includes(count)) throw new RangeError("Mock fleet size must be 64, 256 or 1024.");
    const width = Math.log(count) / Math.log(4);
    const nodes = Array.from({length: count}, (_, index) => {
      const id = index.toString(4).padStart(width, "0");
      const status = index % 47 === 11 ? "offline" : index % 23 === 9 ? "incompatible" : index % 7 === 2 ? "issue" : "healthy";
      return {index, id, prefix: id.slice(0, 2), label: `Endpoint ${String(index + 1).padStart(4, "0")}`, status};
    });
    const contacts = nodes.map((node) => {
      const peers = new Set();
      // Nearby IDs form the illustrative leaf set, wrapping around the ID ring.
      for (const offset of [-2, -1, 1, 2]) peers.add((node.index + offset + count) % count);
      // One contact for every alternative digit at each prefix depth. Keeping
      // the suffix deterministic makes this complete synthetic ID space easy to
      // inspect; real Pastry chooses among live, possibly sparse members.
      for (let depth = 0; depth < width; depth += 1) {
        for (const digit of "0123") {
          if (digit === node.id[depth]) continue;
          peers.add(parseInt(node.id.slice(0, depth) + digit + node.id.slice(depth + 1), 4));
        }
      }
      return [...peers].sort((left, right) => left - right);
    });
    const directedContacts = contacts.reduce((total, peers) => total + peers.length, 0);
    const allToAllContacts = count * (count - 1);
    return {
      count, width, nodes, contacts,
      stats: {
        directedContacts,
        allToAllContacts,
        maxContacts: Math.max(...contacts.map((peers) => peers.length)),
        averageContacts: directedContacts / count,
        contactRatio: directedContacts / allToAllContacts,
      },
    };
  }

  function route(fleet, sourceIndex, targetIndex) {
    if (!fleet || !SIZES.includes(fleet.count) || fleet.nodes?.length !== fleet.count || fleet.contacts?.length !== fleet.count) {
      throw new TypeError("Expected a generated mock fleet.");
    }
    for (const index of [sourceIndex, targetIndex]) {
      if (!Number.isInteger(index) || index < 0 || index >= fleet.count) throw new RangeError("Route endpoint is outside the mock fleet.");
    }
    const target = fleet.nodes[targetIndex].id;
    const path = [sourceIndex];
    let current = sourceIndex;
    // Display statuses are illustrative metadata. This idealized routing model
    // assumes all topology members are reachable; it does not simulate outages.
    while (current !== targetIndex) {
      let best = current;
      let progress = matchingPrefix(fleet.nodes[current].id, target);
      for (const neighbor of fleet.contacts[current]) {
        const prefix = matchingPrefix(fleet.nodes[neighbor].id, target);
        if (prefix > progress) {
          best = neighbor;
          progress = prefix;
        }
      }
      if (best === current || path.length > fleet.width) throw new Error("Mock topology cannot make prefix progress.");
      current = best;
      path.push(current);
    }
    return path;
  }

  return {SIZES, createFleet, route, matchingPrefix};
});
