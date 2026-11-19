"use strict";

// Anchors the four iframe function tabs and the host bridge (U4).
//
// The logic under test lives in plugin/src/tabs/tabsCore.mjs: plain ESM with
// no Zotero/DOM globals, so `node --test` exercises the real hash routing,
// registry integrity, cleanup ledger and deep-link decision without npm
// install or a Zotero process. The DOM/Zotero half (iframe mounting,
// Zotero_Tabs.add, Zotero.launchURL, cross-origin DOM access) cannot be
// tested here and stays in the 真机 acceptance list (M0 items ③④⑦); the
// wiring that connects it to the single registry is locked by source-text
// assertions, the convention ADR 0010 established.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const PLUGIN_DIR = path.join(__dirname, "..", "plugin");
const CORE_URL = pathToFileURL(
  path.join(PLUGIN_DIR, "src", "tabs", "tabsCore.mjs"),
).href;
const TABS_TS = path.join(PLUGIN_DIR, "src", "tabs", "tabs.ts");
const REGISTRY_TS = path.join(PLUGIN_DIR, "src", "registry.ts");

const THUMBNAIL_BASE = "http://127.0.0.1:8765";

async function loadCore() {
  return import(CORE_URL);
}

test("the registry names exactly the four function tabs", async () => {
  const core = await loadCore();
  assert.deepEqual(core.TAB_IDS, ["search", "graph", "research", "approvals"]);
  assert.equal(core.TAB_REGISTRY.length, 4);
  assert.ok(Object.isFrozen(core.TAB_REGISTRY), "the registry is frozen data");
  for (const tab of core.TAB_REGISTRY) {
    assert.ok(Object.isFrozen(tab), `${tab.id} must be frozen`);
  }
});

test("the registry has no duplicate id, hash or title key", async () => {
  const core = await loadCore();
  const ids = core.TAB_REGISTRY.map((tab) => tab.id);
  const hashes = core.TAB_REGISTRY.map((tab) => tab.hash);
  const titleKeys = core.TAB_REGISTRY.map((tab) => tab.titleKey);
  assert.equal(new Set(ids).size, ids.length, "duplicate tab id");
  assert.equal(new Set(hashes).size, hashes.length, "duplicate hash route");
  assert.equal(
    new Set(titleKeys).size,
    titleKeys.length,
    "duplicate title key",
  );
  for (const tab of core.TAB_REGISTRY) {
    assert.ok(
      core.TAB_BUILDERS.includes(tab.builder),
      `${tab.id} uses unknown builder ${tab.builder}`,
    );
    assert.match(tab.titleKey, /^tab-title-/, `${tab.id} title key convention`);
    assert.equal(tab.hash, tab.id, "hash and id stay one-to-one");
  }
});

test("every hash route maps back to its tab and only the four do", async () => {
  const core = await loadCore();
  for (const tab of core.TAB_REGISTRY) {
    assert.equal(core.tabIdForHash(`#${tab.hash}`), tab.id);
    assert.equal(core.tabIdForHash(tab.hash), tab.id);
    assert.equal(core.hashForTab(tab.id), `#${tab.hash}`);
  }
  // The service shell has more pages than the four function tabs; this bridge
  // maps only the four and leaves the rest to the service page itself.
  for (const other of ["#settings", "#chains", "#status", "#", "", null, 42]) {
    assert.equal(core.tabIdForHash(other), null, `unexpected route ${other}`);
  }
  assert.equal(core.tabById("settings"), null);
  assert.equal(core.hashForTab("nope"), null);
});

test("every tab title key exists in both Fluent bundles", async () => {
  const core = await loadCore();
  for (const locale of ["zh-CN", "en-US"]) {
    const ftl = fs.readFileSync(
      path.join(PLUGIN_DIR, "addon", "locale", locale, "addon.ftl"),
      "utf8",
    );
    for (const tab of core.TAB_REGISTRY) {
      assert.match(
        ftl,
        new RegExp(`^${tab.titleKey} = .+$`, "m"),
        `${locale} is missing ${tab.titleKey}`,
      );
    }
  }
});

