"use strict";

// Anchors the item / tools menus and the offline guide card (U6: 菜单入口与
// 离线降级引导).
//
// The logic under test lives in plugin/src/menus/menuCore.mjs: plain ESM with
// no Zotero, DOM or TypeScript globals, so `node --test` runs the real menu
// contract, the real greying rules and the real comparison-set semantics
// without npm install or a Zotero process.
//
// What is deliberately NOT faked here: the DOM half (menu rendering, the XUL
// guide panel, the ProgressWindow) is 真机验收 territory. What is asserted
// about the tooling is the wiring — the table is the only list, the menu goes
// through `Zotero.MenuManager.registerMenu` with the manifest's pluginID, the
// greying probe goes through the S4 client, and every visible string comes
// from the Fluent bundles.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const ROOT = path.join(__dirname, "..");
const PLUGIN_DIR = path.join(ROOT, "plugin");
const MENU_CORE = path.join(PLUGIN_DIR, "src", "menus", "menuCore.mjs");
const MENU_TS = path.join(PLUGIN_DIR, "src", "menus", "menus.ts");
const REGISTRY_TS = path.join(PLUGIN_DIR, "src", "registry.ts");
const STATUS_TS = path.join(PLUGIN_DIR, "src", "status", "status.ts");
const PREFS_TS = path.join(PLUGIN_DIR, "src", "prefs", "prefs.ts");
const ADDON_TS = path.join(PLUGIN_DIR, "src", "addon.ts");
const MANIFEST = path.join(PLUGIN_DIR, "addon", "manifest.json");
const PACKAGE_JSON = path.join(PLUGIN_DIR, "package.json");
const AGENTS_PY = path.join(ROOT, "kbapp", "workflows", "agents.py");
const FTL_DIR = path.join(PLUGIN_DIR, "addon", "locale");

/**
 * `Zotero.MenuManager`'s own lists (xpcom/pluginAPI/menuManager.js
 * VALID_TARGETS / VALID_MENU_TYPES). Copied here on purpose: the plugin must
 * stay inside the API's contract, and a target typo otherwise only shows up
 * as a warning on a real machine.
 */
const VALID_TARGETS = [
  "main/menubar/file",
  "main/menubar/edit",
  "main/menubar/view",
  "main/menubar/go",
  "main/menubar/tools",
  "main/menubar/help",
  "main/library/item",
  "main/library/collection",
  "main/library/addAttachment",
  "main/library/addNote",
  "main/tab",
  "reader/menubar/file",
  "reader/menubar/edit",
  "reader/menubar/view",
  "reader/menubar/go",
  "reader/menubar/window",
  "itemPane/info/row",
  "notesPane/addItemNote",
  "notesPane/addStandaloneNote",
  "sidenav/locate",
];
const GROUPED_TARGETS = ["main/library/collection", "main/library/item"];
const VALID_MENU_TYPES = ["menuitem", "separator", "submenu"];

async function loadCore() {
  return import(pathToFileURL(MENU_CORE).href);
}

function read(file) {
  return fs.readFileSync(file, "utf8");
}

/** Parse one .ftl into name → value. Comments and blanks are skipped. */
function parseFtl(locale) {
  const file = path.join(FTL_DIR, locale, "addon.ftl");
  const entries = new Map();
  for (const line of read(file).split(/\r?\n/)) {
    const match = /^([a-zA-Z0-9_-]+)\s*=\s*(.*)$/.exec(line);
    if (match) {
      entries.set(match[1], match[2]);
    }
  }
  return entries;
}

const AVAILABLE = Object.freeze({
  state: "available",
  reason: null,
  ok: true,
  status: 200,
  serviceVersion: "0.1.0",
  apiVersion: "v2",
});
const OUTDATED = Object.freeze({
  state: "unsupported",
  reason: "version-too-old",
  ok: false,
  status: 200,
  serviceVersion: "0.0.8",
  apiVersion: "v2",
});
const OFFLINE = Object.freeze({ state: "offline", reason: "unreachable", ok: false, status: 0 });

