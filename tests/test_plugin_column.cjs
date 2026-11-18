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

  const inputs = [
    null,
    undefined,
    0,
    "",
    [],
    {},
    { key: "" },
    { key: 42 },
    { key: "   " },
    { key: null },
  ];
  for (const item of inputs) {
    assert.equal(
      core.fakeLineageDataProvider(item, "lineage"),
      "",
      `item ${JSON.stringify(item)} must yield an empty cell`,
    );
  }
  // A resolver that throws (offline service, schema drift) must not escape:
  // dataProvider is called while the item list renders.
  const throwing = core.createDataProvider(() => {
    throw new Error("service offline");
  });
  assert.equal(throwing({ key: "ABCD1234" }, "lineage"), "");
  assert.equal(throwing(null, "lineage"), "");
  // And neither may the raw resolver itself be reachable: the provider
  // returns a string for every input, never undefined/null/object.
  const provider = core.fakeLineageDataProvider;
  for (const item of [...inputs, { key: "ABCD1234" }]) {
    assert.equal(typeof provider(item, "lineage"), "string");
  }
  assert.equal(core.cellText(offline()), "");
});

test("the same item always produces the same text", async () => {
  const core = await loadCore();
  const first = { key: "ITEMKEY1", libraryID: 1 };
  const second = { key: "ITEMKEY1", libraryID: 1 };
  const firstText = core.fakeLineageDataProvider(first, "lineage");
  assert.equal(core.fakeLineageDataProvider(second, "lineage"), firstText);
  assert.equal(core.fakeLineageDataProvider(first, "lineage"), firstText);
  assert.equal(core.fakeLineageDataProvider(first, "other-data-key"), firstText);
  assert.equal(core.stableHash("ITEMKEY1"), core.stableHash("ITEMKEY1"));
  assert.notEqual(core.stableHash("ITEMKEY1"), core.stableHash("ITEMKEY2"));
  assert.equal(core.stableHash(undefined), core.stableHash(""));
});

test("a library of fake items shows all four outcomes, badges included", async () => {
  const core = await loadCore();
  const seen = new Set();
  const byState = new Map();
  for (let index = 0; index < 200; index += 1) {
    const key = `FAKE${index}`;
    const counts = core.fakeLineageFor(key);
    const state = core.badgeState(counts);
    const text = core.fakeLineageDataProvider({ key }, "lineage");
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

test("the count next to the badge always matches the state shown", async () => {
  const core = await loadCore();
  assert.equal(core.primaryCount({ confirmed: 4, pending: 2, gaps: 9 }, "gap"), 9);
  assert.equal(core.primaryCount({ confirmed: 4, pending: 2, gaps: 9 }, "pending"), 2);
  assert.equal(core.primaryCount({ confirmed: 4, pending: 2, gaps: 9 }, "verified"), 4);
  assert.equal(core.primaryCount({ confirmed: 4, pending: 2, gaps: 9 }, "empty"), 0);
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
    { source: "lineage-column", itemKey: "ABCD1234", libraryID: 1, state: "pending" },
  );
  assert.deepEqual(
    core.createLocatePayload({ itemKey: " ABCD1234 ", libraryID: "1", state: "gap" }),
    { source: "lineage-column", itemKey: "ABCD1234", libraryID: null, state: "gap" },
  );
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
  const ref = { key: "K" };
  assert.equal(core.itemRefOf({ getRow: (index) => (index === 3 ? { ref } : null) }, 3), ref);
  assert.equal(core.itemRefOf({ getRow: () => ({}) }, 0), null, "header/spacer rows have no ref");
  assert.equal(core.itemRefOf({ getRow: () => null }, 0), null);
  assert.equal(
    core.itemRefOf({ getRow: () => { throw new Error("index out of range"); } }, 0),
    null,
  );
  assert.equal(core.itemRefOf(null, 0), null);
  assert.equal(core.itemRefOf({}, 0), null);

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
        ZoteroPane: { itemsView: { getRow: () => ({ ref: "active" }) } },
        ZoteroPane_Local: { itemsView: { getRow: () => ({ ref: "local" }) } },
      },
    }),
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
  assert.match(lineage, /dataProvider:\s*fakeLineageDataProvider/, "A2 uses fake data on purpose");
  assert.match(lineage, /export function createLineageColumn\(\)/, "the registry builds it");

  const registry = fs.readFileSync(path.join(PLUGIN_DIR, "src", "registry.ts"), "utf8");
  // The process-wide entry is registerWindowIndependent (shared with the
  // reader events registered by A3); the column is added there, not in a
  // second parallel "global" entry.
  assert.match(
    registry,
    /export function registerWindowIndependent\(\): void \{[\s\S]*?createLineageColumn\(\)[\s\S]*?registerGlobal\(\s*"item-column:lineage"/,
    "registerWindowIndependent must register the column and hand its disposer to the registry",
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
  assert.match(
    hooks.slice(hooks.indexOf("function onShutdown(")),
    /unregisterAll\(\)/,
    "disable/uninstall still reclaims the column",
  );
});

/** Minimal stand-in for a Document/Window: only what the core touches. */
class fakeEventTarget {
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
