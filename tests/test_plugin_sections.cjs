"use strict";

// Anchors the three item-pane sections (U2): 证据 / 谱系关系 / 精读.
//
// The logic under test lives in plugin/src/itemSectionsCore.mjs — plain ESM
// with no DOM and no Zotero globals — so this file needs node built-ins only
// and no npm install. What is locked here is exactly the part that decides
// whether one item's data can appear under another item:
//
//   1. **Cache key.** `z:<libraryID>:<itemKey>`, the same identity
//      kbapp/library/sources.py gives a library paper; non-items and rows
//      without a key produce "" (nothing to ask about).
//   2. **Lazy loading.** A cache hit issues no request; concurrent fetches for
//      one key share one request; a rejection caches nothing so retry retries.
//   3. **Skeleton → fill.** `planRender` always yields a paintable state
//      synchronously: loading when it must fetch, ready/empty from the cache.
//   4. **Four states.** loading / empty / error(+retry) / offline guide card,
//      each with the message keys the FTL packs must contain.
//   5. **No cross-talk.** `acceptResponse` + `createRenderGeneration` drop an
//      answer that arrives after the item changed or a newer paint started.
//
// The DOM/Zotero half (plugin/src/itemSections.ts) is checked by reading its
// source, the convention tests/test_plugin_hooks.cjs established (ADR 0010):
// no plugin code is executed against a fake Zotero here.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const PLUGIN_DIR = path.join(__dirname, "..", "plugin");
const CORE_URL = pathToFileURL(
  path.join(PLUGIN_DIR, "src", "itemSectionsCore.mjs"),
).href;
const SECTIONS_TS = path.join(PLUGIN_DIR, "src", "itemSections.ts");
const REGISTRY_TS = path.join(PLUGIN_DIR, "src", "registry.ts");
const FTL = {
  "en-US": path.join(PLUGIN_DIR, "addon", "locale", "en-US", "addon.ftl"),
  "zh-CN": path.join(PLUGIN_DIR, "addon", "locale", "zh-CN", "addon.ftl"),
};
const ICON_DIR = path.join(PLUGIN_DIR, "addon", "content", "icons");

let corePromise;
function core() {
  corePromise ??= import(CORE_URL);
  return corePromise;
}

/** A Zotero item as the section hooks see it. */
function item(key, libraryID = 1) {
  return { _objectType: "item", key, libraryID, id: 1000 };
}

function snapshot(overrides = {}) {
  return {
    paper_id: "z:1:ITEM0001",
    title: "A Paper",
    evidence: {
      indexed: true,
      index_version: "v1",
      counts: { paper_text: 12, figure: 3, formula: 1 },
      total: 16,
      excerpts: [{ kind: "paper_text", page: 7, excerpt: "adaptive sampling" }],
    },
    lineage: {
      verified: 2,
      pending: 1,
      citations: 4,
      items: [
        {
          id: "rel-1",
          kind: "inherits",
          label: "方法继承",
          status: "confirmed",
          grade: "fulltext",
          grade_label: "原文已核 · 人工确认",
          peer_id: "z:1:PEER",
          peer_title: "Earlier Work",
          peer_year: 2015,
          direction: "previous",
          explanation: "继承采样策略",
        },
      ],
    },
    reading: {
      state: "partial",
      total_pages: 12,
      read_pages: 5,
      missing_pages: [9],
      gaps: ["第9页不可读"],
      classification: { NATIVE_TEXT: 9, SCANNED: 3 },
      documents: 1,
      can_parse: true,
    },
    ...overrides,
  };
}

function functionBody(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `function ${name} must exist`);
  let depth = 0;
  let index = source.indexOf("{", start);
  const open = index;
  for (; index < source.length; index += 1) {
    if (source[index] === "{") {
      depth += 1;
    } else if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) {
        return source.slice(open, index + 1);
      }
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

/* ── 1. Cache key ────────────────────────────────────────────────────── */