test("the contract is one table: unique ids, API-valid shapes, complete groups", async () => {
  const core = await loadCore();

  const ids = core.MENU_ENTRIES.map((entry) => entry.id);
  assert.equal(new Set(ids).size, ids.length, "menu entry ids must be unique");

  const groupIds = core.MENU_GROUPS.map((group) => group.id);
  assert.deepEqual(groupIds, ["item", "tools"]);
  for (const group of core.MENU_GROUPS) {
    assert.ok(VALID_TARGETS.includes(group.target), `bad target ${group.target}`);
    assert.equal(typeof group.menuID, "string");
    assert.ok(group.menuID.length > 0);
    assert.ok(["submenu", "flat"].includes(group.shape));
    if (group.shape === "submenu") {
      assert.equal(typeof group.labelKey, "string", "a submenu needs a label");
    } else {
      assert.equal(group.labelKey, null, "a flat group has no submenu label");
    }
  }
  assert.equal(new Set(core.MENU_GROUPS.map((group) => group.menuID)).size, 2);

  for (const entry of core.MENU_ENTRIES) {
    assert.ok(groupIds.includes(entry.group), `${entry.id} has no group`);
    assert.ok(VALID_MENU_TYPES.includes(entry.menuType), `${entry.id} menuType`);
    // Grouped targets reject top-level separators, and this feature never
    // needs one inside its submenu either.
    assert.notEqual(entry.menuType, "separator", `${entry.id} must not be a separator`);
    assert.match(entry.labelKey, /^menu-item-/, `${entry.id} label namespace`);
    assert.ok(["none", "any", "single"].includes(entry.selection));
    assert.ok(["none", "add", "run", "clear"].includes(entry.compare));
    assert.ok(["always", "offline", "compare"].includes(entry.visible));
    assert.equal(typeof entry.needsService, "boolean");
    assert.equal(typeof entry.command, "string");
    assert.ok(entry.command.length > 0);
  }

  // Every group has at least one registered entry, and the renderer's spec
  // mirrors the table.
  const specs = core.menuSpecs();
  assert.equal(specs.length, core.MENU_GROUPS.length);
  for (const spec of specs) {
    assert.ok(spec.entries.length > 0, `${spec.id} would render an empty menu`);
    assert.ok(
      !GROUPED_TARGETS.includes(spec.target) ||
        spec.shape === "submenu" ||
        spec.entries.every((entry) => entry.menuType !== "separator"),
      "no top-level separator on a grouped target",
    );
  }
  const specIds = specs.flatMap((spec) => spec.entries.map((entry) => entry.id)).sort();
  const registeredIds = core.registeredEntries().map((entry) => entry.id).sort();
  assert.deepEqual(specIds, registeredIds, "the spec must be the registered table");
});

test("the planned offline-graph entry is documented but never registered", async () => {
  const core = await loadCore();
  const blocked = core.MENU_ENTRIES.find(
    (entry) => entry.id === "tools-open-offline-graph",
  );
  assert.ok(blocked, "the planned entry must stay in the contract table");
  assert.equal(blocked.registered, false, "the blocked entry must not be registered");
  assert.equal(typeof blocked.blockedReasonKey, "string");
  assert.match(blocked.blockedReasonKey, /^menu-/);

  const registered = core.registeredEntries().map((entry) => entry.id);
  assert.equal(registered.includes("tools-open-offline-graph"), false);
  const specIds = core
    .menuSpecs()
    .flatMap((spec) => spec.entries.map((entry) => entry.id));
  assert.equal(specIds.includes("tools-open-offline-graph"), false);

  // The reason is user-visible in the Fluent bundle, not a bare comment.
  for (const locale of ["en-US", "zh-CN"]) {
    assert.ok(parseFtl(locale).has(blocked.blockedReasonKey), `${locale} missing blocker copy`);
  }
});

test("every rendered string is a Fluent key present in both bundles", async () => {
  const core = await loadCore();
  const keys = core.menuFluentKeys();
  assert.ok(keys.length >= 30, "the menu layer declares its strings as keys");
  const en = parseFtl("en-US");
  const zh = parseFtl("zh-CN");
  for (const key of keys) {
    assert.match(key, /^menu-[a-z0-9-]+$/, `unexpected menu key ${key}`);
    for (const [locale, ftl] of [["en-US", en], ["zh-CN", zh]]) {
      assert.ok(ftl.has(key), `${locale} is missing ${key}`);
      assert.ok(ftl.get(key).trim().length > 0, `${locale} ${key} is empty`);
    }
  }
  // The prefix belongs to the build; a key already carrying it would become
  // `littrace-littrace-...` at runtime.
  for (const key of keys) {
    assert.equal(key.startsWith("littrace-"), false, `${key} must stay bare in the .ftl`);
  }
});

