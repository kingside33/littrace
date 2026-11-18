"use strict";

// Anchors the lifecycle wiring of the plugin skeleton (A1).
//
// Finding on PR #13 (hooks.ts:36): onMainWindowUnload called the
// argument-less unregisterAll, which disposes the module-level store for
// every window - closing one main window tore down the registrations of the
// windows still open. This file guards which function each lifecycle hook
// calls; the bookkeeping itself is tested in tests/test_plugin_registry.cjs.
//
// Node built-ins only, and pins the convention that nothing in this block
// may reach outside `node:` built-ins and relative paths: the paths here are
// resolved from source text, never by running the plugin.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const HOOKS = path.join(__dirname, "..", "plugin", "src", "hooks.ts");

function readHooks() {
  return fs.readFileSync(HOOKS, "utf8");
}

function bodyOf(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `hooks.ts must define ${name}`);
  const open = source.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    if (source[i] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(open + 1, i);
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

test("onMainWindowUnload disposes only the window it is handed", () => {
  const hooks = readHooks();
  assert.match(
    hooks,
    /async function onMainWindowUnload\(\s*win: Window\s*\)/,
    "onMainWindowUnload must take the window, not ignore it",
  );
  const body = bodyOf(hooks, "onMainWindowUnload");
  assert.match(body, /unregisterWindow\(win\)/, "must dispose the closing window");
  assert.doesNotMatch(
    body,
    /unregisterAll\(/,
    "must not tear down every window when one closes",
  );
});

test("onShutdown still reclaims every registration", () => {
  const body = bodyOf(readHooks(), "onShutdown");
  assert.match(body, /unregisterAll\(\)/, "disable/uninstall must reclaim everything");
});

test("onMainWindowLoad registers for the window it was given", () => {
  const body = bodyOf(readHooks(), "onMainWindowLoad");
  assert.match(body, /registerAll\(win\)/);
});