test("each tab builds an iframe that opens the service page at its hash", async () => {
  const core = await loadCore();
  for (const tab of core.TAB_REGISTRY) {
    const spec = core.tabNodeSpec(tab.id, { baseUrl: `${THUMBNAIL_BASE}/` });
    assert.equal(spec.kind, "iframe");
    assert.equal(spec.tabId, tab.id);
    assert.equal(spec.src, `${THUMBNAIL_BASE}/#${tab.hash}`);
    assert.equal(spec.titleKey, tab.titleKey);
  }
  assert.equal(core.tabNodeSpec("settings", { baseUrl: THUMBNAIL_BASE }), null);
  assert.equal(core.tabNodeSpec("graph", { baseUrl: "" }), null);
  assert.equal(core.tabNodeSpec("graph", {}), null);
});

test("a paper deep link is a query parameter before the hash", async () => {
  const core = await loadCore();
  // The service shell reads `location.search` for `paper` and `location.hash`
  // for the page, so the query must come first; `#graph?paper=…` would be the
  // wrong shape and the page would silently ignore the deep link.
  assert.equal(
    core.tabUrl("graph", { baseUrl: THUMBNAIL_BASE, paper: "ABCD1234" }),
    `${THUMBNAIL_BASE}/?paper=ABCD1234#graph`,
  );
  assert.equal(
    core.tabUrl("search", { baseUrl: `${THUMBNAIL_BASE}/`, paper: "a&b" }),
    `${THUMBNAIL_BASE}/?paper=a%26b#search`,
  );
  // Blank paper = a plain tab, not `?paper=`.
  assert.equal(
    core.tabUrl("approvals", { baseUrl: THUMBNAIL_BASE, paper: "   " }),
    `${THUMBNAIL_BASE}/#approvals`,
  );
});

test("the cleanup ledger records on add and is empty after clear", async () => {
  const core = await loadCore();
  const errors = [];
  const calls = [];
  const ledger = core.createCleanupLedger((error, id) => {
    errors.push([error, id]);
  });

  assert.equal(ledger.size, 0);
  ledger.add("iframe:graph", () => calls.push("iframe"));
  ledger.add("deep-link:graph", () => calls.push("deep-link"));
  ledger.add("focus:graph", () => calls.push("focus"));
  assert.equal(ledger.size, 3, "register is record");
  assert.deepEqual(ledger.ids(), [
    "iframe:graph",
    "deep-link:graph",
    "focus:graph",
  ]);
  assert.equal(ledger.has("focus:graph"), true);

  const cleared = ledger.clear();
  assert.equal(cleared, 3);
  assert.equal(ledger.size, 0, "clear is empty");
  assert.deepEqual(ledger.ids(), []);
  // Reverse registration order: later resources may depend on earlier ones.
  assert.deepEqual(calls, ["focus", "deep-link", "iframe"]);
  assert.deepEqual(errors, []);
});

test("a throwing disposer cannot stop cleanup or leave the table dirty", async () => {
  const core = await loadCore();
  const errors = [];
  const calls = [];
  const ledger = core.createCleanupLedger((error, id) => {
    errors.push([error.message, id]);
  });

  ledger.add("first", () => calls.push("first"));
  ledger.add("boom", () => {
    throw new Error("boom");
  });
  ledger.add("last", () => calls.push("last"));

  assert.equal(ledger.clear(), 3);
  assert.equal(ledger.size, 0, "the table is emptied before disposers run");
  assert.deepEqual(calls, ["last", "first"]);
  assert.deepEqual(errors, [["boom", "boom"]]);
});

test("the ledger rejects duplicate ids and bad entries", async () => {
  const core = await loadCore();
  const ledger = core.createCleanupLedger();
  ledger.add("iframe:graph", () => {});
  assert.throws(
    () => ledger.add("iframe:graph", () => {}),
    /清理表条目重复/,
    "one id cannot silently overwrite another's disposal",
  );
  assert.throws(() => ledger.add("", () => {}), /非空字符串/);
  assert.throws(() => ledger.add("x", null), /清理函数/);
});

test("remove releases exactly one entry and drops it", async () => {
  const core = await loadCore();
  const calls = [];
  const ledger = core.createCleanupLedger();
  ledger.add("iframe:graph", () => calls.push("iframe"));
  ledger.add("focus:graph", () => calls.push("focus"));

  assert.equal(ledger.remove("iframe:graph"), true);
  assert.deepEqual(calls, ["iframe"]);
  assert.equal(ledger.size, 1);
  assert.equal(ledger.has("iframe:graph"), false);
  assert.equal(ledger.remove("iframe:graph"), false, "removing twice is a no-op");
  assert.deepEqual(calls, ["iframe"], "nothing else was disposed");
});