test("the service gate only lets a compatible, online service through", async () => {
  const core = await loadCore();

  assert.equal(core.serviceStateOf(AVAILABLE), "online");
  assert.equal(core.serviceStateOf(OUTDATED), "incompatible");
  assert.equal(core.serviceStateOf(OFFLINE), "offline");
  assert.equal(core.serviceStateOf(null), "checking");
  assert.equal(core.serviceStateOf(undefined), "checking");
  assert.equal(core.serviceStateOf(AVAILABLE, { pending: true }), "checking");
  // Unknown shapes never claim green.
  for (const unknown of [{}, { state: "future-state" }, 42, "available"]) {
    assert.equal(core.serviceStateOf(unknown), "offline", `unexpected for ${unknown}`);
  }

  const evidence = core.MENU_ENTRIES.find((entry) => entry.id === "item-evidence");
  assert.ok(evidence, "missing item-evidence");
  const online = core.entryStateOf(evidence, { health: AVAILABLE, selection: 1 });
  assert.equal(online.enabled, true);
  assert.equal(online.reasonKey, null);

  const offline = core.entryStateOf(evidence, { health: OFFLINE, selection: 1 });
  assert.equal(offline.enabled, false);
  assert.equal(offline.reasonKey, "menu-gate-offline");

  const incompatible = core.entryStateOf(evidence, { health: OUTDATED, selection: 1 });
  assert.equal(incompatible.enabled, false);
  assert.equal(incompatible.reasonKey, "menu-gate-incompatible");

  const checking = core.entryStateOf(evidence, { health: null, selection: 1 });
  assert.equal(checking.enabled, false);
  assert.equal(checking.reasonKey, "menu-gate-checking");

  // 诊断 stays clickable in every state; the guide entry is the offline door
  // and must be visible exactly when the service is not usable.
  const diagnostics = core.MENU_ENTRIES.find((entry) => entry.id === "tools-diagnostics");
  for (const health of [AVAILABLE, OUTDATED, OFFLINE, null]) {
    const state = core.entryStateOf(diagnostics, { health });
    assert.equal(state.enabled, true, `diagnostics greyed for ${health && health.state}`);
    assert.equal(state.visible, true);
  }
  const guideOffline = core.entryStateOf(
    core.MENU_ENTRIES.find((entry) => entry.id === "item-guide"),
    { health: OFFLINE },
  );
  assert.equal(guideOffline.visible, true);
  assert.equal(guideOffline.enabled, true);
  const guideOnline = core.entryStateOf(
    core.MENU_ENTRIES.find((entry) => entry.id === "item-guide"),
    { health: AVAILABLE },
  );
  assert.equal(guideOnline.visible, false);
});