test("the cache key is the service's own library-paper identity", async () => {
  const { cacheKeyFor } = await core();
  assert.equal(cacheKeyFor(item("ABCD1234")), "z:1:ABCD1234");
  assert.equal(cacheKeyFor(item("ABCD1234", 3)), "z:3:ABCD1234");
  // A numeric string is tolerated; a non-numeric library ID falls back to 1
  // rather than producing "z:NaN:KEY".
  assert.equal(cacheKeyFor(item("KEY", "2")), "z:2:KEY");
  assert.equal(cacheKeyFor(item("KEY", "not-a-number")), "z:1:KEY");
  // Defaults may supply the library when the item does not carry one.
  assert.equal(cacheKeyFor({ _objectType: "item", key: "K" }, { libraryID: 5 }), "z:5:K");
});

test("non-items and keyless rows produce no cache key", async () => {
  const { cacheKeyFor } = await core();
  // A trashed collection / saved search also has .key but is not a paper.
  assert.equal(cacheKeyFor({ _objectType: "collection", key: "COLL123" }), "");
  assert.equal(cacheKeyFor({ _objectType: "search", key: "SEARCH1" }), "");
  assert.equal(cacheKeyFor({ _objectType: "item", key: "   " }), "");
  assert.equal(cacheKeyFor({ _objectType: "item" }), "");
  assert.equal(cacheKeyFor(null), "");
  assert.equal(cacheKeyFor(undefined), "");
});

test("the snapshot path encodes the key as one query value", async () => {
  const { snapshotPath } = await core();
  assert.equal(
    snapshotPath("z:1:ABCD1234"),
    "/api/v2/paper/sections?paper=z%3A1%3AABCD1234",
  );
  assert.throws(() => snapshotPath(""), TypeError);
});

/* ── 1b. Follow-up requests use the resolved paper id ────────────────── */