test("the deep-link predicate only accepts zotero: and obsidian:", async () => {
  const core = await loadCore();
  for (const href of [
    "zotero://open-pdf/library/items/ABCD?page=3",
    "ZOTERO://select/items/1",
    "  obsidian://open?vault=_知识库  ",
    "Obsidian://open?vault=x",
  ]) {
    assert.equal(core.isDeepLink(href), true, href);
  }
  for (const href of [
    "https://example.com/zotero:",
    "mailto:someone@example.com",
    "javascript:alert(1)",
    "/relative#graph",
    "",
    null,
    42,
  ]) {
    assert.equal(core.isDeepLink(href), false, String(href));
  }
});

test("a lineage locate payload becomes a littrace paper id, not a raw Zotero key", async () => {
  const core = await loadCore();
  // `itemKey` is a Zotero item key. The service resolves `?paper=` through
  // Store.resolve (kbapp/storage/store.py:155-167), whose aliases table holds
  // paper id / citekey / openalex_id / doi -- a bare Zotero key resolves to
  // nothing, so paper_view raises 「文献不存在」 and graph_page returns an
  // unfiltered graph. littrace's id for a Zotero item is
  // z:<libraryID>:<zoteroKey> (kbapp/library/sources.py:34-40).
  assert.deepEqual(
    core.locateRequestOf({
      source: "lineage-column",
      itemKey: "ABCD1234",
      libraryID: 1,
      state: "verified",
    }),
    { tab: "graph", paper: "z:1:ABCD1234" },
  );
  // The library id is part of the identity: the same key in a group library
  // is a different paper.
  assert.deepEqual(
    core.locateRequestOf({ itemKey: "ABCD1234", libraryID: 2 }),
    { tab: "graph", paper: "z:2:ABCD1234" },
  );
  assert.deepEqual(
    core.locateRequestOf({ itemKey: "ABCD1234", libraryID: "3" }),
    { tab: "graph", paper: "z:3:ABCD1234" },
  );

  // No usable key: ignore the event instead of opening an unfiltered tab.
  assert.equal(core.locateRequestOf({ itemKey: "   " }), null);
  assert.equal(core.locateRequestOf({}), null);
  assert.equal(core.locateRequestOf(null), null);
  assert.equal(core.locateRequestOf("ABCD1234"), null);
  // A missing library id is refused, not guessed: the service would default
  // it to 1 and silently open the wrong paper for a group library.
  assert.equal(core.locateRequestOf({ itemKey: "ABCD1234" }), null);
  assert.equal(core.locateRequestOf({ itemKey: "ABCD1234", libraryID: null }), null);
  assert.equal(core.locateRequestOf({ itemKey: "ABCD1234", libraryID: 1.5 }), null);
  assert.equal(core.locateRequestOf({ itemKey: "ABCD1234", libraryID: -1 }), null);
  assert.equal(core.locateRequestOf({ itemKey: "ABCD1234", libraryID: "x" }), null);
});

test("the locate deep link round-trips into ?paper= the service can resolve", async () => {
  const core = await loadCore();
  const request = core.locateRequestOf({ itemKey: "AAAA1111", libraryID: 1 });
  assert.deepEqual(request, { tab: "graph", paper: "z:1:AAAA1111" });
  // This exact URL shape is what graph_page resolves to a real neighborhood;
  // `?paper=AAAA1111` alone yields 0 nodes / 0 edges.
  assert.equal(
    core.tabUrl(request.tab, { baseUrl: THUMBNAIL_BASE, paper: request.paper }),
    `${THUMBNAIL_BASE}/?paper=z%3A1%3AAAAA1111#graph`,
  );
});

test("a refused locate payload says why instead of failing silently", async () => {
  const core = await loadCore();
  // A click that opens nothing must be visible: the repository rule is that a
  // disabled entry explains itself, and a silent return here is the控件
  // "clicked, nothing happened" state.
  assert.equal(
    core.locateRefusalOf({ itemKey: "ABCD1234", libraryID: 1 }),
    null,
    "a usable payload has no refusal",
  );
  assert.match(core.locateRefusalOf({ itemKey: "ABCD1234" }), /libraryID/);
  assert.match(core.locateRefusalOf({ libraryID: 1 }), /条目 key/);
  assert.match(core.locateRefusalOf(null), /载荷/);
  assert.match(core.locateRefusalOf("ABCD1234"), /载荷/);
});