test("the selection and comparison gates grey the rest, service reason first", async () => {
  const core = await loadCore();
  const byId = new Map(core.MENU_ENTRIES.map((entry) => [entry.id, entry]));
  const stateOf = (id, input) => core.entryStateOf(byId.get(id), input);

  // 至少一条:查证据 / 加入对比集.
  for (const id of ["item-evidence", "item-compare-add"]) {
    const none = stateOf(id, { health: AVAILABLE, selection: 0 });
    assert.equal(none.enabled, false, `${id} must need a selection`);
    assert.equal(none.reasonKey, "menu-gate-selection");
    assert.equal(stateOf(id, { health: AVAILABLE, selection: 1 }).enabled, true);
  }
  // 恰好一条:在谱系中定位 / 解析此文献.
  for (const id of ["item-locate", "item-parse"]) {
    assert.equal(stateOf(id, { health: AVAILABLE, selection: 1 }).enabled, true);
    const none = stateOf(id, { health: AVAILABLE, selection: 0 });
    assert.equal(none.reasonKey, "menu-gate-selection-single");
    const two = stateOf(id, { health: AVAILABLE, selection: 2 });
    assert.equal(two.enabled, false);
    assert.equal(two.reasonKey, "menu-gate-selection-single");
  }
  // 对比集已满:加入置灰并说明「已满」.
  const full = stateOf("item-compare-add", {
    health: AVAILABLE,
    selection: 1,
    compareSize: core.COMPARE_MAX,
  });
  assert.equal(full.enabled, false);
  assert.equal(full.reasonKey, "menu-gate-compare-full");
  // 与对比集对比:2–4 篇可用,其余置灰.
  for (const size of [0, 1, 5]) {
    const state = stateOf("item-compare-run", { health: AVAILABLE, compareSize: size });
    assert.equal(state.enabled, false, `compare-run must reject ${size}`);
    assert.equal(state.reasonKey, "menu-gate-compare-size");
  }
  for (const size of [2, 3, 4]) {
    assert.equal(
      stateOf("item-compare-run", { health: AVAILABLE, compareSize: size }).enabled,
      true,
      `compare-run must accept ${size}`,
    );
  }
  // 清空对比集:服务离线也可用,但只在有内容时出现.
  const clearEmpty = stateOf("item-compare-clear", { health: OFFLINE, compareSize: 0 });
  assert.equal(clearEmpty.visible, false);
  const clearFull = stateOf("item-compare-clear", { health: OFFLINE, compareSize: 4 });
  assert.equal(clearFull.visible, true);
  assert.equal(clearFull.enabled, true);
  // Precedence: with the service down the reason must be the service, not
  // the selection — that is the fact the user has to act on.
  const both = stateOf("item-evidence", { health: OFFLINE, selection: 0 });
  assert.equal(both.reasonKey, "menu-gate-offline");

  // The exported predicates accept a raw input as well as a normalised
  // context; a half-built context must not read `undefined` as "online".
  assert.equal(
    core.gateReasonOf(byId.get("item-evidence"), { health: OFFLINE, selection: 1 }),
    "menu-gate-offline",
  );
  assert.equal(
    core.visibleOf(byId.get("item-guide"), { health: OFFLINE }),
    true,
  );
  assert.equal(
    core.gateReasonOf(byId.get("item-evidence"), { service: "online", selection: 2 }),
    null,
  );
});

