"use strict";

// Anchors the 「谱系」item-tree column prototype (A2).
//
// The logic under test lives in plugin/src/columns/lineageCore.mjs, which is
// plain ESM with no Zotero/DOM globals, so it runs here with node built-ins
// only. renderCell itself needs a Document and stays in the 真机 acceptance
// list; the DOM-independent half it relies on (what to draw, the click
// payload) is covered below. The registration wiring in TS is locked by
// source-text assertions, the same convention tests/test_plugin_hooks.cjs
// established (ADR 0010).

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const PLUGIN_DIR = path.join(__dirname, "..", "plugin");
const CORE_URL = pathToFileURL(
  path.join(PLUGIN_DIR, "src", "columns", "lineageCore.mjs"),
).href;

async function loadCore() {
  return import(CORE_URL);
}

/**
 * Minimal stand-in for a Zotero.Item as the row ref the column sees. It
 * carries `_objectType: "item"` because that is what distinguishes a real
 * item from a collection or saved search (collection.js:39, search.js:44,
 * item.js:99) — a bare `{ key }` is deliberately *not* proven to be an item.
 */
function item(key, libraryID = 1) {
  return { _objectType: "item", key, libraryID };
}

function fakeManager() {
  const calls = { register: [], unregister: [] };
  let nextKey = 0;
  return {
    calls,
    registerColumn(option) {
      calls.register.push(option);
      nextKey += 1;
      return `littrace@example.invalid-${option.dataKey}-${nextKey}`;
    },
    unregisterColumn(dataKey) {
      calls.unregister.push(dataKey);
      return true;
    },
  };
}

/**
 * A2 shipped a deterministic fake lineage provider. Since U1 the column reads
 * the local service, so the fixture lives here as a test double instead of in
 * the shipped module: the badge mapping, the text contract and the item-row
 * guard stay observable without a service, exactly as the A2 cases required.
 * No shipped module imports any of this.
 */
const FAKE_FIXTURES = Object.freeze([
  Object.freeze({ confirmed: 5, pending: 0, gaps: 0 }),
  Object.freeze({ confirmed: 2, pending: 2, gaps: 0 }),
  Object.freeze({ confirmed: 1, pending: 0, gaps: 1 }),
  Object.freeze({ confirmed: 0, pending: 0, gaps: 0 }),
  Object.freeze({ confirmed: 3, pending: 1, gaps: 0 }),
]);