test("a stale host is reopened, a live one is reused", async () => {
  const core = await loadCore();
  // Unknown signals count as alive: a false "reopen" would spawn a duplicate
  // window, which is worse than reusing.
  assert.equal(core.reuseVerdict({}), "reuse");
  assert.equal(core.reuseVerdict(), "reuse");
  assert.equal(core.reuseVerdict({ hostClosed: false }), "reuse");
  assert.equal(core.reuseVerdict({ iframeConnected: true }), "reuse");
  // A host that closed, or an iframe that left the document, is stale: the
  // reuse branch would retarget a dead node and still report success.
  assert.equal(core.reuseVerdict({ hostClosed: true }), "reopen");
  assert.equal(core.reuseVerdict({ iframeConnected: false }), "reopen");
  assert.equal(
    core.reuseVerdict({ hostClosed: false, iframeConnected: false }),
    "reopen",
  );
  assert.equal(core.reuseVerdict(null), "reuse");
});

test("the open mechanism falls back to the dialog, visibly", async () => {
  const core = await loadCore();
  assert.equal(core.DEFAULT_OPEN_MECHANISM, "zotero-tabs");

  const preferred = core.resolveOpenPlan("zotero-tabs", {
    zoteroTabsAvailable: true,
  });
  assert.equal(preferred.mechanism, "zotero-tabs");
  assert.equal(preferred.fallback, false);
  assert.equal(preferred.reason, "");

  const fallback = core.resolveOpenPlan("zotero-tabs", {
    zoteroTabsAvailable: false,
  });
  assert.equal(fallback.mechanism, "dialog");
  assert.equal(fallback.fallback, true);
  assert.ok(fallback.reason.length > 0, "a fallback must say why");

  // Switching the constant to the fallback is not a degradation.
  const switched = core.resolveOpenPlan("dialog", { zoteroTabsAvailable: true });
  assert.equal(switched.mechanism, "dialog");
  assert.equal(switched.fallback, false);

  assert.throws(() => core.resolveOpenPlan("browser", {}), /未知 tab 打开机制/);
});

test("the bridge switch has exactly capture and native", async () => {
  const core = await loadCore();
  assert.equal(core.DEFAULT_BRIDGE_MODE, "capture");
  assert.deepEqual(core.BRIDGE_MODES, ["capture", "native"]);
  assert.equal(core.bridgePlan("capture").capture, true);
  assert.equal(core.bridgePlan("native").capture, false);
  assert.throws(() => core.bridgePlan("auto"), /未知深链桥接模式/);
});