test("the workbench URL puts the paper in the query, before the hash", async () => {
  const { workbenchUrl } = await core();
  // shell.js:59 reads `pid` from location.search and the page from
  // location.hash.slice(1). `#search?paper=…` would make the page name
  // "search?paper=…", which is not in titleNames, so page() falls through to
  // 研究调度台 and replaceState wipes the paper. Query must come first.
  assert.equal(
    workbenchUrl("http://127.0.0.1:8765", "search", "doi:10.1234/legacy"),
    "http://127.0.0.1:8765/?paper=doi%3A10.1234%2Flegacy#search",
  );
  const url = workbenchUrl("http://127.0.0.1:8765/", "graph", "z:1:KEY");
  assert.ok(url.indexOf("?paper=") < url.indexOf("#graph"), "query must precede the hash");
  assert.equal(url, "http://127.0.0.1:8765/?paper=z%3A1%3AKEY#graph");
  // A trailing slash on the base must not produce a double slash.
  assert.match(workbenchUrl("http://127.0.0.1:8765///", "search", "x"), /^http:\/\/127\.0\.0\.1:8765\/\?paper=x#search$/);
  // No paper still yields a usable page URL.
  assert.equal(workbenchUrl("http://127.0.0.1:8765", "search", ""), "http://127.0.0.1:8765/#search");
  assert.throws(() => workbenchUrl("", "search", "x"), TypeError);
  assert.throws(() => workbenchUrl("http://h", "", "x"), TypeError);
});

test("follow-up requests use the resolved paper id, not the lookup key", async () => {
  const { paperIdFor, readyState, loadingState } = await core();
  // A legacy-id paper: the cache key resolves to a different paper id.
  const legacy = readyState("reading", "z:1:ITEM0001", snapshot({
    paper_id: "doi:10.1234/legacy",
  }));
  assert.equal(paperIdFor(legacy), "doi:10.1234/legacy");
  // /api/documents/process does papers().get(store.resolve(id)); store.resolve
  // does not map z:<lib>:<key>, so passing the key would 400.
  assert.notEqual(paperIdFor(legacy), legacy.cacheKey);
  // Without a snapshot there is nothing resolved yet — fall back to the key.
  assert.equal(paperIdFor(loadingState("z:1:KEY")), "z:1:KEY");
  assert.equal(paperIdFor(null), "");
});

/* ── 2. Lazy loading + cache ─────────────────────────────────────────── */

test("a cache hit issues no request", async () => {
  const { createPanelCache } = await core();
  let loads = 0;
  const cache = createPanelCache({
    load: async () => {
      loads += 1;
      return snapshot();
    },
  });
  const first = await cache.fetch("z:1:A");
  assert.equal(loads, 1);
  const second = await cache.fetch("z:1:A");
  assert.equal(loads, 1, "the second fetch must come from the cache");
  assert.equal(first, second, "the cached snapshot is reused by reference");
});

test("concurrent fetches for one key share one request", async () => {
  const { createPanelCache } = await core();
  let loads = 0;
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const cache = createPanelCache({
    load: async () => {
      loads += 1;
      await gate;
      return snapshot();
    },
  });
  const a = cache.fetch("z:1:A");
  const b = cache.fetch("z:1:A");
  assert.equal(cache.inflightSize(), 1);
  release();
  await Promise.all([a, b]);
  assert.equal(loads, 1, "three sections on one item must cost one request");
  assert.equal(cache.inflightSize(), 0);
});

test("a rejected fetch caches nothing, so retry really retries", async () => {
  const { createPanelCache } = await core();
  let loads = 0;
  const cache = createPanelCache({
    load: async () => {
      loads += 1;
      if (loads === 1) {
        throw Object.assign(new Error("boom"), { kind: "http", status: 500 });
      }
      return snapshot();
    },
  });
  await assert.rejects(() => cache.fetch("z:1:A"));
  assert.equal(cache.peek("z:1:A"), null, "a failure must not poison the cache");
  await cache.fetch("z:1:A");
  assert.equal(loads, 2);
  assert.ok(cache.peek("z:1:A"));
});

test("the cache is bounded, evicting the least recently used key", async () => {
  const { createPanelCache } = await core();
  let loads = 0;
  const cache = createPanelCache({
    limit: 2,
    load: async () => {
      loads += 1;
      return snapshot();
    },
  });
  await cache.fetch("z:1:A");
  await cache.fetch("z:1:B");
  // Touch A so B becomes the oldest.
  assert.ok(cache.get("z:1:A"));
  await cache.fetch("z:1:C");
  assert.equal(cache.size(), 2);
  assert.equal(cache.peek("z:1:B"), null, "the least recently used key is evicted");
  assert.ok(cache.peek("z:1:A"), "a recently read key survives");
  assert.equal(loads, 3);
});

test("invalidate drops one key only", async () => {
  const { createPanelCache } = await core();
  const cache = createPanelCache({ load: async () => snapshot() });
  await cache.fetch("z:1:A");
  await cache.fetch("z:1:B");
  assert.equal(cache.invalidate("z:1:A"), true);
  assert.equal(cache.peek("z:1:A"), null);
  assert.ok(cache.peek("z:1:B"));
});

test("an empty cache key is rejected rather than fetching /api/...?paper=", async () => {
  const { createPanelCache } = await core();
  const cache = createPanelCache({ load: async () => snapshot() });
  await assert.rejects(() => cache.fetch(""), TypeError);
  assert.equal(cache.get(""), null);
});

/* ── 3. Skeleton first, then fill ────────────────────────────────────── */

test("planRender paints a skeleton and asks for a fetch on a miss", async () => {
  const { planRender, PANEL_STATES } = await core();
  const plan = planRender("evidence", "z:1:A", null);
  assert.equal(plan.state.status, PANEL_STATES.LOADING);
  assert.equal(plan.request, true);
  assert.equal(plan.state.cacheKey, "z:1:A");
});

test("planRender paints from the cache with no request on a hit", async () => {
  const { planRender, PANEL_STATES } = await core();
  const plan = planRender("evidence", "z:1:A", snapshot());
  assert.equal(plan.state.status, PANEL_STATES.READY);
  assert.equal(plan.request, null, "a hit must not request");
});

test("planRender renders nothing for a row with no cache key", async () => {
  const { planRender, PANEL_STATES } = await core();
  const plan = planRender("evidence", "", null);
  assert.equal(plan.state.status, PANEL_STATES.EMPTY);
  assert.equal(plan.state.cacheKey, "");
  assert.equal(plan.request, null);
});

/* ── 4. Four states ──────────────────────────────────────────────────── */

test("the four states are distinct and only error is retryable", async () => {
  const { loadingState, idleState, offlineState, errorState, PANEL_STATES } = await core();

  const loading = loadingState("z:1:A");
  assert.equal(loading.status, PANEL_STATES.LOADING);
  assert.equal(loading.retryable, false);

  const offline = offlineState("z:1:A");
  assert.equal(offline.status, PANEL_STATES.OFFLINE);
  // The offline card is a guide, not a retry loop, and never silent.
  assert.equal(offline.messageKey, "section-offline");
  assert.equal(offline.retryable, false);

  const failure = errorState("z:1:A", new Error("服务返回 500"));
  assert.equal(failure.status, PANEL_STATES.ERROR);
  assert.equal(failure.retryable, true);
  assert.equal(failure.error, "服务返回 500");
  assert.equal(failure.messageKey, "section-error");

  // An empty message still yields a visible line, never a blank panel.
  assert.equal(errorState("z:1:A", null).error, "未知错误");

  assert.equal(idleState().cacheKey, "");
});

test("only an S4 offline failure becomes the guide card", async () => {
  const { stateForFailure, PANEL_STATES } = await core();
  const offline = Object.assign(new Error("无法连接本地服务"), { kind: "offline" });
  assert.equal(stateForFailure("z:1:A", offline).status, PANEL_STATES.OFFLINE);

  // A 404 means "not in the library" — an empty state, not an error.
  const missing = Object.assign(new Error("文献不存在"), { kind: "http", status: 404 });
  const empty = stateForFailure("z:1:A", missing);
  assert.equal(empty.status, PANEL_STATES.EMPTY);
  assert.equal(empty.messageKey, "section-unindexed");

  // Any other failure is an error with a retry, never "offline": claiming the
  // service is down would hide a real bug.
  const serverError = Object.assign(new Error("boom"), { kind: "http", status: 500 });
  const failure = stateForFailure("z:1:A", serverError);
  assert.equal(failure.status, PANEL_STATES.ERROR);
  assert.equal(failure.retryable, true);

  // An error without a kind is still an error.
  assert.equal(stateForFailure("z:1:A", new Error("x")).status, PANEL_STATES.ERROR);
});

test("a 200 with no rows for one section is that section's empty state", async () => {
  const { readyState, settleRender, PANEL_STATES } = await core();

  const noEvidence = readyState("evidence", "z:1:A", snapshot({
    evidence: { indexed: false, index_version: "v1", counts: {}, total: 0, excerpts: [] },
  }));
  assert.equal(noEvidence.status, PANEL_STATES.EMPTY);
  assert.equal(noEvidence.messageKey, "section-unindexed");
  assert.equal(noEvidence.actionKey, "section-open-search");

  const noRelations = readyState("lineage", "z:1:A", snapshot({
    lineage: { verified: 0, pending: 0, citations: 0, items: [] },
  }));
  assert.equal(noRelations.status, PANEL_STATES.EMPTY);
  assert.equal(noRelations.messageKey, "section-no-relations");
  assert.equal(noRelations.actionKey, "section-open-lineage");

  // One section being empty does not empty the others.
  const withEvidence = readyState("evidence", "z:1:A", snapshot());
  assert.equal(withEvidence.status, PANEL_STATES.READY);

  // 精读 for a paper that could still be parsed is NOT empty: the section's
  // whole job then is to offer the parse button.
  const parseOffer = readyState("reading", "z:1:A", snapshot({
    reading: { state: "unparsed", total_pages: 0, read_pages: 0, missing_pages: [], gaps: [], classification: {}, documents: 0, can_parse: true },
  }));
  assert.equal(parseOffer.status, PANEL_STATES.READY);

  const neverParsed = readyState("reading", "z:1:A", snapshot({
    reading: { state: "unparsed", total_pages: 0, read_pages: 0, missing_pages: [], gaps: [], classification: {}, documents: 0, can_parse: false },
  }));
  assert.equal(neverParsed.status, PANEL_STATES.EMPTY);
  assert.equal(neverParsed.messageKey, "section-unparsed");

  // settleRender routes a failure and a success through the same door.
  assert.equal(
    settleRender("evidence", "z:1:A", { error: Object.assign(new Error("x"), { kind: "offline" }) }).status,
    PANEL_STATES.OFFLINE,
  );
  assert.equal(settleRender("evidence", "z:1:A", { snapshot: snapshot() }).status, PANEL_STATES.READY);
});

test("every message key the sections can show exists in both locale packs", async () => {
  const {
    loadingState, offlineState, errorState, readyState, SECTIONS,
  } = await core();
  const keys = new Set();
  const collect = (state) => {
    if (state.messageKey) {
      keys.add(state.messageKey);
    }
    if (state.actionKey) {
      keys.add(state.actionKey);
    }
  };
  collect(loadingState("z:1:A"));
  collect(offlineState("z:1:A"));
  collect(errorState("z:1:A", new Error("x")));
  for (const sectionID of SECTIONS) {
    collect(readyState(sectionID, "z:1:A", snapshot({
      evidence: { indexed: false, counts: {}, total: 0, excerpts: [] },
      lineage: { verified: 0, pending: 0, citations: 0, items: [] },
      reading: { state: "unparsed", total_pages: 0, read_pages: 0, missing_pages: [], gaps: [], classification: {}, documents: 0, can_parse: false },
    })));
  }
  // Row labels and the buttons the renderer adds are keys too, so the list is
  // taken from the sections module as text rather than guessed.
  const ts = fs.readFileSync(SECTIONS_TS, "utf8");
  for (const match of ts.matchAll(/t\("([a-z0-9-]+)"\)/g)) {
    keys.add(match[1]);
  }
  for (const match of ts.matchAll(/`section-reading-state-\$\{[^}]+\}`/g)) {
    for (const state of ["unparsed", "partial", "complete", "stale"]) {
      keys.add(`section-reading-state-${state}`);
    }
  }
  assert.ok(keys.size >= 12, `expected a substantial key set, got ${keys.size}`);

  for (const [locale, file] of Object.entries(FTL)) {
    const ftl = fs.readFileSync(file, "utf8");
    const defined = new Set(
      [...ftl.matchAll(/^([a-zA-Z0-9_-]+) =/gm)].map((match) => match[1]),
    );
    const missing = [...keys].filter((key) => !defined.has(key));
    assert.deepEqual(missing, [], `${locale} is missing ${missing.join(", ")}`);
  }
});

test("the renderer only ever uses keys that exist, including row labels", async () => {
  const ts = fs.readFileSync(SECTIONS_TS, "utf8");
  // Dynamic key families: evidence-kind-* and reading-class-* come from the
  // core's row view models, section-reading-state-* from the renderer itself.
  const coreSource = fs.readFileSync(
    path.join(PLUGIN_DIR, "src", "itemSectionsCore.mjs"),
    "utf8",
  );
  assert.match(coreSource, /labelKey: `evidence-kind-\$\{kind\}`/);
  assert.match(coreSource, /labelKey: `reading-class-\$\{name\}`/);
  assert.match(ts, /t\(`section-reading-state-\$\{row\.state\}`\)/);
  assert.match(ts, /t\("section-offline-guide"\)/, "the offline card has a guide line");
});

/* ── 5. No cross-talk ────────────────────────────────────────────────── */

test("acceptResponse refuses an answer for a different item", async () => {
  const { acceptResponse, loadingState, readyState } = await core();
  const mounted = loadingState("z:1:A");
  const forB = readyState("evidence", "z:1:B", snapshot());

  assert.equal(acceptResponse(mounted, "z:1:A", forB), null, "B's data must not paint under A");
  assert.equal(
    acceptResponse(loadingState("z:1:C"), "z:1:A", readyState("evidence", "z:1:A", snapshot())),
    null,
    "a mount re-bound to C must refuse A's answer",
  );
  const own = readyState("evidence", "z:1:A", snapshot());
  assert.equal(acceptResponse(mounted, "z:1:A", own), own, "the matching answer paints");
  assert.equal(acceptResponse(null, "z:1:A", own), null, "no mount, nothing to paint");
});

test("the render generation drops an answer superseded by a newer paint", async () => {
  const { createRenderGeneration } = await core();
  const generation = createRenderGeneration();
  const first = generation.begin();
  const second = generation.begin();
  assert.equal(generation.isCurrent(first), false, "the older paint is superseded");
  assert.equal(generation.isCurrent(second), true);
  assert.equal(generation.isCurrent(999), false);
});

test("a switched item's in-flight answer cannot paint (end to end)", async () => {
  const { createPanelCache, createRenderGeneration, planRender, settleRender, acceptResponse } =
    await core();
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  const cache = createPanelCache({
    load: async (key) => {
      if (key === "z:1:A") {
        await gate;
      }
      return snapshot({ paper_id: key });
    },
  });

  // The section is showing A and has a request in flight.
  let state = planRender("evidence", "z:1:A", null).state;
  const pending = cache.fetch("z:1:A").then((value) => settleRender("evidence", "z:1:A", { snapshot: value }));
  const generation = createRenderGeneration();
  const tokenA = generation.begin();

  // The user selects B while A's answer is in flight.
  state = planRender("evidence", "z:1:B", null).state;
  const tokenB = generation.begin();

  release();
  const late = await pending;

  // The answer is for A, the mount is bound to B, and a newer paint started.
  assert.equal(acceptResponse(state, "z:1:B", late), null, "A's answer must be discarded");
  assert.equal(generation.isCurrent(tokenA), false);
  assert.equal(generation.isCurrent(tokenB), true);

  // And the real paint for B succeeds.
  const forB = settleRender("evidence", "z:1:B", { snapshot: await cache.fetch("z:1:B") });
  assert.equal(acceptResponse(state, "z:1:B", forB), forB);
});

/* ── 6. Rows the DOM renders ─────────────────────────────────────────── */

test("evidence rows are the counts plus the excerpts, in that order", async () => {
  const { sectionRows } = await core();
  const rows = sectionRows("evidence", snapshot());
  assert.deepEqual(rows.slice(0, 3), [
    { kind: "count", labelKey: "evidence-kind-paper_text", value: "12" },
    { kind: "count", labelKey: "evidence-kind-figure", value: "3" },
    { kind: "count", labelKey: "evidence-kind-formula", value: "1" },
  ]);
  assert.deepEqual(rows[3], { kind: "excerpt", page: 7, text: "adaptive sampling" });
  // Zero counts are not rendered as "0 段落" noise.
  assert.equal(sectionRows("evidence", snapshot({ evidence: { indexed: true, counts: { paper_text: 1 }, total: 1, excerpts: [] } })).length, 1);
  assert.deepEqual(sectionRows("evidence", null), []);
});

test("lineage rows carry direction and grade, never inventing either", async () => {
  const { sectionRows } = await core();
  const rows = sectionRows("lineage", snapshot());
  assert.equal(rows.length, 1);
  assert.equal(rows[0].kind, "relation");
  assert.equal(rows[0].label, "方法继承");
  assert.equal(rows[0].direction, "previous");
  assert.equal(rows[0].grade, "fulltext");
  assert.equal(rows[0].peerTitle, "Earlier Work");
  assert.equal(rows[0].peerYear, 2015);

  // An unknown direction is normalised to "unknown", not guessed.
  const unknown = sectionRows("lineage", snapshot({
    lineage: {
      verified: 1, pending: 0, citations: 0,
      items: [{ id: "r", label: "引用", direction: "sideways", grade: "title", peer_title: "P" }],
    },
  }));
  assert.equal(unknown[0].direction, "unknown");
  assert.equal(unknown[0].peerYear, null);
});

test("reading rows are coverage, then page classes, then gaps", async () => {
  const { sectionRows } = await core();
  const rows = sectionRows("reading", snapshot());
  assert.deepEqual(rows[0], { kind: "coverage", readPages: 5, totalPages: 12, state: "partial" });
  assert.deepEqual(rows[1], { kind: "page-class", labelKey: "reading-class-NATIVE_TEXT", value: "9" });
  assert.deepEqual(rows[2], { kind: "page-class", labelKey: "reading-class-SCANNED", value: "3" });
  assert.deepEqual(rows[3], { kind: "gap", text: "第9页不可读" });
});

test("canParse only offers the button when the snapshot says it is possible", async () => {
  const { canParse } = await core();
  assert.equal(canParse(snapshot()), true);
  assert.equal(canParse(snapshot({ reading: { can_parse: false } })), false);
  assert.equal(canParse(null), false);
  assert.equal(canParse({}), false);
});

/* ── 7. Registration contract ────────────────────────────────────────── */

function fakeSectionManager({ reject = [] } = {}) {
  const calls = { register: [], unregister: [] };
  return {
    calls,
    registerSection(option) {
      calls.register.push(option);
      if (reject.includes(option.paneID)) {
        return false;
      }
      return `${option.pluginID}-${option.paneID}`;
    },
    unregisterSection(paneID) {
      calls.unregister.push(paneID);
      return true;
    },
  };
}

test("each section registers once with its paneID, pluginID and 20x20 sidenav", async () => {
  const { createSectionRegistration, SECTIONS } = await core();
  const manager = fakeSectionManager();
  for (const sectionID of SECTIONS) {
    const section = createSectionRegistration(
      manager, "littrace@example.invalid", sectionID, {}, "chrome://littrace/content/icons", "littrace",
    );
    assert.ok(section.dataKey, `${sectionID} must register`);
  }
  assert.equal(manager.calls.register.length, 3);
  const ids = manager.calls.register.map((option) => option.paneID);
  assert.deepEqual(ids, ["item-section-evidence", "item-section-lineage", "item-section-reading"]);
  for (const option of manager.calls.register) {
    assert.equal(option.pluginID, "littrace@example.invalid");
    assert.match(option.sidenav.icon, /-20\.svg$/);
    assert.match(option.header.icon, /-16\.svg$/);
    // The l10n ID must carry the build prefix: Zotero looks it up verbatim in
    // the consolidated plugin L10nFileSource.
    assert.match(option.header.l10nID, /^littrace-section-/);
    assert.match(option.sidenav.l10nID, /^littrace-section-/);
    assert.ok(option.onInit === undefined || typeof option.onInit === "function");
  }
});

test("a rejected section yields a null key and never throws", async () => {
  const { createSectionRegistration } = await core();
  const manager = fakeSectionManager({ reject: ["item-section-evidence"] });
  const section = createSectionRegistration(manager, "littrace@example.invalid", "evidence");
  assert.equal(section.dataKey, null);
  assert.equal(section.dispose(), false);
});

test("disposal uses the returned key exactly once", async () => {
  const { createSectionRegistration } = await core();
  const manager = fakeSectionManager();
  const section = createSectionRegistration(manager, "littrace@example.invalid", "reading");
  assert.equal(section.dataKey, "littrace@example.invalid-item-section-reading");
  assert.equal(section.dispose(), true);
  assert.equal(section.dispose(), false, "a second dispose is a no-op");
  assert.deepEqual(manager.calls.unregister, ["littrace@example.invalid-item-section-reading"]);
});

test("an unknown section kind is refused instead of registering a broken section", async () => {
  const { createSectionRegistration, sectionOptions } = await core();
  assert.throws(() => sectionOptions("id", "nope"), TypeError);
  const manager = fakeSectionManager();
  const section = createSectionRegistration(manager, "id", "nope");
  assert.equal(section.dataKey, null);
  assert.equal(manager.calls.register.length, 0);
});

/* ── 8. Wiring (source text, ADR 0010 convention) ────────────────────── */

test("the sections are registered through the single registry entry", () => {
  const source = fs.readFileSync(REGISTRY_TS, "utf8");
  assert.match(source, /import \{ createItemSections, disposeItemSections \} from "\.\/itemSections"/);
  const body = functionBody(source, "registerWindowIndependent");
  assert.match(body, /createItemSections\(\)/, "sections register in the process-wide entry");
  assert.match(body, /registerGlobal\(/, "reclamation goes through the registry");
  assert.match(body, /disposeItemSections\(\)/, "mounts and the cache are released on shutdown");
  // A rejected section is logged, not thrown: one bad section must not cost
  // the other two.
  assert.match(body, /Zotero\.logError/, "a rejected section is reported");
  assert.match(body, /continue;/, "a rejected section is skipped, not fatal");
});

test("onRender is the only paint path; onItemChange only rebinds", () => {
  const source = fs.readFileSync(SECTIONS_TS, "utf8");
  assert.match(source, /onRender\(\{ doc, body, item \}/);
  assert.match(source, /onItemChange\(/);
  // The official hook contract: onItemChange updates data and setEnabled but
  // must not render. The paint happens in onRender.
  const bind = functionBody(source, "bindItem");
  assert.ok(!bind.includes("paintSection"), "onItemChange must not paint");
  // onRender is an object-literal method, so slice it to the next hook.
  const renderStart = source.indexOf("onRender({ doc, body, item }");
  assert.ok(renderStart > 0, "onRender must be registered");
  const onRender = source.slice(renderStart, source.indexOf("},\n    ICON_BASE", renderStart));
  assert.match(onRender, /void paintSection\(mount, sectionID\)/);
});

test("a same-item repaint does not clear the DOM (sidenav reorder leaves it blank)", () => {
  const source = fs.readFileSync(SECTIONS_TS, "utf8");
  const bind = functionBody(source, "bindItem");
  // Zotero's `set item` calls _handleItemChange on every assignment, but
  // render() returns early when [tabID, item.id] are unchanged
  // (itemPaneSectionElementBase.mjs:31-34, 133-155). Reordering a section
  // (itemDetails.js:474-492 → render()) therefore runs onItemChange but skips
  // onRender; an unconditional replaceChildren() left all three sections blank.
  assert.match(bind, /if \(mount\.cacheKey === key\)/, "an unchanged key must short-circuit");
  const guard = bind.slice(bind.indexOf("if (mount.cacheKey === key)"), bind.indexOf("mount.cacheKey = key;"));
  assert.match(guard, /return;/, "the unchanged-key branch returns before clearing");
  assert.ok(
    guard.indexOf("return") < bind.indexOf("replaceChildren()"),
    "replaceChildren must sit after the unchanged-key guard",
  );
  // A real change still clears, and onRender then paints (item.id changed, so
  // the render dependencies changed with it).
  assert.match(bind, /mount\.body\.replaceChildren\(\)/);
});

test("the async fill guards on both the cache key and the render generation", () => {
  const source = fs.readFileSync(SECTIONS_TS, "utf8");
  const paint = source.slice(source.indexOf("async function paintSection("));
  assert.match(paint, /mount\.cacheKey !== owner/, "a switched item must discard the answer");
  assert.match(paint, /mount\.disposed/, "a destroyed section must not paint");
  assert.match(paint, /generation\.isCurrent\(token\)/, "a superseded paint must not land");
  assert.match(paint, /acceptResponse\(/, "the core's single staleness rule is used");
});

test("the parse button submits through the S4 client, not its own request", () => {
  const source = fs.readFileSync(SECTIONS_TS, "utf8");
  assert.match(source, /addon\.service\.submitTask\("\/api\/documents\/process"/);
  // The submitted id is the resolved one, or a legacy doi: paper 400s.
  const parse = source.slice(source.indexOf("async function requestParse("), source.indexOf("function report("));
  assert.match(parse, /paperIdFor\(state\)/, "the parse job must use the resolved paper id");
  assert.match(parse, /paper_id: paperId/, "the request body must not carry the raw cache key");
  assert.ok(!/paper_id: state\.cacheKey/.test(parse), "the cache key must not be submitted as paper_id");
  // The snapshot goes through the one client too, so the token cache and the
  // errorDelayMax: 0 discipline are shared.
  assert.match(source, /addon\.service\.get\(snapshotPath\(/);
});

test("no write path touches Zotero: the sections only read items", () => {
  const source = fs.readFileSync(SECTIONS_TS, "utf8");
  for (const forbidden of ["item.save", "item.setField", "item.addRelatedItem", "Zotero.Items.add"]) {
    assert.ok(!source.includes(forbidden), `sections must not call ${forbidden}`);
  }
});

/* ── 9. Icons ────────────────────────────────────────────────────────── */

test("the six icons exist, are 16/20 SVG, and follow the host theme", () => {
  for (const id of ["evidence", "lineage", "reading"]) {
    for (const size of [16, 20]) {
      const file = path.join(ICON_DIR, `${id}-${size}.svg`);
      assert.ok(fs.existsSync(file), `${id}-${size}.svg must exist`);
      const svg = fs.readFileSync(file, "utf8");
      assert.match(svg, new RegExp(`width="${size}"[^>]*height="${size}"`), `${file} must be ${size}x${size}`);
      assert.match(svg, /context-fill/, `${file} must use context-fill to follow the host theme`);
      assert.ok(!/fill="#[0-9a-fA-F]{3,6}"/.test(svg), `${file} must not hardcode a colour`);
    }
  }
});