test("the comparison set is 2–4 papers, deduped, and never silently evicts", async () => {
  const core = await loadCore();
  assert.equal(core.COMPARE_MIN, 2);
  assert.equal(core.COMPARE_MAX, 4);

  const target = (key, libraryID = 1) => ({ key, libraryID });
  let set = core.EMPTY_COMPARE_SET;

  const first = core.compareSetAdd(set, [target("A"), target("B")]);
  assert.equal(first.added, 2);
  assert.equal(first.size, 2);
  set = first.entries;

  // Duplicate is reported, not added twice.
  const dup = core.compareSetAdd(set, [target("A")]);
  assert.equal(dup.added, 0);
  assert.equal(dup.duplicate, 1);
  assert.equal(dup.size, 2);
  set = dup.entries;

  // Same key in another library is a different paper.
  const otherLibrary = core.compareSetAdd(set, [target("A", 2)]);
  assert.equal(otherLibrary.added, 1);
  set = otherLibrary.entries;

  // The cap refuses the 5th and keeps the four already chosen, in order.
  const overflow = core.compareSetAdd(set, [target("D"), target("E")]);
  assert.equal(overflow.added, 1);
  assert.equal(overflow.full, 1);
  assert.equal(overflow.size, 4);
  assert.deepEqual(
    core.compareSetPaperIds(overflow.entries),
    ["z:1:A", "z:1:B", "z:2:A", "z:1:D"],
  );

  // Removing one is by library-qualified key.
  const removed = core.compareSetRemove(overflow.entries, "1:B");
  assert.equal(removed.removed, true);
  assert.deepEqual(core.compareSetPaperIds(removed.entries), ["z:1:A", "z:2:A", "z:1:D"]);

  // Readiness mirrors the service's 2–4 bound.
  assert.equal(core.compareReadiness(core.compareSetAdd(core.EMPTY_COMPARE_SET, [target("A")]).entries).ready, false);
  assert.equal(core.compareReadiness(core.EMPTY_COMPARE_SET).ready, false);
  const ready = core.compareReadiness(overflow.entries);
  assert.equal(ready.ready, true);
  assert.equal(ready.reasonKey, null);

  // The submission body is the service contract: kind=compare + paper_ids.
  const request = core.compareRunRequest(overflow.entries);
  assert.equal(request.path, "/api/agent-runs");
  assert.equal(request.body.kind, "compare");
  assert.deepEqual(request.body.paper_ids, ["z:1:A", "z:1:B", "z:2:A", "z:1:D"]);
  assert.equal(
    core.compareRunRequest(core.compareSetAdd(core.EMPTY_COMPARE_SET, [target("A")]).entries),
    null,
  );

  // The submitted ids are the resolved ones, deduped; a run whose papers
  // collapse to fewer than two is refused locally instead of 400ing.
  assert.deepEqual(
    core.compareRunBody(["doi:10.1/a", "doi:10.1/b", "doi:10.1/a"]).body.paper_ids,
    ["doi:10.1/a", "doi:10.1/b"],
  );
  assert.equal(core.compareRunBody(["doi:10.1/a"]), null);
  assert.equal(core.compareRunBody(["a", "b", "c", "d", "e"]), null);
  assert.equal(core.compareRunBody([]), null);

  // One unresolvable paper must not be silently dropped: the edge resolves
  // every target and refuses the whole run with the specific reason.
  const outcome = core.resolutionOutcome([
    { paperId: "z:1:A", resolvedId: "z:1:A" },
    { paperId: "z:1:GHOST", resolvedId: null },
    { paperId: "z:1:LEGACY", resolvedId: "doi:10.1/legacy" },
    { paperId: "", resolvedId: "ignored" },
  ]);
  assert.deepEqual(outcome.resolved, ["z:1:A", "doi:10.1/legacy"]);
  assert.deepEqual(outcome.missing, ["z:1:GHOST"]);
  assert.deepEqual(core.resolutionOutcome(undefined).missing, []);

  // The service's own bound, read from its source: a drift on either side
  // must fail this test instead of turning into a 400 for the user.
  const server = read(AGENTS_PY);
  const bound = /kind == 'compare' and \(not (\d+) <= len\(set\(ids\)\) <= (\d+)\)/.exec(server);
  assert.ok(bound, "the service's compare bound moved; update COMPARE_MIN/MAX with it");
  assert.equal(core.COMPARE_MIN, Number(bound[1]));
  assert.equal(core.COMPARE_MAX, Number(bound[2]));

  // Feedback selection: added / already there / full / partially accepted.
  assert.equal(core.compareAddFeedback(first), core.FEEDBACK_KEYS.compareAdded);
  assert.equal(core.compareAddFeedback(dup), core.FEEDBACK_KEYS.compareDuplicate);
  assert.equal(core.compareAddFeedback(overflow), core.FEEDBACK_KEYS.comparePartial);
  assert.equal(
    core.compareAddFeedback({ entries: [], added: 0, duplicate: 0, full: 1, size: 4 }),
    core.FEEDBACK_KEYS.compareFull,
  );
  // Mixed refusal: the cap wins over 「already in the set」, because a refused
  // paper is the one the user cannot see in the set.
  assert.equal(
    core.compareAddFeedback({ entries: [], added: 0, duplicate: 1, full: 2, size: 4 }),
    core.FEEDBACK_KEYS.compareFull,
  );
  // A partially accepted add must name the refused papers: the message is the
  // only place the overflow is reported, and it uses the count passed in.
  const partialKey = core.FEEDBACK_KEYS.comparePartial;
  for (const locale of ["en-US", "zh-CN"]) {
    const line = parseFtl(locale).get(partialKey);
    assert.ok(line, `${locale} is missing ${partialKey}`);
    assert.match(line, /\$full/, `${locale} ${partialKey} must state how many were refused`);
    assert.match(line, /\$count/, `${locale} ${partialKey} must state how many were added`);
  }
});