test("the tabs are wired through the plugin's single registration entry", () => {
  const registry = fs.readFileSync(REGISTRY_TS, "utf8");
  assert.match(
    registry,
    /import \{ createTabHost \} from "\.\/tabs\/tabs";/,
    "registry must build the tab host from the tabs module",
  );
  assert.match(
    registry,
    /export async function registerAll\(win: Window\): Promise<void> \{[\s\S]*?const tabHost = createTabHost\(win\);[\s\S]*?register\(win, "iframe-tabs", \(\) => \{[\s\S]*?tabHost\.dispose\(\);/,
    "registerAll must register the tab host and hand its disposer to the registry",
  );
  const hooks = fs.readFileSync(
    path.join(PLUGIN_DIR, "src", "hooks.ts"),
    "utf8",
  );
  assert.match(
    hooks,
    /async function onMainWindowUnload\(\s*win: Window\s*\)[\s\S]*?unregisterWindow\(win\)/,
    "closing one window must reclaim its tabs through unregisterWindow",
  );
  assert.match(
    hooks.slice(hooks.indexOf("function onShutdown(")),
    /unregisterAll\(\)/,
    "disable/uninstall must reclaim every window's tabs",
  );
});

test("the tab host keeps both switch points and every cleanup entry visible", () => {
  const source = fs.readFileSync(TABS_TS, "utf8");

  // The two switch points: one constant each, no scattered decisions.
  assert.match(source, /const OPEN_MECHANISM = DEFAULT_OPEN_MECHANISM;/);
  assert.match(source, /const BRIDGE_MODE = DEFAULT_BRIDGE_MODE;/);
  assert.match(source, /resolveOpenPlan\(OPEN_MECHANISM,/);
  assert.match(source, /bridgePlan\(BRIDGE_MODE\)/);
  assert.match(source, /openDialog\(/, "the dialog fallback ships in the same change");
  assert.match(source, /Zotero_Tabs/, "the primary path is the internal tab deck");

  // The cleanup table owns the iframe node, the deep-link listener and the
  // focus timer, exactly as the issue requires.
  for (const entry of ["iframe", "deep-link", "focus"]) {
    assert.ok(
      source.includes(`ledger.add(\`${entry}:\${id}\``),
      `the cleanup table must register the ${entry} resource`,
    );
  }
  assert.ok(
    source.includes("ledger.remove(`deep-link:${id}`)") &&
      source.includes("ledger.remove(`focus:${id}`)") &&
      source.includes("ledger.remove(`iframe:${id}`)"),
    "closing one tab removes its three resources",
  );

  // Deep-link bridge: capture-phase click, deep-link predicate, privileged
  // launch, and the A2 locate listener that opens the 谱系 tab.
  assert.match(source, /doc\.addEventListener\("click", onClick, true\);/);
  assert.match(source, /isDeepLink\(href\)/);
  assert.match(source, /Zotero\.launchURL\(href\);/);
  assert.match(source, /listenLineageLocate\(win,/);
  assert.match(source, /locateRequestOf\(detail\)/);

  // The accepted degradation is a real branch, not a comment: no launch API
  // means the native anchor keeps the click.
  assert.match(
    source,
    /if \(!canLaunchExternal\(\)\) \{[\s\S]*?return;\s*\}[\s\S]*?event\.preventDefault\(\);/,
    "a missing launch API must leave the native navigation intact",
  );
});

test("the tab entry stays internal until the tools menu lands", () => {
  const source = fs.readFileSync(TABS_TS, "utf8");
  // U6 adds the tools menu on top of this entry; until then the only caller
  // is the A2 locate listener. The entry must refuse to build a host on
  // demand: a host created outside registerAll would never be reclaimed.
  assert.match(
    source,
    /export function openFunctionTab\(/,
    "the reserved entry for the tools menu (U6) must exist",
  );
  assert.match(
    source,
    /export function openFunctionTab\([\s\S]*?const host = hosts\.get\(win\);[\s\S]*?if \(!host\) \{[\s\S]*?return false;/,
    "openFunctionTab must reuse the registered host, never create one",
  );
  assert.doesNotMatch(
    source,
    /export function openFunctionTab\([\s\S]*?createTabHost\(/,
    "the entry must not create a host outside registerAll",
  );
});

test("a closed host is detected and removed instead of being reused", () => {
  const source = fs.readFileSync(TABS_TS, "utf8");

  // The reuse branch must consult the pure verdict first. Reusing a stale
  // entry retargets a dead iframe and returns true while nothing is on
  // screen: the "click does nothing, forever" failure.
  assert.match(
    source,
    /const existing = opened\.get\(id\);[\s\S]*?if \(reuseVerdict\(existing\.signals\(\)\) === "reuse"\)/,
    "the reuse branch must check liveness first",
  );
  assert.match(
    source,
    /closeOpened\(id, \{ hostAlreadyClosing: true \}\);[\s\S]*?const plan = resolveOpenPlan/,
    "a stale entry must be reclaimed before the fresh mount",
  );

  // The fallback window fires no event this host owns, so it must wire its
  // own close notification.
  assert.match(source, /dialog\.addEventListener\("pagehide", markClosed\);/);
  assert.match(source, /dialog\.addEventListener\("unload", markClosed\);/);
  assert.match(
    source,
    /mountInDialog\(window, entry, spec, \(\) => \{[\s\S]*?closeOpened\(entry\.id, \{ hostAlreadyClosing: true \}\);/,
    "the dialog must report its own close back to the host",
  );

  // Both mount paths expose the raw signals the verdict consumes.
  const signalCount = (source.match(/signals: \(\) => \(/g) ?? []).length;
  assert.equal(signalCount, 2, "both mount paths must provide signals()");
  assert.match(source, /iframeConnected: iframe\.isConnected/);
});

// ── Zotero_Tabs.add result contract (真机定案, Zotero 10.0.8) ──────────────
//
// The real `chrome/content/zotero/tabs.js` returns `{ id, container }` and
// stores `data` verbatim on the tab record; omitting `data` leaves
// `tab.data === undefined` and Zotero's own renderer dereferences it. Both
// halves of that contract are parsed by the pure module, so they are asserted
// here instead of only on a real machine.

test("readTabId accepts the documented object shape and the legacy string", async () => {
  const { readTabId } = await loadCore();

  assert.equal(readTabId({ id: "tab-abc", container: {} }), "tab-abc");
  assert.equal(readTabId("tab-legacy"), "tab-legacy");

  // Everything else is "no usable id", never a throw: the caller falls back.
  assert.equal(readTabId(null), null);
  assert.equal(readTabId(undefined), null);
  assert.equal(readTabId(""), null);
  assert.equal(readTabId({}), null);
  assert.equal(readTabId({ id: "" }), null);
  assert.equal(readTabId({ id: 42 }), null);
  assert.equal(readTabId(7), null);
  assert.equal(readTabId([{ id: "x" }]), null);
});

test("hasResultContainer only accepts a node that can take children", async () => {
  const { hasResultContainer } = await loadCore();
  const appendable = { append() {} };

  assert.equal(hasResultContainer({ id: "t", container: appendable }), true);
  assert.equal(hasResultContainer({ id: "t" }), false);
  assert.equal(hasResultContainer({ container: null }), false);
  assert.equal(hasResultContainer({ container: "div" }), false);
  assert.equal(hasResultContainer({ container: {} }), false);
  assert.equal(hasResultContainer("tab-id"), false);
  assert.equal(hasResultContainer(null), false);
});

test("tabDataPayload always yields a plain object, copying caller fields", async () => {
  const { tabDataPayload } = await loadCore();

  // The record must never carry `undefined`, which is the whole defect.
  const empty = tabDataPayload();
  assert.deepEqual(empty, {});
  assert.equal(typeof empty, "object");
  assert.notEqual(empty, null);
  assert.equal(Array.isArray(empty), false);
  assert.equal(Object.getPrototypeOf(empty), Object.prototype);

  const withFields = tabDataPayload({ paper: "z:1:ABC", itemID: 7 });
  assert.deepEqual(withFields, { paper: "z:1:ABC", itemID: 7 });

  // Nullish and non-object inputs degrade to the empty payload rather than throw.
  assert.deepEqual(tabDataPayload(null), {});
  assert.deepEqual(tabDataPayload(["a"]), {});
  assert.deepEqual(tabDataPayload("x"), {});

  // A fresh object each call: one tab's payload must not leak into another.
  assert.notEqual(tabDataPayload(), tabDataPayload());
});

test("the primary path passes data and reads the object-shaped result", async () => {
  const source = fs.readFileSync(TABS_TS, "utf8");

  // `data` must be in the options: the tab record has to carry an object.
  assert.match(
    source,
    /tabs\.add\(\{[\s\S]*?data: tabDataPayload\(\)[\s\S]*?\}\)/,
    "Zotero_Tabs.add must be given a data payload",
  );
  // The result is parsed by the shared helpers, not by a bare typeof check.
  assert.match(source, /const tabID = readTabID\(result\);/, "id must come from the shared parser");
  assert.match(
    source,
    /readTabContainer\(result\)/,
    "the container must be taken from the result when Zotero supplies one",
  );

  // A XUL host document cannot host remote content in an HTML iframe: the
  // node must be a XUL iframe with type="content", or the page stays blank.
  assert.match(
    source,
    /node\.setAttribute\("type", "content"\)/,
    "the iframe must be a content iframe",
  );
  assert.match(
    source,
    /createXULElement\("browser"\)/,
    "a XUL document must create a XUL browser, not an iframe",
  );
  // Zotero's own content browser sets these; without them FX128+ stays blank
  // and never even attempts the navigation (真机 evidence on Zotero 10.0.8).
  assert.match(
    source,
    /setAttribute\("remote", "false"\)/,
    'the XUL browser needs remote="false"',
  );
  assert.match(
    source,
    /setAttribute\("maychangeremoteness", "true"\)/,
    "the XUL browser needs maychangeremoteness",
  );
  // XUL parents predate `append`; appendChild inserts into both worlds.
  assert.match(
    source,
    /container\.appendChild\(iframe\)/,
    "the mount must use appendChild, not append",
  );
  assert.match(
    source,
    /there\.is\.only\.xul/,
    "the XUL branch must be keyed on the document namespace, not a guess",
  );
});
