"use strict";

// Anchors the reader evidence-Q&A panel (A3 prototype, U3 real data).
//
// Five contracts are locked here, all of them executable without Zotero:
//
//   1. **Append-only card stream.** Every transition returns a new array; the
//      cards already in the stream are reused by reference and are frozen, so
//      a later event can neither edit nor drop history. The single exception
//      is the one progress row, which is not a card.
//   2. **Selection button contract.** `renderTextSelectionPopup` injects
//      exactly two buttons, with fixed ids, actions and a label key that
//      exists in both locale packs.
//   3. **Gap / failure shape.** A gap block offers no disclosure
//      (`expandable === false`, `toggle === null`); a failure card carries the
//      error text and a retry action, and retrying appends instead of
//      rewriting the failed card away.
//   4. **Real responses → cards (U3).** The three request shapes, the run
//      status → outcome mapping, the citation jump targets and the evidence /
//      lineage mappings are asserted against payloads shaped exactly like the
//      service's own answers (`kbapp/research/qa.py`, `kbapp/web/schemas.py`,
//      `kbapp/research/graph.py`).
//   5. **Missing cloud key is a guide, not a retry.** The configuration state
//      has its own card kind with no retry action; every other failure keeps
//      the retry entry.
//
// The logic under test lives in plugin/src/readerPanelCore.mjs, plain ESM with
// no DOM and no Zotero globals, so this file needs node built-ins only and no
// npm install. The DOM/Zotero wiring in plugin/src/readerPanel.ts is checked
// by reading its source, the same way tests/test_plugin_hooks.cjs checks the
// lifecycle hooks: no plugin code is executed here.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const PLUGIN_DIR = path.join(__dirname, "..", "plugin");
const CORE_URL = pathToFileURL(
  path.join(PLUGIN_DIR, "src", "readerPanelCore.mjs"),
).href;
const PANEL_TS = path.join(PLUGIN_DIR, "src", "readerPanel.ts");

let corePromise;

function core() {
  corePromise ??= import(CORE_URL);
  return corePromise;
}

const PAPER = "z:1:ABCD1234";
const ATTACHMENT = "PDFKEY01";

/**
 * A finished answer run, shaped like `qa.answer`'s output
 * (kbapp/research/qa.py:147) after one round of retrieval and verification.
 */
function answerRun(overrides = {}) {
  return {
    id: "run:abc",
    kind: "answer",
    status: "complete",
    error: "",
    completed: 1,
    total: 1,
    processed: 1,
    result: {
      answer: "RRT* 的采样效率来自重连步骤。",
      claims: [
        {
          id: "c1",
          category: "方法",
          claim_type: "paper_fact",
          text: "RRT* 通过重连步骤改善采样效率。",
          evidence: [
            {
              source_id: "src:1",
              quote: "RRT* 在每次插入后执行重连。",
              page_start: 7,
              page_end: 7,
              url: "zotero://open-pdf/library/items/PDFKEY01?page=7",
              paper_id: PAPER,
            },
          ],
          verification_status: "verified",
        },
      ],
      citations: [
        {
          id: "src:1",
          paper_id: PAPER,
          kind: "paper_text",
          title: "Sampling-based motion planning revisited",
          citekey: "Karaman2011",
          year: 2011,
          authors: ["Karaman", "Frazzoli"],
          quote: "RRT* 在每次插入后执行重连。",
          page_start: 7,
          page_end: 7,
          url: "zotero://open-pdf/library/items/PDFKEY01?page=7",
          evidence_level: "fulltext",
          location_status: "exact_page",
        },
      ],
      limitations: ["部分查询向量不可用；本次回答仅覆盖已完成的检索与回查证据"],
      verification_gaps: [],
      verification_status: "partial",
      ...overrides.result,
    },
    ...overrides,
  };
}

/** Question → answer, the happy path every append-only assertion builds on. */
function answeredFixture(mod) {
  let state = mod.createState();
  state = mod.ask(state, "这个方法在哪些实验条件下成立?");
  state = mod.stage(state, {
    labelKey: "reader-panel-stage-searching",
    progress: 40,
  });
  return mod.answer(
    state,
    mod.answerOutcome(answerRun().result),
    { paperId: PAPER, attachmentKey: ATTACHMENT },
  );
}

/** A failure fixture: question, then a service error. */
function failedFixture(mod) {
  let state = mod.createState();
  state = mod.ask(state, "服务离线时的问题");
  return mod.fail(state, { message: "无法连接本地服务，请确认服务已启动" });
}

/**
 * Extract one function body from TypeScript source by brace matching.
 *
 * Used only for wiring that cannot run under node (it needs a Document and a
 * Zotero global). The assertion is always about a statement being present or
 * about its order, never about the exact formatting.
 *
 * Three details the extractor has to get right, each of which was a real miss:
 *   - the parameter list can contain an inline type literal
 *     (`request: Extract<…, { kind: "run" }>`), so the body brace is the first
 *     `{` AFTER the parameter list closes, not the first `{` after the name;
 *   - braces inside strings, template literals and comments do not count —
 *     service paths are built with `${id}` interpolation;
 *   - the returned slice is the body only, without the outer braces.
 */