test("paper identity is z:<libraryID>:<key>, parents included, bad ids refused", async () => {
  const core = await loadCore();
  assert.deepEqual(core.targetOf({ key: "ABCD1234", libraryID: 1 }), {
    libraryID: 1,
    key: "ABCD1234",
  });
  assert.equal(core.paperIdOf({ libraryID: 1, key: "ABCD1234" }), "z:1:ABCD1234");

  // An attachment/note is represented by its parent (same library).
  assert.deepEqual(
    core.targetOf({ key: "CHILD", parentKey: "PARENT", libraryID: 7 }),
    { libraryID: 7, key: "PARENT" },
  );

  // ADR 0028: a missing or non-positive library is refused, never guessed as 0.
  for (const libraryID of [0, -1, null, undefined, "", "abc", 1.5, NaN]) {
    assert.equal(core.targetOf({ key: "ABCD", libraryID }), null, `libraryID ${libraryID}`);
  }
  assert.equal(core.targetOf({ key: "", libraryID: 1 }), null);
  assert.equal(core.targetOf(null), null);
  assert.equal(core.targetOf("ABCD"), null);

  const selection = core.targetsOf([
    { key: "A", libraryID: 1 },
    { key: "A", libraryID: 1 },
    { key: "B", libraryID: 2 },
    { key: "C", libraryID: 0 },
    null,
  ]);
  assert.deepEqual(
    selection.targets.map((target) => core.paperIdOf(target)),
    ["z:1:A", "z:2:B"],
  );
  assert.equal(selection.skipped, 2);
  assert.equal(core.targetsOf(undefined).targets.length, 0);
});

test("the guide card carries the volume/time budget and every action", async () => {
  const core = await loadCore();
  const en = parseFtl("en-US");
  const zh = parseFtl("zh-CN");

  for (const size of [0, 1, 2, 3, 4]) {
    const view = core.guideView({ health: OFFLINE, compareSize: size });
    assert.equal(view.titleKey, core.GUIDE_CARD.titleKey);
    assert.deepEqual(view.bodyKeys, core.GUIDE_CARD.bodyKeys);
    assert.deepEqual(
      view.actions.map((action) => action.id),
      ["settings", "recheck", "close"],
    );
  }
  // The incompatible variant swaps only the first line: the next step differs.
  const incompatible = core.guideView({ health: OUTDATED });
  assert.equal(incompatible.bodyKeys[0], core.GUIDE_CARD.incompatibleKey);
  assert.deepEqual(
    incompatible.bodyKeys.slice(1),
    core.GUIDE_CARD.bodyKeys.slice(1),
  );
  // A service that answered is not 「未运行」: the card title and the guide
  // menu row must not claim it is, or the card's own first line contradicts
  // the title above it.
  assert.equal(incompatible.titleKey, core.GUIDE_CARD.incompatibleTitleKey);
  const offlineView = core.guideView({ health: OFFLINE });
  assert.equal(offlineView.titleKey, core.GUIDE_CARD.titleKey);

  const guideEntry = core.MENU_ENTRIES.find((entry) => entry.id === "item-guide");
  assert.equal(core.labelKeyOf(guideEntry, "offline"), "menu-item-guide");
  assert.equal(core.labelKeyOf(guideEntry, "checking"), "menu-item-guide");
  assert.equal(
    core.labelKeyOf(guideEntry, "incompatible"),
    "menu-item-guide-incompatible",
  );
  assert.equal(core.labelKeyOf(guideEntry, "online"), "menu-item-guide");
  // Entries without an incompatible variant keep their static key.
  const evidenceEntry = core.MENU_ENTRIES.find((entry) => entry.id === "item-evidence");
  assert.equal(core.labelKeyOf(evidenceEntry, "incompatible"), "menu-item-evidence");

  // The two incompatible strings must actually say something else than the
  // offline ones, in both bundles.
  assert.doesNotMatch(zh.get("menu-item-guide-incompatible"), /未运行/);
  assert.doesNotMatch(zh.get("menu-guide-title-incompatible"), /未运行/);
  assert.doesNotMatch(en.get("menu-item-guide-incompatible"), /not running/i);
  assert.doesNotMatch(en.get("menu-guide-title-incompatible"), /not running/i);

  // The budget copy is what makes the card honest; assert it says so, in both
  // bundles, instead of trusting the key name.
  assert.match(zh.get("menu-guide-budget-size"), /MB/);
  assert.match(zh.get("menu-guide-budget-time"), /分钟/);
  assert.match(en.get("menu-guide-budget-size"), /MB/);
  assert.match(en.get("menu-guide-budget-time"), /minute/i);
  // 绝无半功能状态 is part of the promise, in both bundles.
  assert.match(zh.get("menu-guide-body-no-half"), /置灰/);
  assert.match(en.get("menu-guide-body-no-half"), /greyed out/);
});

