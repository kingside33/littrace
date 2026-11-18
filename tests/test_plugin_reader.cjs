"use strict";

// Anchors the reader evidence-Q&A panel prototype (A3).
//
// Three contracts are locked here, all of them executable without Zotero:
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

/** Question → answer, the happy path every append-only assertion builds on. */
function answeredFixture(mod) {
  let state = mod.createState();
  state = mod.ask(state, "这个方法在哪些实验条件下成立?");
  state = mod.stage(state, {
    labelKey: "reader-panel-stage-searching",
    progress: 40,
  });
  return mod.answer(state, mod.demoOutcome("evidence", "这个方法在哪些实验条件下成立?"));
}

/** A failure fixture: question, then a scripted error. */
function failedFixture(mod) {
  let state = mod.createState();
  state = mod.ask(state, "带 #fail 的问题");
  return mod.fail(state, { message: "本地服务连接中断" });
}

test("appending an answer keeps every earlier card by reference and frozen", async () => {
  const mod = await core();
  let state = mod.createState();
  state = mod.ask(state, "问题一");
  const question = state.cards[0];

  state = mod.answer(state, mod.demoOutcome("evidence", "问题一"));

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

test("a gap is a grey block with no disclosure", async () => {
  const mod = await core();
  const state = answeredFixture(mod);
  const gap = state.cards.find((card) => card.kind === "gap");
  assert.ok(gap, "the demo outcome must produce a gap card");

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
  assert.equal(view.message, "本地服务连接中断");
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
  assert.equal(fresh.text, "带 #fail 的问题", "the retry reuses the original question");
  assert.equal(fresh.retryOf, failureId);
  assert.ok(retried.busy, "a retry is in flight again");
  assert.equal(retried.stage.labelKey, "reader-panel-stage-searching");
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

test("both selection paths run through the static data function", async () => {
  const mod = await core();
  for (const action of ["evidence", "lineage"]) {
    const outcome = mod.demoOutcome(action, "选中一段正文");
    assert.equal(outcome.ok, true);
    assert.ok(outcome.conclusions.length > 0, `${action} must produce a card`);
    assert.ok(outcome.evidence.length > 0, `${action} must carry evidence text`);
    assert.ok(outcome.gaps.length > 0, `${action} must carry an honest gap`);
  }
});

test("the prototype can script a failure for acceptance without a live service", async () => {
  const mod = await core();
  const failed = mod.demoOutcome("evidence", "这句 #fail");
  assert.equal(failed.ok, false);
  assert.match(failed.error, /连接中断/);

  // The scripted failure goes through the real failure path.
  let state = mod.ask(mod.createState(), "这句 #fail");
  state = mod.fail(state, { message: failed.error });
  assert.equal(state.cards.at(-1).kind, "failure");
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
  assert.match(source, /STATIC DATA PATH/, "the fake-data path must be greppable");
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