function functionBody(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `readerPanel.ts must define ${name}`);

  /** Skip a string/template literal starting at `index`; returns its end. */
  function skipString(index) {
    const quote = source[index];
    for (let i = index + 1; i < source.length; i += 1) {
      if (source[i] === "\\") {
        i += 1;
      } else if (source[i] === quote) {
        return i;
      }
    }
    throw new Error(`unterminated string in ${name}`);
  }

  // 1. Find the `)` that closes the parameter list.
  let parens = 0;
  let i = source.indexOf("(", start);
  assert.notEqual(i, -1, `${name} must have a parameter list`);
  for (; i < source.length; i += 1) {
    const char = source[i];
    if (char === '"' || char === "'" || char === "`") {
      i = skipString(i);
      continue;
    }
    if (char === "(") parens += 1;
    if (char === ")") {
      parens -= 1;
      if (parens === 0) break;
    }
  }
  // 2. The body opens at the next `{` that is not inside a string.
  for (; i < source.length; i += 1) {
    const char = source[i];
    if (char === '"' || char === "'" || char === "`") {
      i = skipString(i);
      continue;
    }
    if (char === "/" && source[i + 1] === "/") {
      const end = source.indexOf("\n", i);
      if (end === -1) throw new Error(`unterminated line comment in ${name}`);
      i = end;
      continue;
    }
    if (char === "/" && source[i + 1] === "*") {
      const end = source.indexOf("*/", i + 2);
      if (end === -1) throw new Error(`unterminated block comment in ${name}`);
      i = end + 1;
      continue;
    }
    if (char === "{") break;
    // A declared-but-unimplemented signature (an interface method) has no body.
    if (char === ";") {
      throw new Error(`${name} has no body`);
    }
  }
  const open = i;
  assert.notEqual(open, source.length, `${name} must have a body`);

  // 3. Match braces from the body's opening brace.
  let depth = 0;
  for (let j = open; j < source.length; j += 1) {
    const char = source[j];
    if (char === '"' || char === "'" || char === "`") {
      j = skipString(j);
      continue;
    }
    if (char === "/" && source[j + 1] === "/") {
      const end = source.indexOf("\n", j);
      if (end === -1) throw new Error(`unterminated line comment in ${name}`);
      j = end;
      continue;
    }
    if (char === "/" && source[j + 1] === "*") {
      const end = source.indexOf("*/", j + 2);
      if (end === -1) throw new Error(`unterminated block comment in ${name}`);
      j = end + 1;
      continue;
    }
    if (char === "{") depth += 1;
    if (char === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(open + 1, j);
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

test("appending an answer keeps every earlier card by reference and frozen", async () => {
  const mod = await core();
  let state = mod.createState();
  state = mod.ask(state, "问题一");
  const question = state.cards[0];

  state = mod.answer(state, mod.answerOutcome(answerRun().result));

  assert.equal(state.cards.length, 3, "question, answer and one gap card");
  assert.equal(state.cards[0], question, "the question card is the same object");
  assert.ok(Object.isFrozen(question), "cards are frozen so no later edit lands");
  assert.ok(Object.isFrozen(state.cards), "the card array itself is frozen");
  assert.equal(state.cards[0].text, "问题一");
  assert.equal(state.cards[0].kind, "question");
});

test("a second question appends and leaves the first answer untouched", async () => {
  const mod = await core();
  const first = answeredFixture(mod);
  const before = first.cards.slice();

  const second = mod.ask(first, "问题二");

  assert.equal(second.cards.length, before.length + 1);
  assert.deepEqual(
    second.cards.slice(0, before.length),
    before,
    "history keeps order, content and length",
  );
  for (const [index, card] of before.entries()) {
    assert.equal(second.cards[index], card, `card ${index} is reused by reference`);
  }
  assert.notEqual(second.cards, first.cards, "a new array, never in-place push");
});

test("card ids are stable across a repaint and unique across the stream", async () => {
  const mod = await core();
  const state = answeredFixture(mod);
  const ids = mod.cardViews(state).map((view) => view.id);

  assert.equal(new Set(ids).size, ids.length, "every card id is unique");
  assert.deepEqual(
    mod.cardViews(state).map((view) => view.id),
    ids,
    "reading the view twice does not renumber anything",
  );
});

test("the stage row advances without ever becoming a card", async () => {
  const mod = await core();
  let state = mod.createState();
  state = mod.ask(state, "问题");
  const cardsAfterAsk = state.cards.length;

  state = mod.stage(state, { labelKey: "reader-panel-stage-verifying", progress: 80 });
  state = mod.stage(state, { labelKey: "reader-panel-stage-verifying", progress: 120 });

  assert.equal(state.cards.length, cardsAfterAsk, "progress never appends a card");
  assert.equal(state.stage.labelKey, "reader-panel-stage-verifying");
  assert.equal(state.stage.progress, 100, "progress is clamped to 100");
  assert.equal(
    mod.cardViews(state).filter((view) => view.kind !== "question").length,
    0,
  );
});

test("progress is clamped and NaN never reaches the progress bar", async () => {
  const mod = await core();
  let state = mod.ask(mod.createState(), "问题");

  assert.equal(mod.stage(state, { labelKey: "x", progress: -5 }).stage.progress, 0);
  assert.equal(mod.stage(state, { labelKey: "x", progress: Number.NaN }).stage.progress, 0);
  assert.equal(mod.stage(state, { labelKey: "x" }).stage.progress, 0);
});

test("the empty state is empty and a question turns it busy", async () => {
  const mod = await core();
  const empty = mod.createState();
  assert.equal(mod.panelStatus(empty).kind, "empty");
  assert.equal(empty.stage, null);
  assert.equal(empty.busy, false);

  const asking = mod.ask(empty, "问题");
  assert.equal(mod.panelStatus(asking).kind, "busy");
  assert.ok(asking.busy);
  assert.equal(mod.panelStatus(answeredFixture(mod)).kind, "ready");
});

test("a blank question is rejected instead of freezing the progress row", async () => {
  const mod = await core();
  assert.throws(() => mod.ask(mod.createState(), "   "), /问题不能为空/);
  assert.throws(() => mod.ask(mod.createState(), ""), /问题不能为空/);
  assert.throws(() => mod.ask(mod.createState(), undefined), /问题不能为空/);
  assert.throws(() => mod.askRunRequest("  "), /问题不能为空/);
  assert.throws(() => mod.evidenceSearchRequest(""), /问题不能为空/);
});

test("citation badges read author year and page", async () => {
  const mod = await core();

  assert.equal(mod.citationLabel({ authors: ["Zhang"], year: 2019, page: 4 }), "Zhang 2019 · p.4");
  assert.equal(
    mod.citationLabel({ authors: ["Li", "Wang"], year: 2023, page: 11, pageEnd: 13 }),
    "Li & Wang 2023 · p.11–13",
  );
  assert.equal(
    mod.citationLabel({ authors: ["Chen", "Zhao", "Xu"], year: 2022, page: 7 }),
    "Chen 等 2022 · p.7",
    "three or more authors collapse to the first author plus 等",
  );
});

test("a missing page or year is shown as unknown, never guessed", async () => {
  const mod = await core();
  assert.equal(mod.citationLabel({ authors: ["A"], year: 2020 }), "A 2020 · p.?");
  assert.equal(mod.citationLabel({ authors: ["A"], page: 9 }), "A n.d. · p.9");
  assert.equal(mod.citationLabel({ authors: [], year: 2020, page: 1 }), "佚名 2020 · p.1");
});

test("a badge without authors falls back to citekey then title, never to n.d.", async () => {
  const mod = await core();

  assert.equal(mod.citationLabel({ citekey: "Karaman2011", page: 7 }), "Karaman2011 · p.7");
  const longTitle = mod.citationLabel({
    title: "Sampling-based motion planning revisited",
    page: 3,
  });
  assert.match(
    longTitle,
    /^Sampling-based motion .*… · p\.3$/,
    "a long title is shortened, not printed in full inside a pill",
  );
  assert.ok(longTitle.length < 40, `the badge stays short, got ${longTitle}`);
  assert.equal(
    mod.citationLabel({ authors: ["A"], year: 2020, page: 0 }),
    "A 2020 · p.?",
    "page 0 is not a page; it must not print as p.0",
  );
});

test("a gap is a grey block with no disclosure", async () => {
  const mod = await core();
  const state = answeredFixture(mod);
  const gap = state.cards.find((card) => card.kind === "gap");
  assert.ok(gap, "the service limitations must produce a gap card");

  const view = mod.cardView(gap);
  assert.equal(view.kind, "gap");
  assert.equal(view.expandable, false, "a gap block must not be expandable");
  assert.equal(view.toggle, null, "a gap block must offer no toggle");
  assert.ok(view.reason.length > 0);
  assert.equal(view.hintKey, "reader-panel-gap-hint");
});

test("every gap in the answer becomes its own card, in order", async () => {
  const mod = await core();
  let state = mod.ask(mod.createState(), "问题");
  state = mod.answer(state, {
    gaps: ["缺口一", "缺口二", "  "],
    conclusions: [{ text: "结论", citations: [] }],
  });

  assert.deepEqual(
    state.cards.map((card) => card.kind),
    ["question", "answer", "gap", "gap"],
    "a blank reason is skipped, not rendered as an empty block",
  );
  assert.deepEqual(
    state.cards.filter((card) => card.kind === "gap").map((card) => card.reason),
    ["缺口一", "缺口二"],
  );
});

test("a failure card carries the error text and a retry action", async () => {
  const mod = await core();
  const state = failedFixture(mod);
  const failure = state.cards.at(-1);

  assert.equal(failure.kind, "failure");
  const view = mod.cardView(failure);
  assert.equal(view.kind, "failure");
  assert.equal(view.message, "无法连接本地服务，请确认服务已启动");
  assert.deepEqual(view.retry, { labelKey: "reader-panel-retry", action: "retry" });
  assert.equal(view.expandable, false);
  assert.equal(
    failure.questionId,
    state.cards[0].id,
    "the failure remembers which question failed",
  );
});

test("retrying appends a new question and keeps the failure in place", async () => {
  const mod = await core();
  const failed = failedFixture(mod);
  const before = failed.cards.slice();
  const failureId = before.at(-1).id;

  const retried = mod.retry(failed, failureId);

  assert.equal(retried.cards.length, before.length + 1);
  assert.equal(
    retried.cards.at(-2),
    before.at(-1),
    "the failed card stays exactly where it was",
  );
  const fresh = retried.cards.at(-1);
  assert.equal(fresh.kind, "question");
  assert.equal(fresh.text, "服务离线时的问题", "the retry reuses the original question");
  assert.equal(fresh.retryOf, failureId);
  assert.ok(retried.busy, "a retry is in flight again");
  assert.equal(retried.stage.labelKey, "reader-panel-stage-queued");
});

test("retry rejects an unknown card and a non-failure card", async () => {
  const mod = await core();
  const state = answeredFixture(mod);

  assert.throws(() => mod.retry(state, "nope"), /找不到卡片/);
  assert.throws(
    () => mod.retry(state, state.cards[0].id),
    /不是失败卡片/,
    "retrying a successful question is a wiring bug, not a quiet no-op",
  );
  assert.throws(
    () => mod.retry(state, state.cards.at(-1).id),
    /不是失败卡片/,
    "a guide card has no retry, so it is not retryable",
  );
});

test("a failure with no error text is refused", async () => {
  const mod = await core();
  const state = mod.ask(mod.createState(), "问题");
  assert.throws(() => mod.fail(state, { message: "  " }), /必须有错误文案/);
});

test("the selection popup injects exactly two buttons with a fixed contract", async () => {
  const mod = await core();
  const buttons = mod.SELECTION_BUTTONS;

  assert.equal(buttons.length, 2, "查证据 + 问谱系, no more");
  assert.deepEqual(
    buttons.map((button) => button.action),
    ["evidence", "lineage"],
  );
  assert.deepEqual(
    buttons.map((button) => button.id),
    ["littrace-selection-evidence", "littrace-selection-lineage"],
    "ids are what real-machine acceptance selects on",
  );
  for (const button of buttons) {
    assert.match(button.labelKey, /^reader-selection-/);
    assert.ok(Object.isFrozen(button), "the contract must not be mutated at runtime");
  }
  assert.deepEqual(mod.SELECTION_ACTIONS, { EVIDENCE: "evidence", LINEAGE: "lineage" });
});

test("the selection buttons are reachable from a reader event handler", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");
  assert.match(
    source,
    /export function onRenderTextSelectionPopup\(/,
    "the handler keeps its name so the registered event stays greppable",
  );
  assert.match(
    source,
    /renderTextSelectionPopup:\s*onRenderTextSelectionPopup/,
    "the event is wired to that handler in READER_EVENTS",
  );
  assert.match(
    source,
    /registerEventListener\(type,\s*handler,\s*pluginID\)/,
    "registration keeps the documented three-argument form",
  );
  assert.match(
    source,
    /unregisterEventListener\(\s*\n?\s*type,/,
    "the disposer must unregister, not only drop the DOM",
  );
});

test("source markers round-trip so a retry keeps its selection action", async () => {
  const mod = await core();

  assert.equal(mod.sourceForAction(null), "panel");
  assert.equal(mod.sourceForAction("lineage"), "selection:lineage");
  assert.equal(mod.actionForSource(mod.sourceForAction("lineage")), "lineage");
  assert.equal(mod.actionForSource(mod.sourceForAction("evidence")), "evidence");
  assert.equal(mod.actionForSource("panel"), null);
  assert.equal(mod.actionForSource("selection:bogus"), null);
  assert.equal(mod.actionForSource(undefined), null);

  let state = mod.ask(mod.createState(), "选中正文", { source: "selection:lineage" });
  state = mod.fail(state, { message: "断线" });
  state = mod.retry(state, state.cards.at(-1).id);
  assert.equal(
    mod.actionForSource(state.cards.at(-1).source),
    "lineage",
    "a retried lineage question must not become an evidence question",
  );
});

test("three carrier fallbacks are declared and each maps to a plan", async () => {
  const mod = await core();

  assert.deepEqual(
    mod.CARRIERS,
    ["toolbar-window", "sidebar-container", "item-pane-section"],
    "the three candidate carriers from the design",
  );
  assert.ok(
    mod.CARRIERS.includes(mod.DEFAULT_CARRIER),
    "the prototype default must be one of the candidates",
  );

  const hosts = mod.CARRIERS.map((carrier) => mod.carrierPlan(carrier).host);
  assert.deepEqual(hosts, [
    "standalone-window",
    "reader-document",
    "item-pane-section",
  ]);
  const layouts = mod.CARRIERS.map((carrier) => mod.carrierPlan(carrier).layout);
  assert.deepEqual(layouts, ["wide", "sidebar", "compact"]);

  assert.throws(
    () => mod.carrierPlan("reader-tab-panel"),
    /未知面板承载形态/,
    "the removed toolkit carrier must not silently fall back to a default",
  );
});

test("the switch point is one constant and all three fallbacks are documented", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");

  assert.match(
    source,
    /const CARRIER: string = DEFAULT_CARRIER;/,
    "the carrier must stay a single constant",
  );
  for (const carrier of ["toolbar-window", "sidebar-container", "item-pane-section"]) {
    assert.ok(
      source.includes(carrier),
      `readerPanel.ts must document the "${carrier}" fallback`,
    );
  }
});

test("every message key used by the panel exists in both locale packs", async () => {
  const mod = await core();
  const locales = ["en-US", "zh-CN"];

  for (const locale of locales) {
    const ftl = fs.readFileSync(
      path.join(PLUGIN_DIR, "addon", "locale", locale, "addon.ftl"),
      "utf8",
    );
    for (const key of mod.MESSAGE_KEYS) {
      assert.match(
        ftl,
        new RegExp(`^${key} =`, "m"),
        `${locale} is missing reader-panel message "${key}"`,
      );
    }
  }
});

test("message ids get the build prefix exactly once", async () => {
  const mod = await core();

  assert.equal(mod.messageId("reader-panel-title", "littrace"), "littrace-reader-panel-title");
  assert.equal(
    mod.messageId("littrace-reader-panel-title", "littrace"),
    "littrace-reader-panel-title",
    "an already-prefixed key is not prefixed twice",
  );
  assert.equal(mod.messageId("reader-panel-title", ""), "reader-panel-title");
  assert.throws(() => mod.messageId("", "littrace"), /不能为空/);
});

test("reader events are registered once, not once per main window", () => {
  const registry = fs.readFileSync(
    path.join(PLUGIN_DIR, "src", "registry.ts"),
    "utf8",
  );
  const hooks = fs.readFileSync(path.join(PLUGIN_DIR, "src", "hooks.ts"), "utf8");

  assert.match(registry, /registerGlobal\("reader-panel"/, "reader events are window-independent");
  assert.doesNotMatch(
    registry,
    /register\(win,\s*"reader-panel"/,
    "per-window registration would inject the selection buttons once per window",
  );
  assert.match(
    hooks,
    /registerWindowIndependent\(\)/,
    "startup must register the window-independent features exactly once",
  );
});

test("an unknown card kind throws instead of rendering a blank card", async () => {
  const mod = await core();
  assert.throws(() => mod.cardView({ id: "c1", kind: "mystery" }), /未知卡片种类/);
});

// ---------------------------------------------------------------------------
// U3: the three real-data paths.
//
// The fixtures below are shaped exactly like the service's own answers, so a
// change on either side (a renamed field, a new status) fails here instead of
// on a real machine. Endpoint paths are asserted verbatim because they are the
// contract with kbapp/web/*.py.
// ---------------------------------------------------------------------------

test("the three entries build the documented service requests", async () => {
  const mod = await core();

  const ask = mod.askRunRequest("这个方法成立吗?", { paperId: PAPER });
  assert.equal(ask.kind, "run");
  assert.equal(ask.path, "/api/agent-runs", "POST /api/agent-runs (research_api.py:24)");
  assert.equal(ask.body.kind, "answer");
  assert.equal(ask.body.query, "这个方法成立吗?");
  assert.deepEqual(ask.body.filters, { paper: PAPER });

  const evidence = mod.evidenceSearchRequest("选中的一段正文", { paperId: PAPER });
  assert.equal(evidence.kind, "read");
  assert.equal(evidence.shape, "evidence");
  const evidenceUrl = new URL(evidence.path, "http://127.0.0.1:8765");
  assert.equal(evidenceUrl.pathname, "/api/v2/search");
  assert.equal(evidenceUrl.searchParams.get("q"), "选中的一段正文");
  assert.equal(evidenceUrl.searchParams.get("paper"), PAPER);
  assert.equal(
    evidenceUrl.searchParams.get("wait_vectors"),
    "false",
    "waiting for vectors would exceed the client's request timeout",
  );
  assert.equal(evidenceUrl.searchParams.get("level"), "passage");

  const lineage = mod.lineageRequest(PAPER);
  assert.equal(lineage.kind, "read");
  assert.equal(lineage.shape, "lineage");
  const lineageUrl = new URL(lineage.path, "http://127.0.0.1:8765");
  assert.equal(lineageUrl.pathname, "/api/graph");
  assert.equal(lineageUrl.searchParams.get("paper"), PAPER);
  assert.equal(lineageUrl.searchParams.get("candidates"), "false");
});

test("a question without a paper identity still asks, but lineage refuses", async () => {
  const mod = await core();

  const ask = mod.askRunRequest("全库问题", { paperId: "" });
  assert.equal(ask.kind, "run");
  assert.deepEqual(ask.body.filters, {}, "no paper filter means a library-wide question");

  const blocked = mod.lineageRequest("");
  assert.equal(blocked.kind, "blocked");
  assert.match(
    blocked.message,
    /无法识别当前文献/,
    "querying /api/graph without a paper would return the whole library and look like an answer",
  );

  // The dispatch picks the right builder per action, and `null` is the panel.
  assert.equal(mod.requestFor("evidence", "x", { paperId: PAPER }).shape, "evidence");
  assert.equal(mod.requestFor("lineage", "x", { paperId: PAPER }).shape, "lineage");
  assert.equal(mod.requestFor(null, "x", { paperId: PAPER }).kind, "run");
});

test("the answer request refuses an over-long question instead of truncating it", async () => {
  const mod = await core();
  const long = "问".repeat(mod.QUESTION_MAX + 1);
  assert.throws(() => mod.askRunRequest(long), /不能超过/);
  assert.equal(mod.askRunRequest("问".repeat(mod.QUESTION_MAX)).kind, "run");
});

test("a selection longer than the service limit is refused locally, not sent as a 422", async () => {
  const mod = await core();

  // /api/v2/search's `q` is Query(min_length=1, max_length=2000)
  // (kbapp/web/v2_api.py:29), and a 422's `detail` is an array that the S4
  // client cannot render, so an over-long selection would produce an
  // unreadable error plus a retry that always fails the same way.
  const long = "摘".repeat(mod.QUESTION_MAX + 1);
  assert.throws(
    () => mod.evidenceSearchRequest(long),
    /不能超过/,
    "the evidence path must carry the same guard as the answer path",
  );
  assert.throws(() => mod.requestFor("evidence", long), /不能超过/);

  const atLimit = mod.evidenceSearchRequest("摘".repeat(mod.QUESTION_MAX));
  assert.equal(atLimit.shape, "evidence", "exactly at the limit is still accepted");
  assert.ok(
    new URL(atLimit.path, "http://127.0.0.1:8765").searchParams.get("q").length ===
      mod.QUESTION_MAX,
    "nothing is silently truncated on the way out",
  );
});

test("a real answer response becomes an answer card with page-bearing badges", async () => {
  const mod = await core();
  const outcome = mod.answerOutcome(answerRun().result);

  assert.equal(outcome.ok, true);
  assert.equal(outcome.conclusions.length, 1);
  assert.equal(outcome.conclusions[0].text, "RRT* 通过重连步骤改善采样效率。");
  assert.equal(outcome.conclusions[0].citations.length, 1);
  assert.equal(outcome.conclusions[0].citations[0].page, 7);
  assert.equal(
    outcome.conclusions[0].citations[0].url,
    "zotero://open-pdf/library/items/PDFKEY01?page=7",
    "the jump target is the service's own deep link",
  );
  assert.deepEqual(
    outcome.gaps,
    ["部分查询向量不可用；本次回答仅覆盖已完成的检索与回查证据"],
    "limitations become gap cards verbatim",
  );

  let state = mod.ask(mod.createState(), "这个问题");
  state = mod.answer(state, outcome, { paperId: PAPER, attachmentKey: ATTACHMENT });

  const answer = mod.cardView(state.cards[1]);
  assert.equal(answer.kind, "answer");
  assert.equal(answer.conclusions[0].citations[0].label, "Karaman & Frazzoli 2011 · p.7");
  assert.equal(
    answer.conclusions[0].citations[0].href,
    "zotero://open-pdf/library/items/PDFKEY01?page=7",
    "the badge view carries the href the renderer turns into a jump",
  );
  assert.equal(answer.expandable, true, "evidence text is behind a disclosure");
  assert.equal(answer.evidence[0].href, "zotero://open-pdf/library/items/PDFKEY01?page=7");
});

test("an answer with no verified claim keeps the honest gap instead of inventing one", async () => {
  const mod = await core();
  const outcome = mod.answerOutcome({
    answer: "当前证据不足以形成通过逐项核验的结论。",
    claims: [],
    citations: [],
    limitations: [],
    verification_gaps: [{ claim: "候选结论", reason: "结论含无法定位的原文摘录" }],
  });

  assert.equal(outcome.ok, true);
  assert.equal(outcome.conclusions.length, 0, "no verified claim means no conclusion card");
  assert.ok(
    outcome.gaps.some((gap) => gap.includes("证据不足以形成")),
    "the service's own sentence is kept",
  );
  assert.ok(
    outcome.gaps.some((gap) => gap.includes("未通过核验：结论含无法定位的原文摘录")),
    "the verification reason is surfaced, not dropped",
  );
});

test("a failed run is a retryable failure; a cloud-config run is a guide", async () => {
  const mod = await core();

  const offline = mod.outcomeFromRun({
    id: "run:x",
    status: "failed",
    error: "无法连接本地服务，请确认服务已启动",
    result: {},
  });
  assert.equal(offline.ok, false);
  assert.equal(offline.kind, "failure");
  assert.match(offline.message, /无法连接本地服务/);

  const interrupted = mod.outcomeFromRun({ status: "interrupted", error: "", result: {} });
  assert.equal(interrupted.kind, "failure", "a stopped run is still retryable");

  const unconfigured = mod.outcomeFromRun({
    status: "failed",
    error: "请先在模型与费用设置中启用云端问答",
    result: {},
  });
  assert.equal(
    unconfigured.kind,
    "guide",
    "the service's cloud-config wording must route to the guide, not to retry",
  );

  const halted = mod.outcomeFromRun({
    status: "failed",
    error: "请先在模型与费用设置中启用云端问答",
    result: { halt_error: true, message: "请先在模型与费用设置中启用云端问答" },
  });
  assert.equal(halted.kind, "guide");

  assert.equal(
    mod.outcomeFromRun({ status: "running", result: {} }),
    null,
    "an active run is not an outcome yet",
  );

  const malformed = mod.outcomeFromRun({ status: "complete", error: "", result: { plan_id: "x" } });
  assert.equal(malformed.kind, "failure", "2xx with a non-answer result is not success");
});

test("the cloud-config guide card has no retry and one settings action", async () => {
  const mod = await core();
  assert.equal(mod.cloudDisabled({ cloud_enabled: false }), true);
  assert.equal(mod.cloudDisabled({ cloud_enabled: true }), false);
  assert.equal(
    mod.cloudDisabled({}),
    false,
    "an unknown field (older service) must not block asking",
  );

  let state = mod.ask(mod.createState(), "问题");
  state = mod.stage(state, { labelKey: "reader-panel-stage-queued", progress: 8 });
  state = mod.guide(state);

  const card = state.cards.at(-1);
  assert.equal(card.kind, "guide");
  const view = mod.cardView(card);
  assert.equal(view.kind, "guide");
  assert.equal(view.retry, null, "a missing key is not something retry can fix");
  assert.equal(view.expandable, false);
  assert.equal(view.messageKey, "reader-panel-guide");
  assert.equal(view.hintKey, "reader-panel-guide-cloud");
  assert.deepEqual(view.action, {
    labelKey: "reader-panel-guide-settings",
    action: "open-settings",
  });
  assert.equal(state.busy, false, "the guide ends the flight");
  assert.equal(state.stage, null, "the progress row is retracted");
  assert.equal(
    state.cards[0].kind,
    "question",
    "the question the user typed stays in the stream",
  );
});

test("a scripted cloud-config failure produces the guide through the run mapper", async () => {
  const mod = await core();
  let state = mod.ask(mod.createState(), "问题");
  const outcome = mod.outcomeFromRun({
    status: "failed",
    error: "请先在模型与费用设置中启用云端问答",
    result: {},
  });
  state = state.busy === undefined ? state : state;
  state =
    outcome.kind === "guide"
      ? mod.guide(state, { message: outcome.message })
      : mod.fail(state, { message: outcome.message });
  assert.equal(state.cards.at(-1).kind, "guide");
  assert.match(mod.cardView(state.cards.at(-1)).message, /启用云端问答/);
});

test("the run status becomes one monotonic progress row", async () => {
  const mod = await core();

  const queued = mod.stageForRun({ status: "queued", result: {} }, 0);
  assert.equal(queued.labelKey, "reader-panel-stage-queued");

  const searching = mod.stageForRun(
    { status: "running", result: { role: "证据检索者" }, total: 1, processed: 0 },
    queued.progress,
  );
  assert.equal(searching.labelKey, "reader-panel-stage-searching");

  const writing = mod.stageForRun(
    { status: "running", result: { role: "写作者" }, total: 1, processed: 0 },
    searching.progress,
  );
  assert.equal(writing.labelKey, "reader-panel-stage-writing");
  const verifying = mod.stageForRun(
    { status: "running", result: { role: "核验者" }, total: 1, processed: 1 },
    writing.progress,
  );
  assert.equal(verifying.labelKey, "reader-panel-stage-verifying");

  assert.ok(
    queued.progress <= searching.progress &&
      searching.progress <= writing.progress &&
      writing.progress <= verifying.progress,
    "the bar never goes backwards",
  );
  assert.ok(verifying.progress <= 95, "a run that is still executing never reads 100%");

  assert.equal(
    mod.stageForRun(
      { status: "running", result: { role: "写作者" }, total: 1, processed: 0 },
      verifying.progress,
    ).progress,
    verifying.progress,
    "a later stage cannot lower the bar",
  );

  assert.equal(mod.stageForRun({ status: "complete" }, 50), null);
  assert.equal(mod.stageForRun({ status: "failed" }, 50), null);
});

test("a real search response becomes evidence cards with pages", async () => {
  const mod = await core();
  const outcome = mod.evidenceOutcome({
    query: "选中的正文",
    mode: "two-stage-mixed",
    warning: "向量模型已被手动停止，当前使用关键词结果",
    vector_status: "stopped",
    results: [
      {
        id: "src:9",
        paper_id: PAPER,
        kind: "paper_text",
        title: "Sampling-based motion planning revisited",
        citekey: "Karaman2011",
        text: "RRT* 在每次插入后执行重连，代价随迭代下降。",
        snippet: "RRT* 在每次插入后执行重连",
        page_start: 7,
        page_end: 8,
        url: "zotero://open-pdf/library/items/PDFKEY01?page=7",
      },
      {
        id: "src:10",
        paper_id: PAPER,
        kind: "metadata",
        title: "Sampling-based motion planning revisited",
        text: "摘要",
        page_start: null,
      },
    ],
  });

  assert.equal(outcome.ok, true);
  assert.equal(outcome.hits.length, 2);
  assert.equal(outcome.hits[0].citation.page, 7);
  assert.equal(outcome.hits[0].citation.pageEnd, 8);
  assert.equal(outcome.hits[0].kindKey, "evidence-kind-paper_text");
  assert.equal(outcome.hits[1].citation.page, null, "a hit without a page is still a hit");
  assert.deepEqual(
    outcome.gaps,
    ["向量模型已被手动停止，当前使用关键词结果"],
    "the service warning is surfaced as a gap",
  );

  let state = mod.ask(mod.createState(), "选中的正文", { source: "selection:evidence" });
  state = mod.evidence(state, outcome, { paperId: PAPER, attachmentKey: ATTACHMENT });
  assert.deepEqual(
    state.cards.map((card) => card.kind),
    ["question", "evidence", "evidence", "gap"],
  );
  const card = mod.cardView(state.cards[1]);
  assert.equal(card.kind, "evidence");
  assert.equal(card.page, 7);
  assert.equal(card.href, "zotero://open-pdf/library/items/PDFKEY01?page=7");
  assert.equal(card.label, "Karaman2011 · p.7–8");
  assert.equal(card.expandable, false, "the quote IS the card, nothing to expand");
  assert.equal(card.toggle, null);
});

test("a search with no hit yields a gap card, never a fake result", async () => {
  const mod = await core();
  const outcome = mod.evidenceOutcome({ query: "无命中", results: [], warning: "" });

  assert.equal(outcome.hits.length, 0);
  assert.match(outcome.gaps[0], /没有找到可定位页码的原文证据/);

  let state = mod.ask(mod.createState(), "无命中");
  state = mod.evidence(state, outcome);
  assert.deepEqual(
    state.cards.map((card) => card.kind),
    ["question", "gap"],
  );
});

test("more hits than the card limit are truncated and said so", async () => {
  const mod = await core();
  const results = Array.from({ length: mod.EVIDENCE_HIT_LIMIT + 3 }, (_, index) => ({
    id: `src:${index}`,
    paper_id: PAPER,
    kind: "paper_text",
    text: `片段 ${index}`,
    page_start: index + 1,
  }));

  const outcome = mod.evidenceOutcome({ query: "q", results, warning: "" });
  assert.equal(outcome.hits.length, mod.EVIDENCE_HIT_LIMIT);
  assert.ok(
    outcome.gaps.some((gap) => gap.includes(String(results.length))),
    "the total hit count is reported so the user knows cards were dropped",
  );
});

test("a real graph response becomes lineage cards with page-bearing citations", async () => {
  const mod = await core();
  const outcome = mod.lineageOutcome(
    {
      nodes: [
        { id: PAPER, title: "Sampling-based motion planning revisited", citekey: "Karaman2011", year: 2011 },
        { id: "z:1:PREV0001", title: "Rapidly-exploring random trees", citekey: "LaValle1998", year: 1998 },
      ],
      edges: [
        {
          id: "rel:1",
          src: PAPER,
          dst: "z:1:PREV0001",
          kind: "improves",
          status: "confirmed",
          explanation: "本文在 RRT 的基础上加入重连步骤。",
          change: "插入后重连",
          conditions: "静态地图",
          evidence: [
            {
              source_id: "src:1",
              paper_id: PAPER,
              quote: "RRT* 在每次插入后执行重连。",
              page: 7,
              url: "zotero://open-pdf/library/items/PDFKEY01?page=7",
            },
          ],
          evidence_grade: "fulltext",
          evidence_label: "原文已核 · 模型复核",
        },
      ],
      total: 2,
      offset: 0,
      has_more: false,
    },
    { paperId: PAPER },
  );

  assert.equal(outcome.ok, true);
  assert.equal(outcome.conclusions.length, 1);
  assert.match(outcome.conclusions[0].text, /与《LaValle1998》的关系：具体改进/);
  assert.match(outcome.conclusions[0].text, /证据等级：原文已核 · 模型复核/);
  assert.equal(outcome.conclusions[0].citations[0].page, 7);
  assert.equal(outcome.evidence.length, 1);

  let state = mod.ask(mod.createState(), "选中正文", { source: "selection:lineage" });
  state = mod.answer(state, outcome, { paperId: PAPER, attachmentKey: ATTACHMENT });
  const card = mod.cardView(state.cards[1]);
  assert.equal(card.kind, "answer");
  assert.equal(
    card.conclusions[0].citations[0].label,
    "Karaman2011 · p.7",
    "the badge names the paper the quote came from (the citing paper), not the target",
  );
  assert.equal(card.conclusions[0].citations[0].href, "zotero://open-pdf/library/items/PDFKEY01?page=7");
});

test("a paper with no lineage says which kind of nothing it is", async () => {
  const mod = await core();

  const unknown = mod.lineageOutcome({ nodes: [], edges: [] }, { paperId: PAPER });
  assert.equal(unknown.conclusions.length, 0);
  assert.match(unknown.gaps[0], /还没有进入方法谱系/);

  const noEdges = mod.lineageOutcome(
    { nodes: [{ id: PAPER, title: "本文" }], edges: [] },
    { paperId: PAPER },
  );
  assert.match(noEdges.gaps[0], /还没有已确认的方法关系/);

  const citationsOnly = mod.lineageOutcome(
    {
      nodes: [{ id: PAPER }, { id: "z:1:OTHER001" }],
      edges: [{ src: PAPER, dst: "z:1:OTHER001", kind: "cites", evidence: [] }],
    },
    { paperId: PAPER },
  );
  assert.match(
    citationsOnly.gaps.join(" "),
    /只有引用关系/,
    "a citation is not a method relation, and the gap must say so",
  );
});

test("method relations are listed before citation-only edges", async () => {
  const mod = await core();
  const outcome = mod.lineageOutcome(
    {
      nodes: [
        { id: PAPER, citekey: "Self2020" },
        { id: "z:1:CITE0001", citekey: "Cite2019" },
        { id: "z:1:METH0001", citekey: "Meth2018" },
      ],
      edges: [
        { src: PAPER, dst: "z:1:CITE0001", kind: "cites", evidence: [] },
        { src: PAPER, dst: "z:1:METH0001", kind: "inherits", evidence: [] },
      ],
    },
    { paperId: PAPER },
  );
  assert.match(outcome.conclusions[0].text, /Meth2018/, "method relations come first");
});

test("a citation badge jumps through the host bridge, or copies when it cannot", async () => {
  const mod = await core();
  const url = "zotero://open-pdf/library/items/PDFKEY01?page=7";

  const open = mod.jumpPlan(url, { bridgeAvailable: true });
  assert.deepEqual(open, { mode: "open", href: url });

  const copy = mod.jumpPlan(url, { bridgeAvailable: false });
  assert.deepEqual(
    copy,
    { mode: "copy", href: url },
    "no launcher means copy the deep link, not a dead click",
  );
  assert.equal(
    mod.jumpPlan(url).mode,
    "copy",
    "an unspecified bridge is treated as unavailable, never as working",
  );
  assert.equal(mod.jumpPlan("").mode, "none");
  assert.equal(mod.jumpPlan(null).mode, "none");
});

test("a deep link is only built when the service or the attachment key proves it", async () => {
  const mod = await core();

  assert.equal(
    mod.citationDeepLink({
      url: "zotero://open-pdf/library/items/PDFKEY01?page=4",
      page: 7,
      paperId: PAPER,
    }),
    "zotero://open-pdf/library/items/PDFKEY01?page=7",
    "the page from the evidence wins over a stale one in the url",
  );
  assert.equal(
    mod.citationDeepLink({ url: "zotero://open-pdf/library/items/PDFKEY01?page=4", page: null }),
    "zotero://open-pdf/library/items/PDFKEY01?page=4",
    "a missing page leaves the url's own page alone",
  );
  assert.equal(
    mod.citationDeepLink({ url: "zotero://select/library/items/KEY" }),
    null,
    "select cannot jump to a page, so it is not a jump target",
  );
  assert.equal(
    mod.citationDeepLink(
      { url: "http://127.0.0.1:8765/api/documents/doc1/file#page=3", page: 3, paperId: PAPER },
      { paperId: PAPER, attachmentKey: ATTACHMENT },
    ),
    `zotero://open-pdf/library/items/${ATTACHMENT}?page=3`,
    "a same-paper document link is completed with the open PDF's attachment key",
  );
  assert.equal(
    mod.citationDeepLink(
      { url: "http://127.0.0.1:8765/api/documents/doc1/file#page=3", page: 3, paperId: "z:1:OTHER" },
      { paperId: PAPER, attachmentKey: ATTACHMENT },
    ),
    null,
    "another paper's attachment key would open the wrong PDF, so no link is guessed",
  );
  assert.equal(
    mod.citationDeepLink({ url: "http://127.0.0.1:8765/api/file?paper=x", page: 3 }),
    null,
    "without an attachment key there is no open-pdf deep link to build",
  );
});

test("page parameters are merged, not appended twice", async () => {
  const mod = await core();
  assert.equal(mod.withPageParam("zotero://open-pdf/x?page=1", 5), "zotero://open-pdf/x?page=5");
  assert.equal(mod.withPageParam("zotero://open-pdf/x", 5), "zotero://open-pdf/x?page=5");
  assert.equal(
    mod.withPageParam("http://h/api/doc#page=9", 5),
    "http://h/api/doc?page=5#page=9",
  );
  assert.equal(mod.withPageParam("zotero://open-pdf/x?page=1", null), "zotero://open-pdf/x?page=1");
  assert.equal(mod.withPageParam("", 3), "");
});

test("the attachment key names the paper, and a missing key names nothing", async () => {
  const mod = await core();
  assert.equal(
    mod.paperKeyOf({ itemKey: "ATT1", libraryId: 1, parentKey: "PAPER1", parentLibraryId: 1 }),
    "z:1:PAPER1",
    "a PDF's paper is its parent item (kbapp/library/sources.py:34-40)",
  );
  assert.equal(
    mod.paperKeyOf({ itemKey: "SOLO001", libraryId: 3 }),
    "z:3:SOLO001",
    "a standalone PDF is its own paper",
  );
  assert.equal(mod.paperKeyOf({ itemKey: "X", libraryId: 7 }), "z:7:X");
  assert.equal(mod.paperKeyOf({}), "", "no item key means no paper, not z:1:");
  assert.equal(mod.paperKeyOf(null), "");
});

test("the settings action targets the workbench settings page", async () => {
  const mod = await core();
  assert.equal(mod.settingsUrl("http://127.0.0.1:8765"), "http://127.0.0.1:8765/#settings");
  assert.equal(mod.settingsUrl("http://127.0.0.1:8765/"), "http://127.0.0.1:8765/#settings");
  assert.equal(mod.settingsUrl(""), null);
  assert.equal(mod.settingsUrl(null), null);
});

// ---------------------------------------------------------------------------
// Wiring in readerPanel.ts. These cannot run under node (a Document and a
// Zotero global are needed), so each assertion pins the statement that fixes a
// specific failure mode, named in the message.
// ---------------------------------------------------------------------------

test("U3 wiring: the three service paths exist and no static-fake path remains", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");

  assert.doesNotMatch(
    source,
    /demoOutcome/,
    "the static fake-data path must be gone once real data lands",
  );
  for (const helper of [
    "runAnswer",
    "runRead",
    "dispatch",
    "activateJump",
    "openSettings",
    "copyLink",
    "identityOfReader",
  ]) {
    assert.match(
      source,
      new RegExp(`function ${helper}\\(`),
      `readerPanel.ts must define ${helper}`,
    );
  }
  assert.match(
    source,
    /addon\.service\.submitTask\(request\.path, request\.body\)/,
    "the answer path goes through the S4 client, not a hand-built request",
  );
  assert.match(
    source,
    /addon\.service\.pollTask\(/,
    "polling is the S4 poller, so cancel-on-dispose works",
  );
  assert.match(
    source,
    /cloudDisabled\(status\)/,
    "the cloud check happens before a doomed run is submitted",
  );
});

test("U3 wiring: a citation click jumps, and copies when the host cannot", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");

  const jump = functionBody(source, "activateJump");
  assert.match(jump, /jumpPlan\(href, \{ bridgeAvailable: canOpenHostLink\(\) \}\)/);
  assert.match(
    jump,
    /copyLink\(doc, mount, plan\.href\)/,
    "a bridge that is unavailable must degrade to copying, visibly",
  );
  const badge = functionBody(source, "renderBadge");
  assert.match(
    badge,
    /dataset\.jump = href/,
    "the badge carries the href the delegated click handler reads",
  );
  assert.match(
    badge,
    /element\(doc, "span", "lt-badge"\)/,
    "a badge without a jump target stays a span, so it cannot look clickable",
  );
  const copy = functionBody(source, "copyLink");
  assert.match(
    copy,
    /await clipboard\.writeText\(href\)/,
    "the clipboard write is awaited, so the notice reports what actually happened",
  );
  assert.match(
    copy,
    /showMessage\(/,
    "the copied (or failed) state must be visible, never a silent no-op",
  );
  assert.match(
    copy,
    /execCommand\("copy"\)/,
    "privileged reader documents need the textarea fallback",
  );
});

test("U3 wiring: the in-flight slot is claimed before the form is painted", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");

  // syncFormState() reads canAsk(), which reads mount.flight. If the slot is
  // claimed after the paint, the submit button stays live for the whole
  // request: A3 only got away with it because the flight lasted 350 ms.
  for (const [name, marker] of [
    ["run", "mount.state = ask("],
    ["retryCard", "mount.state = retry("],
  ]) {
    const body = functionBody(source, name);
    const claimAt = body.indexOf("beginFlight(mount)");
    const paintAt = body.indexOf("syncFormState(mount)");
    assert.notEqual(claimAt, -1, `${name} must claim the in-flight slot`);
    assert.notEqual(paintAt, -1, `${name} must sync the form`);
    assert.ok(
      claimAt < paintAt,
      `${name} claims the flight after syncFormState, so the submit button stays enabled for the whole request`,
    );
    assert.ok(
      body.indexOf(marker) < paintAt,
      `${name} must paint its card before the form sync`,
    );
  }

  // dispatch() must not start a second flight: the caller owns the slot, and a
  // second beginFlight would invalidate the caller's own epoch.
  const dispatch = functionBody(source, "dispatch");
  assert.doesNotMatch(
    dispatch,
    /beginFlight\(/,
    "dispatch receives the claimed flight instead of starting another",
  );
  for (const body of ["runAnswer", "runRead"]) {
    assert.doesNotMatch(
      functionBody(source, body),
      /beginFlight\(/,
      `${body} must use the caller's flight, not claim its own`,
    );
  }
});

test("U3 wiring: dispose stops the in-flight request and a stale answer cannot paint", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");

  const begin = functionBody(source, "beginFlight");
  assert.match(
    begin,
    /mount\.epoch \+= 1/,
    "every flight starts a new epoch",
  );
  assert.match(
    begin,
    /mount\.epoch === epoch/,
    "alive() must compare the epoch, or a late answer paints into another paper's panel",
  );

  const dispose = functionBody(source, "disposeMount");
  assert.match(dispose, /mount\.flight\.cancel\(\)/, "disposal must cancel the poller");
  assert.match(dispose, /mount\.epoch \+= 1/, "disposal must invalidate in-flight callbacks");

  const prune = functionBody(source, "pruneDetachedMounts");
  assert.match(prune, /mount\.flight\.cancel\(\)/, "a detached panel must not keep polling");

  for (const body of ["runAnswer", "runRead"]) {
    assert.match(
      functionBody(source, body),
      /if \(!flight\.alive\(\)\) \{\s*return;/,
      `${body} must drop a response for a superseded flight`,
    );
  }
});

test("U3 wiring: the guide card has no retry, the failure card has one", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");
  const render = functionBody(source, "renderCard");

  const guide = render.indexOf("case CARD_KINDS.GUIDE");
  const failure = render.indexOf("case CARD_KINDS.FAILURE");
  assert.ok(guide !== -1 && failure !== -1, "both card kinds must be rendered");
  const guideBlock = render.slice(guide, failure);
  assert.doesNotMatch(
    guideBlock,
    /dataset\.retry/,
    "the guide card must not render a retry button: retrying without a key fails identically",
  );
  assert.match(
    guideBlock,
    /dataset\.settings/,
    "the guide card offers exactly one action, and it opens the settings page",
  );
  const failureBlock = render.slice(failure);
  assert.match(failureBlock, /dataset\.retry/, "the failure card keeps its retry");
});

test("U3 wiring: a question that cannot form a request is reported, not left running", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");
  const run = functionBody(source, "run");

  const buildAt = run.indexOf("requestFor(action, question");
  const cardAt = run.indexOf("mount.state = ask(");
  assert.notEqual(buildAt, -1, "run must build the request");
  assert.notEqual(cardAt, -1, "run must append the question card");
  assert.ok(
    buildAt < cardAt,
    "building the request first means a rejected question never leaves a progress row that cannot finish",
  );
  assert.match(
    run,
    /showMessage\(mount, errorMessage\(error\)\)/,
    "the refusal must be visible",
  );
});

// ---------------------------------------------------------------------------
// Review findings on PR #20. These are wiring bugs in readerPanel.ts, which
// cannot run under node (it needs a Document and a Zotero global). The checks
// below therefore pin the SHAPE of the code that fixes each one: the
// regression is a specific statement going missing, and each assertion names
// the symptom that statement prevents.
// ---------------------------------------------------------------------------

test("finding 4609667207: the stream is replaced, so a stale progress row cannot accumulate", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");
  const paint = functionBody(source, "paintStream");

  assert.match(
    paint,
    /stream\.replaceChildren\(/,
    "paintStream must replace the child list; appending leaves .lt-stage and .lt-empty in the stream forever",
  );
  assert.doesNotMatch(
    paint,
    /for \(const node of ordered\) \{\s*stream\.append\(node\)/,
    "appending ordered nodes is exactly the bug: the old progress row stays and one more is added per question",
  );
  // The reuse map is what keeps <details> state across a repaint.
  assert.match(
    paint,
    /data-card-id/,
    "card nodes are still reused by id, so replaceChildren does not reset disclosure state",
  );
});

test("finding 4609678628: panels for a closed reader document are pruned, not leaked", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");

  assert.match(
    source,
    /function pruneDetachedMounts\(/,
    "there must be a sweep for mounts whose document is gone",
  );
  const prune = functionBody(source, "pruneDetachedMounts");
  assert.match(
    prune,
    /isConnected/,
    "isConnected is the detachment check that needs no particular reader event name",
  );
  assert.match(prune, /mounts\.delete\(doc\)/, "a detached mount must leave the map");
  assert.match(
    prune,
    /mount\.flight\.cancel\(\)/,
    "a detached mount's in-flight request must be cancelled too (U3: the pending work is a poller, not a timer)",
  );

  const open = functionBody(source, "openPanel");
  assert.match(
    open,
    /pruneDetachedMounts\(\)/,
    "every open path must sweep first, so the map cannot grow across closed PDFs",
  );
});

test("finding 4609678628: the in-flight marker is cleared before the outcome is built", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");
  const settle = functionBody(source, "settle");

  const clearAt = settle.indexOf("mount.flight = null");
  // Real call sites only: the prose comment above the clear line mentions
  // `apply()` too, and matching that would compare against a comment.
  const buildAt = settle.indexOf("apply();");
  assert.notEqual(clearAt, -1, "settle must clear the in-flight marker");
  assert.notEqual(buildAt, -1, "settle must build an outcome");
  assert.ok(
    clearAt < buildAt,
    "clearing after the outcome leaves the marker set when the mapper throws, which would refuse every later question forever",
  );
  assert.match(
    settle,
    /catch \(error\)/,
    "a throwing outcome must be caught so the panel cannot stay busy",
  );
});

test("finding 4609683072: a refused question is visible and its input is not consumed", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");

  // run() must report whether the question started.
  assert.match(
    source,
    /function run\([\s\S]*?\): boolean \{/,
    "run must return whether it started the question, so callers can decide about the input",
  );
  const runBody = functionBody(source, "run");
  assert.match(
    runBody,
    /return false/,
    "a refused question reports false instead of returning silently",
  );
  assert.match(
    runBody,
    /showNotice\(mount, "reader-panel-wait"\)/,
    "the refusal must say why -- the repository rule is no silent failure",
  );

  // The form clears the input only on success.
  const create = functionBody(source, "createPanel");
  const submit = create.slice(create.indexOf('form.addEventListener("submit"'));
  assert.ok(
    submit.indexOf("if (run(doc, mount, question, null))") !== -1,
    "the submit handler must gate on run()'s result",
  );
  const clearAt = submit.indexOf('input.value = ""');
  const runAt = submit.indexOf("run(doc, mount, question, null)");
  assert.ok(
    clearAt > runAt,
    "clearing the input before run() is the reported bug: a mid-flight question vanishes",
  );

  // The selection path keeps the selected text in the input when it cannot ask.
  const popupStart = source.indexOf("export function onRenderTextSelectionPopup(");
  const popupEnd = source.indexOf("export function onRenderToolbar(");
  assert.ok(popupStart !== -1 && popupEnd > popupStart, "both handlers must exist");
  const popup = source.slice(popupStart, popupEnd);
  assert.ok(
    popup.indexOf("if (run(doc, mount, selection, button.action))") !== -1,
    "the selection handler must also gate on run()'s result",
  );
  assert.match(
    popup,
    /input\.value = selection;/,
    "a refused selection question must put the selected text somewhere visible",
  );
});

test("finding 4609683072: the in-flight rule is visible on the form, not just enforced", () => {
  const source = fs.readFileSync(PANEL_TS, "utf8");

  assert.match(
    source,
    /function syncFormState\(/,
    "the form must reflect the in-flight state",
  );
  const sync = functionBody(source, "syncFormState");
  assert.match(sync, /submit\.disabled = busy/, "the submit button is disabled while asking");
  assert.match(
    sync,
    /reader-panel-wait/,
    "the placeholder must say why submission is unavailable",
  );
});

test("the new wait message exists in both locale packs", async () => {
  const mod = await core();
  assert.ok(
    mod.MESSAGE_KEYS.includes("reader-panel-wait"),
    "the refusal message must be registered, or the panel would show a raw key",
  );
  for (const locale of ["en-US", "zh-CN"]) {
    const ftl = fs.readFileSync(
      path.join(PLUGIN_DIR, "addon", "locale", locale, "addon.ftl"),
      "utf8",
    );
    assert.match(ftl, /^reader-panel-wait =/m, `${locale} is missing reader-panel-wait`);
  }
});