test("the verdict cache expires by TTL instead of trusting a stale answer", async () => {
  const core = await loadCore();
  const cache = core.createVerdictCache({ ttl: 1000 });
  assert.equal(cache.read(), null);
  assert.equal(cache.stale(0), true, "an unwritten cache is stale");

  cache.write(AVAILABLE, 1000);
  assert.equal(cache.read(), AVAILABLE);
  assert.equal(cache.stale(1500), false);
  assert.equal(cache.stale(2000), true);

  cache.write(OFFLINE, 3000);
  assert.equal(core.serviceStateOf(cache.read()), "offline");
  cache.clear();
  assert.equal(cache.read(), null);
  assert.equal(cache.stale(3000), true);
});

test("the menus are registered once, through the plugin's single entry", () => {
  const registry = read(REGISTRY_TS);
  assert.match(
    registry,
    /import \{ createMenus \} from "\.\/menus\/menus";/,
    "registry must import the menus from their module",
  );
  // Process-wide registration with a disposer: MenuManager keeps one registry
  // keyed by menuID, so a per-window registration would be a duplicate id.
  assert.match(
    registry,
    /const menus = createMenus\(\);[\s\S]*?registerGlobal\("menus", \(\) => \{[\s\S]*?menus\.dispose\(\);/,
    "the menus must be a global registration with a disposer",
  );

  const menus = read(MENU_TS);
  const pkg = JSON.parse(read(PACKAGE_JSON));
  // pluginID is the manifest id, never a second literal.
  assert.match(menus, /pluginID: config\.addonID/);
  assert.match(menus, /menuID: spec\.menuID/);
  assert.match(menus, /target: spec\.target/);
  assert.match(menus, /menus: menusFor\(spec\)/);
  assert.match(menus, /api\.unregisterMenu\(spec\.menuID\)/);
  assert.match(menus, /Zotero as \{ MenuManager\?:/);
  // l10nID must be the namespaced id the build produces, via ftlId().
  assert.match(menus, /l10nID: ftlId\(entry\.labelKey\)/);
  assert.match(menus, /l10nID: ftlId\(labelKey\)/);

  const manifest = JSON.parse(read(MANIFEST));
  assert.equal(manifest.applications.zotero.strict_min_version, "8.0");
  assert.equal(
    Number(manifest.applications.zotero.strict_min_version.split(".")[0]) >= 8,
    true,
    "MenuManager requires Zotero 8+",
  );
  assert.equal(pkg.config.addonID, manifest.applications.zotero.id);

  // The comparison set is exposed as a reserved consumer interface for U3's
  // comparison entry, not buried in the menu module.
  const addon = read(ADDON_TS);
  assert.match(addon, /import \{ comparisonSet \} from "\.\/menus\/menus";/);
  assert.match(addon, /get comparisonSet\(\): ReadonlyArray<string>/);
});

test("the greying probe goes through the S4 client and no other HTTP path", () => {
  const menus = read(MENU_TS);
  const registry = read(REGISTRY_TS);
  const status = read(STATUS_TS);

  // One request path: the S4 client's probe (GET /api/version).
  assert.match(menus, /import \{ probeHealth \} from "\.\.\/status\/diagnostics";/);
  assert.match(menus, /await probeHealth\(\)/);
  assert.doesNotMatch(menus, /Zotero\.HTTP/, "the menus must not open their own HTTP path");
  // The onShowing hook must repaint from a probe/cache, not from an interval.
  assert.match(menus, /onShowing:/);
  assert.match(menus, /void refreshVerdict\(\)/);
  assert.doesNotMatch(menus, /setInterval/);

  // 「服务诊断」 reuses U5's panel instead of growing a second one.
  assert.match(menus, /import \{ openWindowDiagnostics \} from "\.\.\/status\/status";/);
  assert.match(menus, /openWindowDiagnostics\(window\)/);
  assert.match(status, /export function openWindowDiagnostics\(win: Window\): boolean/);
  // The guide card's 「打开设置页」 navigates by the pane id U5 registered.
  const prefs = read(PREFS_TS);
  assert.match(prefs, /export const PANE_ID = "littrace-preferences";/);
  assert.match(menus, /import \{ PANE_ID \} from "\.\.\/prefs\/prefs";/);
  assert.match(menus, /internal\.openPreferences\(PANE_ID\)/);
  // Commands open the U4 tab host, with the browser as the visible fallback.
  assert.match(menus, /import \{ openFunctionTab \} from "\.\.\/tabs\/tabs";/);
  assert.match(menus, /openFunctionTab\(window, id, options\)/);
  assert.match(menus, /Zotero\.launchURL\(url\)/);
});

test("no visible menu string is hardcoded in the TypeScript edge", () => {
  const menus = read(MENU_TS);
  // A quoted non-empty literal assigned to textContent is exactly the string
  // that would stay English in the zh-CN build; `= ""` (clearing) is fine.
  assert.doesNotMatch(menus, /textContent\s*=\s*["'][^"']+["']/);
  assert.doesNotMatch(
    menus,
    /setAttribute\(\s*["'](?:label|tooltiptext|aria-label)["']\s*,\s*["']/,
  );
  for (const match of menus.matchAll(/textContent\s*=\s*`([^`]*)`/g)) {
    assert.match(match[1], /\bt\(/, "a template literal must still resolve through t()");
  }
  // Every visible label goes through the Fluent lookup, and the guide entry's
  // label is chosen per service state (offline vs incompatible).
  assert.match(menus, /labelKeyOf\(entry, input\.service as MenuServiceState\)/);
  assert.match(menus, /t\(labelKey\)/);
  assert.match(menus, /t\(view\.titleKey\)/);
  assert.match(menus, /t\(key, args\)/);
});

test("a compare run resolves every paper before it is submitted", () => {
  const menus = read(MENU_TS);
  // A bare `z:<lib>:<key>` only resolves when it is the stored id; a paper
  // under a legacy `doi:` id otherwise makes the service 400 the whole run.
  // The edge therefore resolves each target first (same two-hop as parse).
  assert.match(menus, /await resolvePaperId\(paperId\)/);
  assert.match(menus, /\/api\/v2\/paper\/sections\?paper=\$\{encodeURIComponent\(paper\)\}/);
  assert.match(menus, /compareRunBody\(outcome\.resolved\)/);
  // An unindexed paper is named as such; a service error shows its own text
  // instead of being reported as 「未确认」.
  assert.match(menus, /FEEDBACK_KEYS\.compareUnindexed/);
  assert.match(menus, /FEEDBACK_KEYS\.compareError/);
  assert.match(menus, /detail: errorDetail\(error\)/);
  assert.match(menus, /resolvePaperId\(paperId\)/);
});

test("the parse command distinguishes unindexed from unselected", () => {
  const menus = read(MENU_TS);
  const start = menus.indexOf("async function runParse(");
  const end = menus.indexOf("\nfunction runDiagnostics(", start);
  assert.ok(start >= 0 && end > start, "runParse not found");
  const body = menus.slice(start, end);
  // The paper is resolved first (a legacy `doi:` id otherwise 400s), and the
  // two failures get their own lines instead of borrowing the selection gate.
  assert.match(body, /await resolvePaperId\(paper\)/);
  assert.match(body, /FEEDBACK_KEYS\.parseNotIndexed/);
  assert.match(body, /FEEDBACK_KEYS\.parseError/);
  assert.equal(
    (body.match(/FEEDBACK_KEYS\.selectFirst/g) || []).length,
    1,
    "selectFirst may only describe the no-selection branch",
  );
  assert.doesNotMatch(body, /"解析前解析文献"/);
  // Both new lines are real Fluent keys.
  const en = parseFtl("en-US");
  const zh = parseFtl("zh-CN");
  for (const key of ["menu-feedback-parse-not-indexed", "menu-feedback-parse-error"]) {
    assert.ok(en.has(key), `en-US missing ${key}`);
    assert.ok(zh.has(key), `zh-CN missing ${key}`);
  }
});

test("the pure core has no Zotero or DOM globals", () => {
  const core = read(MENU_CORE);
  // Strip block comments and `*`/`//` lines first: the module's own
  // documentation names `Zotero.MenuManager` to explain the split, and that
  // sentence is not a reference to the global.
  const code = core
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      return !trimmed.startsWith("//") && !trimmed.startsWith("*");
    })
    .join("\n");
  assert.doesNotMatch(code, /\bZotero\./);
  assert.doesNotMatch(code, /\bdocument\./);
  assert.doesNotMatch(code, /\bwindow\./);
  // It must reuse the status layer's health mapping instead of a second one.
  assert.match(core, /import \{ dotStateOf \} from "\.\.\/status\/statusCore\.mjs";/);
  assert.match(code, /dotStateOf\(health\)/);
});