/** FNV-1a, stable across sessions: same key -> same fixture. */
function stableHash(text) {
  const source = typeof text === "string" ? text : "";
  let hash = 0x811c9dc5;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

/** Fake lineage record for an item key; deterministic, empty for no key. */
function fakeLineageFor(key) {
  const source = typeof key === "string" ? key : "";
  if (source.length === 0) {
    return { confirmed: 0, pending: 0, gaps: 0 };
  }
  return { ...FAKE_FIXTURES[stableHash(source) % FAKE_FIXTURES.length] };
}

/** The A2 provider, rebuilt over the core's real guard. */
function fakeLineageProvider(core) {
  return core.createDataProvider((ref) =>
    core.isItemRef(ref)
      ? fakeLineageFor(core.itemKeyOf(ref))
      : { confirmed: 0, pending: 0, gaps: 0 },
  );
}

test("the three-state table is a fixed precedence: gap > pending > verified > empty", async () => {
  const core = await loadCore();
  const cases = [
    [{ confirmed: 3, pending: 0, gaps: 0 }, "verified"],
    [{ confirmed: 0, pending: 2, gaps: 0 }, "pending"],
    [{ confirmed: 4, pending: 3, gaps: 0 }, "pending"],
    [{ confirmed: 0, pending: 0, gaps: 1 }, "gap"],
    [{ confirmed: 9, pending: 9, gaps: 1 }, "gap"],
    [{ confirmed: 0, pending: 0, gaps: 0 }, "empty"],
    [{}, "empty"],
  ];
  for (const [counts, expected] of cases) {
    assert.equal(
      core.badgeState(counts),
      expected,
      `${JSON.stringify(counts)} must map to ${expected}`,
    );
  }
});

test("badge states carry distinct symbols, classes and colours", async () => {
  const core = await loadCore();
  const verified = core.stateBadge("verified");
  const pending = core.stateBadge("pending");
  const gap = core.stateBadge("gap");

  assert.equal(verified.symbol, "●", "verified is a filled dot");
  assert.equal(pending.symbol, "○", "pending is a hollow ring");
  assert.equal(gap.symbol, "⚠", "gap is a warning mark");
  assert.deepEqual(core.LINEAGE_STATES, ["verified", "pending", "gap"]);

  const classes = [verified.className, pending.className, gap.className];
  assert.equal(new Set(classes).size, 3, "each state needs its own class");
  const colors = [verified.color, pending.color, gap.color];
  assert.equal(new Set(colors).size, 3, "each state needs its own colour");

  assert.equal(core.stateBadge("empty"), null, "empty renders nothing");
  assert.equal(core.stateBadge("Verified"), null, "states are case-sensitive");
  assert.equal(core.stateBadge(undefined), null);
});

test("counters are sanitised: garbage never becomes a badge", async () => {
  const core = await loadCore();
  const malformed = [
    null,
    undefined,
    0,
    "verified",
    [],
    { confirmed: -3, pending: NaN, gaps: Infinity },
    { confirmed: "4", pending: "x", gaps: null },
    Object.create(null),
  ];
  for (const record of malformed) {
    const counts = core.normalizeCounts(record);
    for (const key of ["confirmed", "pending", "gaps"]) {
      assert.ok(
        Number.isInteger(counts[key]) && counts[key] >= 0,
        `${key} must be a non-negative integer, got ${counts[key]}`,
      );
    }
  }
  assert.deepEqual(core.normalizeCounts({ confirmed: "4" }), {
    confirmed: 4,
    pending: 0,
    gaps: 0,
  });
});

test("dataProvider is empty for every empty / error / offline input", async () => {
  const core = await loadCore();
  const offline = () => undefined;
  const fake = fakeLineageProvider(core);

  const inputs = [
    null,
    undefined,
    0,
    "",
    [],
    {},
    item(""),
    item(42),
    item("   "),
    item(null),
    { key: "ABCD1234" }, // no object type: not proven to be an item
  ];
  for (const value of inputs) {
    assert.equal(
      fake(value, "lineage"),
      "",
      `item ${JSON.stringify(value)} must yield an empty cell`,
    );
  }
  // A resolver that throws (offline service, schema drift) must not escape:
  // dataProvider is called while the item list renders.
  const throwing = core.createDataProvider(() => {
    throw new Error("service offline");
  });
  assert.equal(throwing(item("ABCD1234"), "lineage"), "");
  assert.equal(throwing(null, "lineage"), "");
  // And neither may the raw resolver itself be reachable: the provider
  // returns a string for every input, never undefined/null/object.
  const stringProvider = fakeLineageProvider(core);
  for (const value of [...inputs, item("ABCD1234")]) {
    assert.equal(typeof stringProvider(value, "lineage"), "string");
  }
  assert.equal(core.cellText(offline()), "");
});

test("trashed collections and saved searches get no badge and no locate payload", async () => {
  const core = await loadCore();
  const fake = fakeLineageProvider(core);
  // Zotero.Collection (collection.js:39) and Zotero.Search (search.js:44)
  // both carry a `.key`, so the trash view would otherwise show a fake badge
  // for them and dispatch a locate payload carrying a non-item key that U4
  // cannot resolve (PR #23 finding, plugin/src/columns/lineageCore.mjs:248).
  const collection = { _objectType: "collection", key: "COLL1234", name: "已删除分类" };
  const search = { _objectType: "search", key: "SEARCH12", name: "已删除检索" };
  const header = { _objectType: "header" };
  const spacer = { _objectType: "spacer" };

  for (const ref of [collection, search, header, spacer]) {
    assert.equal(core.isItemRef(ref), false, `${ref._objectType} is not an item`);
    assert.equal(core.itemKeyOf(ref), "", "a non-item has no item key");
    assert.equal(
      fake(ref, "lineage"),
      "",
      "a non-item row must render a blank cell, not a fake badge",
    );
  }

  // A real item is unaffected, including one whose row also carries extras.
  assert.equal(core.isItemRef(item("ABCD1234")), true);
  assert.equal(core.itemKeyOf(item("ABCD1234")), "ABCD1234");
  assert.notEqual(fake(item("ABCD1234"), "lineage"), "");

  // Without _objectType the item-only API decides: Collection and Search
  // define neither isRegularItem nor getField.
  const bareItem = { key: "ABCD1234", isRegularItem: () => true, getField: () => "" };
  assert.equal(core.isItemRef(bareItem), true);
  assert.equal(core.isItemRef({ key: "X", isRegularItem: () => false }), false);
  assert.equal(core.isItemRef({ key: "X", getField: () => "" }), false);
  assert.equal(core.isItemRef({ _objectType: "item", key: "K" }), true);
  assert.equal(core.isItemRef({ _objectType: "item", key: "K" }).toString(), "true");
  assert.equal(core.itemKeyOf({ _objectType: "item", key: 7 }), "", "a non-string key is not usable");
});

test("the same item always produces the same text", async () => {
  const core = await loadCore();
  const fake = fakeLineageProvider(core);
  const first = item("ITEMKEY1", 1);
  const second = item("ITEMKEY1", 1);
  const firstText = fake(first, "lineage");
  assert.notEqual(firstText, "", "a real item must produce fake lineage text");
  assert.equal(fake(second, "lineage"), firstText);
  assert.equal(fake(first, "lineage"), firstText);
  assert.equal(fake(first, "other-data-key"), firstText);
  assert.equal(stableHash("ITEMKEY1"), stableHash("ITEMKEY1"));
  assert.notEqual(stableHash("ITEMKEY1"), stableHash("ITEMKEY2"));
  assert.equal(stableHash(undefined), stableHash(""));
});

test("a library of fake items shows all four outcomes, badges included", async () => {
  const core = await loadCore();
  const seen = new Set();
  const byState = new Map();
  for (let index = 0; index < 200; index += 1) {
    const key = `FAKE${index}`;
    const counts = fakeLineageFor(key);
    const state = core.badgeState(counts);
    const text = fakeLineageProvider(core)(item(key), "lineage");
    seen.add(state);
    // Every text that is not blank must round-trip back to the same counts,
    // so renderCell can rebuild exactly the badge the dataProvider meant.
    if (text !== "") {
      assert.deepEqual(core.parseCellText(text), counts, `text for ${key} must round-trip`);
    }
    if (!byState.has(state)) {
      byState.set(state, text);
    }
  }
  assert.deepEqual(
    [...seen].sort(),
    ["empty", "gap", "pending", "verified"],
    "the prototype must be observable in all three badge states plus blank",
  );
  for (const state of ["verified", "pending", "gap"]) {
    assert.notEqual(byState.get(state), "", `${state} needs a non-empty text`);
  }
  assert.equal(byState.get("empty"), "", "the empty state exports an empty cell");
});

test("cellText and parseCellText round-trip; anything else parses to null", async () => {
  const core = await loadCore();
  const counts = [{ confirmed: 5, pending: 0, gaps: 0 }, { confirmed: 2, pending: 2, gaps: 1 }];
  for (const value of counts) {
    assert.deepEqual(core.parseCellText(core.cellText(value)), value);
  }
  assert.equal(core.cellText({ confirmed: 0, pending: 0, gaps: 0 }), "");
  assert.equal(core.cellText(null), "");
  assert.equal(core.cellText({ confirmed: 5, pending: 0, gaps: 0 }), "5 已确认 · 0 待核 · 0 缺口");

  for (const text of [null, undefined, 5, "", "5", "5 已确认", "5 已确认 · 0 待核", "a 已确认 · b 待核 · c 缺口"]) {
    assert.equal(core.parseCellText(text), null, `must not parse ${JSON.stringify(text)}`);
  }
});

test("badgeForText derives the badge from the exported text alone", async () => {
  const core = await loadCore();
  const text = core.cellText({ confirmed: 3, pending: 1, gaps: 0 });
  const badge = core.badgeForText(text);
  assert.equal(badge.state, "pending");
  assert.equal(badge.symbol, "○");
  assert.deepEqual(badge.counts, { confirmed: 3, pending: 1, gaps: 0 });
  assert.equal(badge.title, "已确认 3 条 · 待核 1 条");

  assert.equal(core.badgeForText(""), null);
  assert.equal(core.badgeForText("0 已确认 · 0 待核 · 0 缺口"), null);
  assert.equal(core.badgeForText(null), null);
});

test("the count next to the badge is always the confirmed relation count", async () => {
  const core = await loadCore();
  // Issue #22 需求 3: 「三态徽标 + 已确认关系数」. A pending item shows its
  // *confirmed* count (`○ 3`), not its pending count — the state is carried by
  // the glyph and the full breakdown by the title, so the number means the
  // same thing in every row (PR #23 finding, plugin/src/columns/lineage.ts:70).
  const counts = { confirmed: 3, pending: 1, gaps: 0 };
  assert.equal(core.badgeForText(core.cellText(counts)).state, "pending");
  assert.equal(core.relationCount(counts), 3, "pending rows still show confirmed");
  assert.equal(core.relationCount({ confirmed: 4, pending: 2, gaps: 9 }), 4);
  assert.equal(core.relationCount({ confirmed: 0, pending: 2, gaps: 0 }), 0);
  assert.equal(core.relationCount({ confirmed: 0, pending: 0, gaps: 1 }), 0);
  assert.equal(core.relationCount(null), 0);
  assert.equal(core.relationCount({ confirmed: "7" }), 7);

  // Every fixture must render a number that parses back to its confirmed
  // count, so the cell and the exported text cannot disagree.
  for (let index = 0; index < 200; index += 1) {
    const counts_ = fakeLineageFor(`FAKE${index}`);
    if (core.badgeState(counts_) === "empty") {
      continue;
    }
    assert.equal(core.relationCount(counts_), core.parseCellText(core.cellText(counts_)).confirmed);
  }
});

test("the registered option only carries keys registerColumn accepts", async () => {
  const core = await loadCore();
  const option = core.lineageColumnOption("littrace@example.invalid");
  // pluginAPIBase._validateObject throws "Unknown option" for any key outside
  // itemTreeManager's optionTypeDefinition, and registerColumn then returns
  // false. Locking the key set catches an accidental extra key in review.
  assert.deepEqual(Object.keys(option).sort(), [
    "dataKey",
    "enabledTreeIDs",
    "flex",
    "label",
    "minWidth",
    "pluginID",
    "showInColumnPicker",
    "zoteroPersist",
  ]);
  assert.equal(option.dataKey, "lineage");
  assert.equal(option.label, "谱系");
  assert.equal(option.pluginID, "littrace@example.invalid");
  assert.deepEqual(option.enabledTreeIDs, ["main"], "main list only");
  assert.deepEqual(option.zoteroPersist, ["width", "hidden", "sortDirection"]);
  assert.equal("defaultIn" in option, false, "default-off: no defaultIn");
  assert.equal(option.showInColumnPicker, true, "enabled from the column picker");
});

test("registration happens once and disposal uses the returned key", async () => {
  const core = await loadCore();
  const manager = fakeManager();
  const registration = core.createColumnRegistration(
    manager,
    "littrace@example.invalid",
    { dataProvider: () => "" },
  );

  assert.equal(manager.calls.register.length, 1, "exactly one registerColumn");
  assert.equal(registration.dataKey, "littrace@example.invalid-lineage-1");
  assert.equal(manager.calls.register[0].dataProvider(), "");

  assert.equal(registration.dispose(), true);
  assert.deepEqual(manager.calls.unregister, [registration.dataKey], "unregister by the returned key, not the raw dataKey");
  assert.equal(registration.dispose(), false, "second dispose is a no-op");
  assert.equal(manager.calls.unregister.length, 1);
});

test("a rejected or throwing registration degrades to a no-op disposer", async () => {
  const core = await loadCore();

  const rejected = {
    registerColumn: () => false,
    unregisterColumn: () => {
      throw new Error("must not be called");
    },
  };
  const failed = core.createColumnRegistration(rejected, "p");
  assert.equal(failed.dataKey, null);
  assert.equal(failed.dispose(), false);

  const throwing = {
    registerColumn: () => {
      throw new Error("Zotero unavailable");
    },
    unregisterColumn: () => true,
  };
  const broken = core.createColumnRegistration(throwing, "p");
  assert.equal(broken.dataKey, null, "a throw must not escape");
  assert.equal(broken.dispose(), false);

  const failingUnregister = {
    registerColumn: () => "key",
    unregisterColumn: () => {
      throw new Error("already gone");
    },
  };
  assert.equal(core.createColumnRegistration(failingUnregister, "p").dispose(), false);
});

test("the locate payload is only produced for a routable request", async () => {
  const core = await loadCore();
  assert.deepEqual(
    core.createLocatePayload({ itemKey: "ABCD1234", libraryID: 1, state: "pending" }),
    {
      source: "lineage-column",
      itemKey: "ABCD1234",
      libraryID: 1,
      state: "pending",
      tab: "lineage",
      paper: null,
    },
    "the payload names the tab to open; paper stays null when unknown",
  );
  assert.deepEqual(
    core.createLocatePayload({ itemKey: " ABCD1234 ", libraryID: "1", state: "gap" }),
    {
      source: "lineage-column",
      itemKey: "ABCD1234",
      libraryID: null,
      state: "gap",
      tab: "lineage",
      paper: null,
    },
  );
  // U1 需求 5: the payload carries the identity the 谱系 tab passes through as
  // `?paper=`, so the listener needs no second lookup.
  assert.deepEqual(
    core.createLocatePayload({
      itemKey: "ABCD1234",
      libraryID: 2,
      state: "verified",
      paper: " z:2:ABCD1234 ",
    }),
    {
      source: "lineage-column",
      itemKey: "ABCD1234",
      libraryID: 2,
      state: "verified",
      tab: "lineage",
      paper: "z:2:ABCD1234",
    },
  );
  assert.equal(
    core.createLocatePayload({ itemKey: "K", state: "verified", tab: "  " }),
    null,
    "a payload that names no tab cannot be routed",
  );
  assert.equal(core.createLocatePayload({ itemKey: "K", state: "verified", tab: 5 }), null);
  assert.equal(core.createLocatePayload(), null);
  assert.equal(core.createLocatePayload({ itemKey: "", state: "verified" }), null, "no paper, no locate");
  assert.equal(core.createLocatePayload({ itemKey: "K", state: "empty" }), null, "no badge, nothing to locate");
  assert.equal(core.createLocatePayload({ itemKey: "K", state: "bogus" }), null);
});

test("dispatch delivers the payload and unsubscribing stops delivery", async () => {
  const core = await loadCore();
  const target = new fakeEventTarget();
  const payload = core.createLocatePayload({ itemKey: "K1", libraryID: 2, state: "verified" });

  const received = [];
  const unsubscribe = core.listenLineageLocate(target, (detail) => received.push(detail));

  assert.equal(core.dispatchLineageLocate(target, payload), true);
  assert.deepEqual(received, [payload]);
  assert.equal(target.lastEvent.type, core.LINEAGE_LOCATE_EVENT);
  assert.equal(target.lastEvent.bubbles, true, "must bubble to the window");

  unsubscribe();
  assert.equal(core.dispatchLineageLocate(target, payload), true);
  assert.deepEqual(received, [payload], "after unsubscribe nothing is delivered");
});

test("dispatch never throws on a missing target, payload or DOM", async () => {
  const core = await loadCore();
  const payload = core.createLocatePayload({ itemKey: "K1", state: "verified" });
  assert.equal(core.dispatchLineageLocate(null, payload), false);
  assert.equal(core.dispatchLineageLocate({}, payload), false, "target without dispatchEvent");
  assert.equal(core.dispatchLineageLocate(new fakeEventTarget(), null), false);
  assert.equal(core.listenLineageLocate(null, () => {})(), undefined);
  assert.equal(core.listenLineageLocate(new fakeEventTarget(), null)(), undefined);

  const exploding = new fakeEventTarget();
  exploding.dispatchEvent = () => {
    throw new Error("window torn down");
  };
  assert.equal(core.dispatchLineageLocate(exploding, payload), false);
});

test("item refs are read from the tree, never through private globals", async () => {
  const core = await loadCore();
  const ref = item("K");
  assert.equal(core.itemRefOf({ getRow: (index) => (index === 3 ? { ref } : null) }, 3), ref);
  assert.equal(core.itemRefOf({ getRow: () => ({}) }, 0), null, "header/spacer rows have no ref");
  assert.equal(core.itemRefOf({ getRow: () => null }, 0), null);
  assert.equal(
    core.itemRefOf({ getRow: () => { throw new Error("index out of range"); } }, 0),
    null,
  );
  assert.equal(core.itemRefOf(null, 0), null);
  assert.equal(core.itemRefOf({}, 0), null);

  // A collection row reaches this function too, and it must not look like an
  // item: no badge, no locate payload (PR #23 finding, lineageCore.mjs:248).
  assert.equal(
    core.itemRefOf({ getRow: () => ({ ref: { _objectType: "collection", key: "COLL1234" } }) }, 0),
    null,
    "collection rows are not items",
  );

  // renderCell cannot rely on `this`: pluginAPIBase.mjs wraps every option
  // function in an arrow function before storing it, so the ItemTree's
  // `.apply(this, ...)` never reaches our function. The document it is
  // handed is the node that does reach it, and the window owns the tree.
  const fromDocument = core.itemRefOf(undefined, 2, {
    defaultView: { ZoteroPane: { itemsView: { getRow: (index) => (index === 2 ? { ref } : null) } } },
  });
  assert.equal(fromDocument, ref, "the registered renderer is handed doc, not this");
  assert.equal(
    core.itemRefOf(undefined, 2, { defaultView: { ZoteroPane: { itemsView: null } } }),
    null,
    "a window without the item tree must not throw",
  );
  assert.equal(core.itemRefOf(undefined, 2, null), null);
  assert.equal(core.itemRefOf(undefined, 2, { defaultView: {} }), null);
  assert.equal(
    core.itemRefOf(undefined, 2, {
      defaultView: { ZoteroPane: { itemsView: { getRow: () => { throw new Error("gone"); } } } },
    }),
    null,
  );
  // Fallbacks for an embedding without window.ZoteroPane: the local pane and
  // the documented getters. Each is tried until one owns the item tree.
  assert.equal(
    core.itemRefOf(undefined, 0, {
      defaultView: {
        ZoteroPane: null,
        Zotero: { getActiveZoteroPane: () => ({ itemsView: { getRow: () => ({ ref }) } }) },
      },
    }),
    ref,
  );
  assert.equal(
    core.itemRefOf(undefined, 0, {
      defaultView: { Zotero: { getMainWindow: () => ({ ZoteroPane: { itemsView: { getRow: () => ({ ref }) } } }) } },
    }),
    ref,
  );
  assert.equal(
    core.itemRefOf(undefined, 0, {
      defaultView: {
        ZoteroPane: { itemsView: { getRow: () => ({ ref: item("active") }) } },
        ZoteroPane_Local: { itemsView: { getRow: () => ({ ref: item("local") }) } },
      },
    }).key,
    "active",
    "the active pane wins over the fallbacks",
  );
});

test("the column is wired through the plugin's single registration entry", () => {
  const lineage = fs.readFileSync(
    path.join(PLUGIN_DIR, "src", "columns", "lineage.ts"),
    "utf8",
  );
  assert.match(
    lineage,
    /createColumnRegistration\(\s*Zotero\.ItemTreeManager/,
    "must register through Zotero.ItemTreeManager",
  );
  assert.doesNotMatch(
    lineage,
    /from "\.\.\/registry"/,
    "the column must not import the registry: registry -> lineage -> registry would be a cycle",
  );
  assert.match(
    lineage,
    /renderCell:\s*renderLineageCell/,
    "the renderer must be the one this module defines",
  );
  // U1 replaced the A2 fake data. The column must read the shared real-data
  // provider and must not keep a fake one anywhere in its wiring.
  assert.match(
    lineage,
    /dataProvider:\s*\(item: unknown, dataKey: string\) =>\s*paperData\(\)\.lineageProvider\(item, dataKey\)/,
    "「谱系」 reads real data through the shared cache",
  );
  assert.doesNotMatch(
    lineage,
    /fakeLineageDataProvider/,
    "U1 binds real data; the fake provider must not be registered",
  );
  assert.match(lineage, /export function createLineageColumn\(\)/, "the registry builds it");

  const reading = fs.readFileSync(
    path.join(PLUGIN_DIR, "src", "columns", "reading.ts"),
    "utf8",
  );
  assert.match(
    reading,
    /createColumnRegistration\(\s*Zotero\.ItemTreeManager/,
    "「精读」 registers through Zotero.ItemTreeManager too",
  );
  assert.match(
    reading,
    /readingColumnOption\(pluginID\)/,
    "「精读」 uses its own dataKey/label option, not the lineage one",
  );
  assert.match(
    reading,
    /dataProvider:\s*\(item: unknown, dataKey: string\) =>\s*paperData\(\)\.readingProvider\(item, dataKey\)/,
    "「精读」 reads the same shared cache",
  );
  assert.doesNotMatch(reading, /from "\.\.\/registry"/, "no registry cycle");
  assert.match(reading, /export function createReadingColumn\(\)/, "the registry builds it");

  const registry = fs.readFileSync(path.join(PLUGIN_DIR, "src", "registry.ts"), "utf8");
  // The process-wide entry is registerWindowIndependent (shared with the
  // reader events registered by A3); both columns are added there, not in a
  // second parallel "global" entry.
  assert.match(
    registry,
    /export function registerWindowIndependent\(\): void \{[\s\S]*?createLineageColumn\(\)[\s\S]*?registerGlobal\(\s*"item-column:lineage"/,
    "registerWindowIndependent must register the lineage column and hand its disposer to the registry",
  );
  assert.match(
    registry,
    /export function registerWindowIndependent\(\): void \{[\s\S]*?createReadingColumn\(\)[\s\S]*?registerGlobal\(\s*"item-column:reading"/,
    "registerWindowIndependent must register the reading column too",
  );
  assert.doesNotMatch(
    registry,
    /registerGlobalAll/,
    "no second process-wide entry point",
  );

  const hooks = fs.readFileSync(path.join(PLUGIN_DIR, "src", "hooks.ts"), "utf8");
  const startup = hooks.slice(hooks.indexOf("async function onStartup("), hooks.indexOf("async function onMainWindowLoad("));
  assert.match(startup, /registerWindowIndependent\(\)/, "startup registers the process-wide features once");
  assert.doesNotMatch(
    hooks.slice(hooks.indexOf("async function onMainWindowUnload("), hooks.indexOf("function onShutdown(")),
    /unregisterColumn|unregisterAll\(/,
    "closing one window must not unregister a process-wide column",
  );
  // Shutdown must release the cached library data along with the columns, or
  // a disabled plugin would keep paper records in memory.
  const shutdown = hooks.slice(hooks.indexOf("function onShutdown("));
  assert.match(shutdown, /unregisterAll\(\)/, "disable/uninstall still reclaims the columns");
  assert.match(shutdown, /resetPaperData\(\)/, "shutdown drops the per-paper cache");

  const paperData = fs.readFileSync(
    path.join(PLUGIN_DIR, "src", "columns", "paperData.ts"),
    "utf8",
  );
  // Finding 1 (PR #29): the column must read the read-only v2 endpoint and the
  // request queue must be bounded.
  assert.match(
    paperData,
    /maxConcurrentRequests/,
    "the column passes a concurrency cap to the provider factory",
  );
  const dataCore = fs.readFileSync(
    path.join(PLUGIN_DIR, "src", "columns", "columnDataCore.mjs"),
    "utf8",
  );
  assert.match(
    dataCore,
    /DEFAULT_MAX_CONCURRENT_REQUESTS = \d+/,
    "the cap is a named constant, not a magic number",
  );
  assert.match(dataCore, /PAPER_ENDPOINT = "\/api\/v2\/paper"/, "reads the v2 endpoint");
  assert.ok(
    shutdown.indexOf("unregisterAll()") < shutdown.indexOf("resetPaperData()"),
    "the columns are unregistered before their cache is dropped",
  );
});

// ---------------------------------------------------------------------------
// U1: the two columns read real data from the local service.
//
// Everything below runs the real module (plugin/src/columns/columnDataCore.mjs)
// against response bodies shaped exactly like `GET /api/v2/paper`
// (kbapp/web/library_api.py `paper_view`, checked on main): a `paper` object,
// a `relations` array of raw relation payloads, and `understanding` — the
// latest PaperUnderstanding artifact (kbapp/research/reading.py:148) or null.
// ---------------------------------------------------------------------------

const DATA_CORE_URL = pathToFileURL(
  path.join(PLUGIN_DIR, "src", "columns", "columnDataCore.mjs"),
).href;

async function loadDataCore() {
  return import(DATA_CORE_URL);
}

/** One relation as the service stores it (kbapp/storage/store.py `relation`). */
function relation(status, extra = {}) {
  return {
    id: `rel-${status}`,
    src: "z:1:AAAA1111",
    dst: "z:1:BBBB2222",
    kind: "inherits",
    status,
    level: "fulltext",
    evidence: [{ quote: "...", origin: "pdf" }],
    ...extra,
  };
}

/** A full `GET /api/v2/paper` body; override any part per case. */
function paperResponse(overrides = {}) {
  return {
    paper: {
      id: "z:1:AAAA1111",
      kind: "library",
      title: "Informed RRT*",
      year: 2020,
      zotero_key: "AAAA1111",
      library_id: 1,
    },
    relations: [],
    approvals: [],
    understanding: null,
    note_sync: { status: "not_generated" },
    references: { items: [], status: "complete" },
    ...overrides,
  };
}

/** Let queued microtasks run so an in-flight fetch can settle. */
function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

/** Let one macrotask (and its microtasks) run; for fetches awaiting a timer. */
function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/** Wait until `condition` holds or `rounds` macrotasks have passed. */
async function until(condition, rounds = 200) {
  for (let index = 0; index < rounds; index += 1) {
    if (condition()) {
      return true;
    }
    await tick();
  }
  return condition();
}

/** A fetchPaper stand-in that records calls and resolves/rejects on demand. */
function recordingFetcher(impl) {
  const calls = [];
  const fetchPaper = (paper, context) => {
    calls.push({ paper, context });
    return impl(paper, context, calls.length);
  };
  return { calls, fetchPaper };
}

test("relation counts follow the service's own notion of established", async () => {
  const data = await loadDataCore();
  // kbapp/research/viewmodels.py:35 — established is `status == 'confirmed'
  // and not evidence_stale`. A confirmed-but-stale edge is the gap case
  // (viewmodels.py:67 「证据已过期，待复核」), not a confirmed one.
  const counts = data.lineageCountsFromRelations([
    relation("confirmed"),
    relation("confirmed"),
    relation("confirmed", { evidence_stale: true }),
    relation("candidate"),
    relation("rejected"),
  ]);
  assert.deepEqual(counts, { confirmed: 2, pending: 1, gaps: 1 });

  // `rejected` is the only status the service never shows
  // (`status!='rejected'` throughout graph.py), so it must not count anywhere.
  assert.deepEqual(
    data.lineageCountsFromRelations([relation("rejected"), relation("rejected", { evidence_stale: true })]),
    { confirmed: 0, pending: 0, gaps: 0 },
  );

  // An unknown status is pending, not dropped: the service mentioned it.
  assert.deepEqual(data.lineageCountsFromRelations([relation("under_review")]), {
    confirmed: 0,
    pending: 1,
    gaps: 0,
  });

  // Defensive: a non-array, nulls and primitives never throw.
  for (const value of [null, undefined, {}, "", 5, [null, 3, "x", []]]) {
    assert.deepEqual(data.lineageCountsFromRelations(value), {
      confirmed: 0,
      pending: 0,
      gaps: 0,
    });
  }
});

test("a real response body maps to the two column texts", async () => {
  const data = await loadDataCore();
  const core = await loadCore();
  const body = paperResponse({
    relations: [
      relation("confirmed"),
      relation("confirmed"),
      relation("candidate"),
      relation("confirmed", { evidence_stale: true }),
    ],
    understanding: {
      paper_id: "z:1:AAAA1111",
      coverage: { total_pages: 12, read_pages: [1, 2, 3, 4, 5, 6, 7], missing_pages: [8, 9, 10, 11, 12], complete: false },
    },
  });

  const providers = data.createPaperDataProviders({
    fetchPaper: async () => body,
  });
  const row = item("AAAA1111", 1);

  // First read: nothing cached yet, so both cells are blank and one request
  // is started. A synchronous dataProvider cannot wait for HTTP.
  assert.equal(providers.lineageProvider(row, "lineage"), "");
  assert.equal(providers.readingProvider(row, "reading"), "");
  await flush();

  // After the answer lands, the same synchronous providers return real text.
  assert.equal(providers.lineageProvider(row, "lineage"), core.cellText({ confirmed: 2, pending: 1, gaps: 1 }));
  assert.equal(providers.lineageProvider(row, "lineage"), "2 已确认 · 1 待核 · 1 缺口");
  assert.equal(providers.readingProvider(row, "reading"), "部分精读 · 7/12 页");

  // The text must parse back to the badge the renderer draws, so the cell and
  // the sort/export key cannot disagree.
  assert.deepEqual(core.parseCellText(providers.lineageProvider(row, "lineage")), {
    confirmed: 2,
    pending: 1,
    gaps: 1,
  });
  assert.deepEqual(data.parseReadingCellText(providers.readingProvider(row, "reading")), {
    state: "partial",
    readPages: 7,
    totalPages: 12,
  });
  assert.equal(core.badgeForText(providers.lineageProvider(row, "lineage")).state, "gap");
});

test("reading coverage maps to its three states without inventing one", async () => {
  const data = await loadDataCore();
  const cases = [
    // A finished reading: every page read, complete.
    [
      { coverage: { total_pages: 12, read_pages: new Array(12).fill(0).map((_, i) => i + 1), missing_pages: [], complete: true } },
      { state: "complete", readPages: 12, totalPages: 12 },
      "已精读 · 12/12 页",
    ],
    // Partial: some pages read, not complete.
    [
      { coverage: { total_pages: 12, read_pages: [1, 2, 3], missing_pages: [4, 5], complete: false } },
      { state: "partial", readPages: 3, totalPages: 12 },
      "部分精读 · 3/12 页",
    ],
    // A real answer with no reading at all (reading.py:61 writes this shape
    // when the PDF is unavailable).
    [
      { coverage: { total_pages: 0, read_pages: [], missing_pages: [], complete: false } },
      { state: "none", readPages: 0, totalPages: 0 },
      "未精读",
    ],
    // `complete: true` with no pages is not a completed reading; it is still
    // a real answer, so it stays visible rather than vanishing.
    [
      { coverage: { total_pages: 0, read_pages: [], complete: true } },
      { state: "partial", readPages: 0, totalPages: 0 },
      "部分精读 · 0/0 页",
    ],
    // The service rewrites a stale artifact with `coverage.complete = False`
    // and `verification_status: 'stale'` (kbapp/web/library_api.py), so this
    // is the body a stale reading actually arrives as — verified against the
    // real endpoint with a stored artifact. It must render 部分精读, never
    // 已精读.
    [
      {
        verification_status: "stale",
        coverage: { total_pages: 12, read_pages: [1, 2, 3, 4, 5, 6, 7], missing_pages: [8, 9, 10, 11, 12], complete: false },
      },
      { state: "partial", readPages: 7, totalPages: 12 },
      "部分精读 · 7/12 页",
    ],
    // `understanding: null` is the service saying "no artifact yet".
    [null, { state: "none", readPages: 0, totalPages: 0 }, "未精读"],
  ];
  for (const [understanding, expected, text] of cases) {
    const reading = data.readingFromUnderstanding(understanding);
    assert.deepEqual(reading, expected, `coverage ${JSON.stringify(understanding)}`);
    assert.equal(data.readingCellText(reading), text);
    assert.deepEqual(data.parseReadingCellText(text), expected, `${text} must round-trip`);
  }

  // No usable coverage is "no answer", never a fabricated state: the column
  // renders blank. This is the branch to revisit if the endpoint ever drops
  // the coverage field (issue #27 需求 2).
  for (const unknown of [undefined, {}, { coverage: null }, { coverage: [] }, "complete", 3]) {
    assert.equal(data.readingFromUnderstanding(unknown), null, `no answer for ${JSON.stringify(unknown)}`);
    assert.equal(data.readingCellText(data.readingFromUnderstanding(unknown)), "");
  }
  assert.equal(data.readingCellText({ state: "bogus" }), "");
  assert.equal(data.readingCellText(null), "");
});

test("the reading badge carries the full sentence in its title", async () => {
  const data = await loadDataCore();
  const complete = data.readingBadgeForText("已精读 · 12/12 页");
  assert.equal(complete.state, "complete");
  assert.equal(complete.symbol, "●");
  assert.equal(complete.title, "已精读 · 已读 12/12 页");

  const partial = data.readingBadgeForText("部分精读 · 7/12 页");
  assert.equal(partial.symbol, "◐");
  assert.equal(partial.className, "littrace-reading-partial");

  const none = data.readingBadgeForText("未精读");
  assert.equal(none.state, "none");
  assert.equal(none.title, "尚无精读产物", "no fake page progress for an unread paper");

  // Three states must be visually distinguishable, exactly as the lineage
  // badges are (ADR 0012).
  const classes = [complete.className, partial.className, none.className];
  assert.equal(new Set(classes).size, 3);
  const symbols = [complete.symbol, partial.symbol, none.symbol];
  assert.equal(new Set(symbols).size, 3);

  for (const text of ["", null, undefined, 5, "已精读", "已精读 · 12 页", "部分精读 · x/y 页", "未精读 · 0/0 页"]) {
    assert.equal(data.readingBadgeForText(text), null, `must not badge ${JSON.stringify(text)}`);
  }
});

test("the cache key is the identity the service resolves", async () => {
  const data = await loadDataCore();
  assert.equal(data.paperKeyOf(1, "AAAA1111"), "z:1:AAAA1111");
  assert.equal(data.paperKeyOf(2, "AAAA1111"), "z:2:AAAA1111", "the same key in another library is another paper");
  assert.equal(data.paperKeyOf(1, "  AAAA1111  "), "z:1:AAAA1111", "keys are trimmed");
  // The service defaults a missing library_id to 1 (`int(p.get('library_id',
  // 1))`), so an absent/garbage libraryID must mean the user library.
  for (const libraryID of [undefined, null, 0, -1, NaN, Infinity, "1", {}]) {
    assert.equal(data.paperKeyOf(libraryID, "AAAA1111"), "z:1:AAAA1111", `libraryID ${String(libraryID)}`);
  }
  assert.equal(data.paperKeyOf(1.7, "K"), "z:1:K", "fractional library ids are truncated");
  for (const itemKey of ["", "   ", null, undefined, 5, {}]) {
    assert.equal(data.paperKeyOf(1, itemKey), "", "no item key, no paper");
  }
  assert.equal(data.paperKeyOf(1, "AAAA1111"), "z:1:AAAA1111", "stable across calls");

  // The request path escapes the identity: a key is opaque to this module.
  assert.equal(data.paperPath(1, "AAAA1111"), "/api/v2/paper?paper=z%3A1%3AAAAA1111");
  assert.equal(data.paperPath(0, ""), "", "no key, no request");
});

test("one request per paper: hits are served from cache, misses are deduped", async () => {
  const data = await loadDataCore();
  const fetcher = recordingFetcher(async () => {
    await flush();
    return paperResponse({ relations: [relation("confirmed")] });
  });
  const providers = data.createPaperDataProviders({ fetchPaper: fetcher.fetchPaper });
  const row = item("AAAA1111", 1);

  // Both columns ask on the same tick, then the list repaints and asks again:
  // the cache is empty until the answer lands, so every one of these calls is
  // a miss — but exactly one request may be in flight.
  assert.equal(providers.lineageProvider(row, "lineage"), "");
  assert.equal(providers.readingProvider(row, "reading"), "");
  assert.equal(providers.lineageProvider(row, "lineage"), "");
  assert.equal(fetcher.calls.length, 1, "in-flight requests are deduped");
  assert.equal(fetcher.calls[0].paper, "z:1:AAAA1111");
  assert.deepEqual(fetcher.calls[0].context, { libraryID: 1, itemKey: "AAAA1111" });
  await flush();

  // Served from cache afterwards; no second request.
  assert.equal(providers.lineageProvider(row, "lineage"), "1 已确认 · 0 待核 · 0 缺口");
  assert.equal(providers.readingProvider(row, "reading"), "未精读");
  assert.equal(fetcher.calls.length, 1, "a cache hit must not call the service");

  // A different paper (same key, other library) is a different cache entry.
  providers.lineageProvider(item("AAAA1111", 2), "lineage");
  assert.equal(fetcher.calls.length, 2);
  assert.equal(fetcher.calls[1].paper, "z:2:AAAA1111");

  // Non-item rows never reach the service: collections and searches have keys
  // too (collection.js:39, search.js:44) but no paper behind them.
  for (const ref of [
    { _objectType: "collection", key: "AAAA1111" },
    { _objectType: "search", key: "AAAA1111" },
    { _objectType: "header" },
    { _objectType: "item", key: "" },
    null,
  ]) {
    assert.equal(providers.lineageProvider(ref, "lineage"), "");
    assert.equal(providers.readingProvider(ref, "reading"), "");
  }
  assert.equal(fetcher.calls.length, 2, "non-item rows must not hit the service");
});

test("a sort click cannot flood the single-process service with requests", async () => {
  const data = await loadDataCore();
  // One click on a column header re-reads every row: itemTree.jsx
  // `_handleColumnSort` -> `sort()` -> `rowProvider.sort()`, and
  // `_initSortState` drops `_sortCache`, so each row misses exactly once.
  // Without a cap this opens one request per library item at once.
  let inFlight = 0;
  let peak = 0;
  let issued = 0;
  const providers = data.createPaperDataProviders({
    maxConcurrentRequests: 4,
    fetchPaper: async () => {
      issued += 1;
      inFlight += 1;
      peak = Math.max(peak, inFlight);
      // Resolve on a later tick so arrivals really overlap.
      await new Promise((resolve) => setTimeout(resolve, 0));
      inFlight -= 1;
      return paperResponse({ relations: [relation("confirmed")] });
    },
  });

  // 40 rows, as a sort would touch them.
  for (let index = 0; index < 40; index += 1) {
    providers.lineageProvider(item(`ROW${index}`), "lineage");
  }
  assert.equal(issued, 4, "only the concurrency limit is issued immediately");
  assert.equal(peak, 4, "never more than the cap in flight at once");

  // The queued remainder drains on its own, still within the cap. Wait for
  // the last answer to *settle*, not merely to be issued.
  const drained = await until(
    () => issued === 40 && providers.cache.read("z:1:ROW39") !== undefined,
  );
  assert.ok(drained, `every queued paper is eventually requested, got ${issued}`);
  assert.equal(issued, 40);
  assert.ok(peak <= 4, `peak concurrency ${peak} must stay within the cap`);

  // And every one of them landed in the cache.
  for (let index = 0; index < 40; index += 1) {
    assert.equal(
      providers.lineageProvider(item(`ROW${index}`), "lineage"),
      "1 已确认 · 0 待核 · 0 缺口",
      `row ${index} must be filled after the queue drains`,
    );
  }
});

test("queueing dedupes repeated rows and skips papers filled while waiting", async () => {
  const data = await loadDataCore();
  const requested = [];
  const providers = data.createPaperDataProviders({
    maxConcurrentRequests: 1,
    fetchPaper: async (paper) => {
      requested.push(paper);
      await flush();
      return paperResponse({ relations: [relation("confirmed")] });
    },
  });

  // Same paper asked for repeatedly in one tick, before anything settles.
  for (let index = 0; index < 5; index += 1) {
    providers.lineageProvider(item("SAME0001"), "lineage");
  }
  // A second paper queued behind the first.
  providers.lineageProvider(item("SAME0002"), "lineage");
  assert.equal(requested.length, 1, "only one request starts under a cap of 1");

  await until(() => requested.length === 2);
  assert.deepEqual(requested, ["z:1:SAME0001", "z:1:SAME0002"], "each paper exactly once");
});

test("a queued paper that another request already filled is not fetched again", async () => {
  const data = await loadDataCore();
  let issued = 0;
  const providers = data.createPaperDataProviders({
    maxConcurrentRequests: 1,
    fetchPaper: async () => {
      issued += 1;
      await flush();
      return paperResponse({ relations: [relation("confirmed")] });
    },
  });

  providers.lineageProvider(item("QUEUE001"), "lineage");
  providers.lineageProvider(item("QUEUE002"), "lineage");
  // While QUEUE002 waits, its value is filled out from under the queue.
  providers.cache.store("z:1:QUEUE002", paperResponse({ relations: [relation("confirmed")] }));
  await until(() => providers.cache.read("z:1:QUEUE001") !== undefined);
  await tick();
  await tick();
  assert.equal(issued, 1, "the already-filled paper is skipped, not re-requested");
  assert.equal(providers.lineageProvider(item("QUEUE002"), "lineage"), "1 已确认 · 0 待核 · 0 缺口");
});

test("an update hook fires once per successful answer, never on failure", async () => {
  const data = await loadDataCore();
  const updated = [];
  let mode = "ok";
  const providers = data.createPaperDataProviders({
    fetchPaper: async () => {
      if (mode === "fail") {
        throw new Error("service offline");
      }
      return paperResponse();
    },
    onUpdate: (key) => updated.push(key),
  });

  providers.lineageProvider(item("AAAA1111"), "lineage");
  await flush();
  assert.deepEqual(updated, ["z:1:AAAA1111"], "a landed answer repaints the tree once");

  mode = "fail";
  providers.lineageProvider(item("BBBB2222"), "lineage");
  await flush();
  assert.deepEqual(updated, ["z:1:AAAA1111"], "a failure changes nothing on screen");

  // A throwing repaint hook must not turn a good response into a failure.
  const throwing = data.createPaperDataProviders({
    fetchPaper: async () => paperResponse(),
    onUpdate: () => {
      throw new Error("window torn down");
    },
  });
  throwing.lineageProvider(item("CCCC3333"), "lineage");
  await flush();
  assert.equal(throwing.readingProvider(item("CCCC3333"), "reading"), "未精读");
});

test("every failure leaves both cells blank instead of throwing", async () => {
  const data = await loadDataCore();
  const failures = [
    async () => {
      throw new Error("service offline");
    },
    () => {
      throw new Error("thrown synchronously");
    },
    async () => null,
    async () => "not an object",
    async () => ({}),
    async () => ({ relations: "not an array" }),
  ];
  for (const fetchPaper of failures) {
    const providers = data.createPaperDataProviders({ fetchPaper });
    const row = item("AAAA1111");
    // Before the rejection settles and after it: still empty, still no throw.
    assert.equal(providers.lineageProvider(row, "lineage"), "");
    assert.equal(providers.readingProvider(row, "reading"), "");
    await flush();
    assert.equal(providers.lineageProvider(row, "lineage"), "");
    assert.equal(providers.readingProvider(row, "reading"), "");
    // The list keeps asking on every repaint; none of that may throw either.
    for (let index = 0; index < 3; index += 1) {
      assert.equal(typeof providers.lineageProvider(row, "lineage"), "string");
      assert.equal(typeof providers.readingProvider(row, "reading"), "string");
    }
  }
});

test("a failed paper is retried after the short failure TTL, not immediately", async () => {
  const data = await loadDataCore();
  let now = 1000;
  let attempts = 0;
  const cache = data.createPaperCache({ now: () => now, ttlMs: 60000, failureTtlMs: 5000 });
  const providers = data.createPaperDataProviders({
    cache,
    fetchPaper: async () => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error("service offline");
      }
      return paperResponse({ relations: [relation("confirmed")] });
    },
  });
  const row = item("AAAA1111");

  providers.lineageProvider(row, "lineage");
  await flush();
  assert.equal(attempts, 1);
  assert.equal(providers.lineageProvider(row, "lineage"), "");

  // Within the failure TTL the list may repaint as often as it likes; a down
  // service must not get one request per row per repaint.
  now += 1000;
  providers.lineageProvider(row, "lineage");
  await flush();
  assert.equal(attempts, 1, "a recent failure is not retried immediately");

  // Once the short TTL expires the column recovers on its own.
  now += 5000;
  providers.lineageProvider(row, "lineage");
  await flush();
  assert.equal(attempts, 2, "the service is retried after the failure TTL");
  assert.equal(providers.lineageProvider(row, "lineage"), "1 已确认 · 0 待核 · 0 缺口");
});

test("the cache expires, bounds itself and can be cleared", async () => {
  const data = await loadDataCore();
  let now = 0;
  const cache = data.createPaperCache({ now: () => now, ttlMs: 100, maxEntries: 2 });
  assert.equal(cache.read("k"), undefined, "a miss is undefined");

  cache.store("k", { value: 1 });
  assert.deepEqual(cache.read("k"), { ok: true, data: { value: 1 } });
  now = 99;
  assert.notEqual(cache.read("k"), undefined, "still fresh before the TTL");
  now = 100;
  assert.equal(cache.read("k"), undefined, "expires at the TTL boundary");

  cache.storeFailure("f");
  assert.deepEqual(cache.read("f"), { ok: false, data: null }, "a failure is cached as 'no answer'");
  now += data.PAPER_CACHE_DEFAULTS.failureTtlMs;
  assert.equal(cache.read("f"), undefined, "the failure entry expires too");

  // Oldest-insertion eviction keeps memory bounded on a huge library.
  now = 0;
  cache.store("a", { n: 1 });
  cache.store("b", { n: 2 });
  cache.store("c", { n: 3 });
  assert.equal(cache.size, 2, "the oldest entry is evicted");
  assert.equal(cache.read("a"), undefined);
  assert.notEqual(cache.read("c"), undefined, "the newest entry survives");

  cache.clear();
  assert.equal(cache.size, 0);
  for (const key of ["", null, undefined, 5]) {
    assert.equal(cache.read(key), undefined);
    cache.store(key, {});
    cache.storeFailure(key);
  }
  assert.equal(cache.size, 0, "garbage keys are ignored, not stored");
});

test("both column options register with documented keys only", async () => {
  const core = await loadCore();
  const lineage = core.lineageColumnOption("littrace@example.invalid");
  const reading = core.readingColumnOption("littrace@example.invalid");
  // pluginAPIBase._validateObject throws "Unknown option" for any key outside
  // itemTreeManager's optionTypeDefinition. Both columns must stay inside it.
  const keys = [
    "dataKey",
    "enabledTreeIDs",
    "flex",
    "label",
    "minWidth",
    "pluginID",
    "showInColumnPicker",
    "zoteroPersist",
  ];
  assert.deepEqual(Object.keys(lineage).sort(), keys);
  assert.deepEqual(Object.keys(reading).sort(), keys);

  assert.equal(reading.dataKey, "reading");
  assert.equal(reading.label, "精读");
  assert.notEqual(reading.dataKey, lineage.dataKey, "two columns need two dataKeys");
  assert.equal("defaultIn" in reading, false, "「精读」 is default-off too");
  assert.equal(reading.showInColumnPicker, true);
  assert.deepEqual(reading.enabledTreeIDs, ["main"]);
  assert.deepEqual(reading.zoteroPersist, ["width", "hidden", "sortDirection"]);

  // The reading column registers its own option, not the lineage one.
  const manager = fakeManager();
  const registration = core.createColumnRegistration(
    manager,
    "littrace@example.invalid",
    { dataProvider: () => "" },
    reading,
  );
  assert.equal(manager.calls.register[0].dataKey, "reading");
  assert.equal(registration.dispose(), true);
});

/** Minimal stand-in for a Document/Window: only what the core touches. */class fakeEventTarget {
  constructor() {
    this.listeners = new Map();
    this.lastEvent = null;
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatchEvent(event) {
    this.lastEvent = event;
    for (const listener of [...(this.listeners.get(event.type) ?? [])]) {
      listener(event);
    }
    return true;
  }
}
