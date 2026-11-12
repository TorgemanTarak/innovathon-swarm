// Pure synthetic topology behavior. These are not browser or network tests.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const modelPath = path.join(__dirname, "../src/swarm/web/mock-fleet-model.js");
const {SIZES, createFleet, route, matchingPrefix} = require(modelPath);

for (const count of SIZES) {
  test(`${count} synthetic devices have bounded, unique prefix and leaf contacts`, () => {
    const fleet = createFleet(count);
    assert.equal(fleet.nodes.length, count);
    assert.equal(new Set(fleet.nodes.map((node) => node.id)).size, count);
    assert.equal(new Set(fleet.nodes.map((node) => node.prefix)).size, 16);
    for (const node of fleet.nodes) {
      assert.match(node.id, new RegExp(`^[0-3]{${fleet.width}}$`));
      assert.equal(parseInt(node.id, 4), node.index);
      const peers = fleet.contacts[node.index];
      assert.equal(new Set(peers).size, peers.length);
      assert.ok(!peers.includes(node.index));
      assert.ok(peers.length <= 4 + 3 * fleet.width);
      for (const peer of peers) assert.ok(Number.isInteger(peer) && peer >= 0 && peer < count);
      for (const offset of [-2, -1, 1, 2]) assert.ok(peers.includes((node.index + offset + count) % count));
      for (let depth = 0; depth < fleet.width; depth += 1) {
        for (const digit of "0123") {
          if (digit === node.id[depth]) continue;
          assert.ok(peers.some((peer) => fleet.nodes[peer].id.startsWith(node.id.slice(0, depth) + digit)));
        }
      }
    }
    assert.equal(fleet.stats.directedContacts, fleet.contacts.reduce((sum, peers) => sum + peers.length, 0));
    assert.equal(fleet.stats.allToAllContacts, count * (count - 1));
    assert.equal(fleet.stats.averageContacts, fleet.stats.directedContacts / count);
    assert.ok(fleet.stats.directedContacts < fleet.stats.allToAllContacts);
  });

  test(`${count} devices route using existing contacts with strictly increasing prefix matches`, () => {
    const fleet = createFleet(count);
    // Exhaustive at 64; every source against 32 dispersed targets at larger sizes.
    const step = count === 64 ? 1 : count / 32;
    for (let source = 0; source < count; source += 1) {
      for (let target = 0; target < count; target += step) {
        const hops = route(fleet, source, target);
        assert.equal(hops[0], source);
        assert.equal(hops.at(-1), target);
        assert.equal(new Set(hops).size, hops.length);
        assert.ok(hops.length - 1 <= fleet.width);
        for (let index = 1; index < hops.length; index += 1) {
          assert.ok(fleet.contacts[hops[index - 1]].includes(hops[index]));
          assert.ok(matchingPrefix(fleet.nodes[hops[index]].id, fleet.nodes[target].id)
            > matchingPrefix(fleet.nodes[hops[index - 1]].id, fleet.nodes[target].id));
        }
      }
    }
  });
}

test("topology and illustrative statuses are deterministic, with no shared mutation", () => {
  const first = createFleet();
  const second = createFleet();
  assert.deepEqual(first, second);
  assert.deepEqual(new Set(first.nodes.map((node) => node.status)), new Set(["healthy", "issue", "incompatible", "offline"]));
  first.nodes[0].status = "issue";
  first.contacts[0].pop();
  assert.equal(second.nodes[0].status, "healthy");
  assert.notDeepEqual(first.contacts[0], second.contacts[0]);
});

test("self routing is zero hops and invalid input is rejected", () => {
  const fleet = createFleet(64);
  assert.deepEqual(route(fleet, 63, 63), [63]);
  for (const count of [0, 1, 63, 128, 2048, "256", null, Infinity]) assert.throws(() => createFleet(count), RangeError);
  for (const index of [-1, 64, 0.5, "1", null, Infinity]) {
    assert.throws(() => route(fleet, index, 1), RangeError);
    assert.throws(() => route(fleet, 1, index), RangeError);
  }
  assert.throws(() => route(null, 0, 1), TypeError);
  fleet.contacts[0] = [];
  assert.throws(() => route(fleet, 0, 63), /cannot make prefix progress/);
});

test("browser global exposes the same model without CommonJS or network access", () => {
  const context = vm.createContext({});
  vm.runInContext(fs.readFileSync(modelPath, "utf8"), context);
  assert.equal(context.SwarmMockModel.createFleet(1024).nodes.length, 1024);
  assert.equal(context.SwarmMockModel.route(context.SwarmMockModel.createFleet(64), 0, 63).at(-1), 63);
});
