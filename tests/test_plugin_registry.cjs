"use strict";

// Anchors the window-scoped registration contract (A1 插件骨架与构建链).
//
// Findings on PR #13 that drove this file:
//   - registry.ts:29 - a globally unique id made registerAll, which runs once
//     per open main window, throw "duplicate registration id" on the second
//     window and abort every later registration.
//
// The logic under test lives in plugin/src/registryCore.mjs, which is plain
// ESM with no Zotero globals, so it runs here with node built-ins only and
// needs no npm install. The lifecycle wiring is covered by
// tests/test_plugin_hooks.cjs.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const PLUGIN_DIR = path.join(__dirname, "..", "plugin");
const CORE_URL = pathToFileURL(
  path.join(PLUGIN_DIR, "src", "registryCore.mjs"),
).href;

async function loadCore() {
  const mod = await import(CORE_URL);
  return mod.createRegistry;
}

function recorder() {
  const calls = [];
  const dispose = (name) => () => calls.push(name);
  return { calls, dispose };
}

test("the same id registers once per window without throwing", async () => {
  const createRegistry = await loadCore();
  const registry = createRegistry();
  const first = {};
  const second = {};
  const { calls, dispose } = recorder();

  registry.register(first, "item-column", dispose("first"));
  registry.register(second, "item-column", dispose("second"));

  registry.unregisterWindow(first);
  assert.deepEqual(calls, ["first"]);

  registry.unregisterWindow(second);
  assert.deepEqual(calls, ["first", "second"]);
});

test("a duplicate id still throws inside one window", async () => {
  const createRegistry = await loadCore();
  const registry = createRegistry();
  const win = {};

  registry.register(win, "item-column", () => {});
  assert.throws(
    () => registry.register(win, "item-column", () => {}),
    /duplicate window registration id "item-column"/,
  );
});

test("closing one window keeps the other windows' registrations alive", async () => {
  const createRegistry = await loadCore();
  const registry = createRegistry();
  const staying = {};
  const leaving = {};
  const { calls, dispose } = recorder();

  registry.register(staying, "reader-panel", dispose("staying"));
  registry.register(leaving, "reader-panel", dispose("leaving"));

  // This is what onMainWindowUnload now calls.
  registry.unregisterWindow(leaving);

  assert.deepEqual(calls, ["leaving"], "only the closing window is disposed");

  registry.unregisterWindow(staying);
  assert.deepEqual(calls, ["leaving", "staying"]);
});

test("unregisterEverything reclaims all windows and the globals", async () => {
  const createRegistry = await loadCore();
  const registry = createRegistry();
  const { calls, dispose } = recorder();

  registry.register({}, "column", dispose("window-a"));
  registry.register({}, "column", dispose("window-b"));
  registry.registerGlobal("notifier", dispose("global"));

  // This is what onShutdown calls.
  registry.unregisterEverything();

  assert.deepEqual(calls.sort(), ["global", "window-a", "window-b"]);
});

test("globals survive a window closing and die on shutdown", async () => {
  const createRegistry = await loadCore();
  const registry = createRegistry();
  const win = {};
  const { calls, dispose } = recorder();

  registry.register(win, "column", dispose("window"));
  registry.registerGlobal("notifier", dispose("global"));

  registry.unregisterWindow(win);
  assert.deepEqual(calls, ["window"], "a notifier outlives a single window");

  registry.unregisterEverything();
  assert.deepEqual(calls, ["window", "global"]);
});

test("claimWindow makes registerAll idempotent per window", async () => {
  const createRegistry = await loadCore();
  const registry = createRegistry();
  const win = {};

  assert.equal(registry.claimWindow(win), true);
  assert.equal(
    registry.claimWindow(win),
    false,
    "the same window must not be registered twice at startup",
  );
  assert.equal(registry.claimWindow({}), true, "a second window is new");
});

test("disposers run in reverse order and a throw does not stop the rest", async () => {
  const createRegistry = await loadCore();
  const errors = [];
  const registry = createRegistry((error, id) => errors.push(id));
  const win = {};

  registry.register(win, "first", () => {});
  registry.register(win, "boom", () => {
    throw new Error("disposer failed");
  });
  const seen = [];
  registry.register(win, "last", () => seen.push("last"));

  registry.unregisterWindow(win);

  assert.deepEqual(seen, ["last"], "the throw must not skip later disposers");
  assert.deepEqual(errors, ["boom"]);
});

test("re-registering after unregister is allowed", async () => {
  const createRegistry = await loadCore();
  const registry = createRegistry();
  const win = {};

  registry.register(win, "column", () => {});
  registry.unregisterWindow(win);
  registry.register(win, "column", () => {});

  registry.unregisterWindow(win);
  registry.unregisterWindow(win); // safe to call twice
});

test("register rejects a missing disposer and an empty id", async () => {
  const createRegistry = await loadCore();
  const registry = createRegistry();
  const win = {};

  assert.throws(() => registry.register(win, "column"), /must provide a disposer/);
  assert.throws(() => registry.register(win, "", () => {}), /non-empty string/);
});
