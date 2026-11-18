"use strict";

// Anchors the toolbar service status dot and its diagnostic popup (U5:
// 设置页、Fluent 文案与服务状态点).
//
// The logic under test lives in plugin/src/status/statusCore.mjs: plain ESM
// with no Zotero/DOM globals, so `node --test` runs the real three-colour
// decision table, the version comparison, the diagnostic row model and the
// context-menu enable rules without npm install or a Zotero process.
//
// The DOM half (toolbar injection, panel positioning, right-click menu) is
// 真机验收 territory and is not faked here. What is asserted about the
// tooling is the wiring: the dot probes through the S4 client's `probe()`
// (GET /api/version, one request discipline), polls sequentially, and every
// user-visible string comes from the Fluent bundles.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const PLUGIN_DIR = path.join(__dirname, "..", "plugin");
const STATUS_CORE = path.join(PLUGIN_DIR, "src", "status", "statusCore.mjs");
const SERVICE_CORE = path.join(PLUGIN_DIR, "src", "serviceClientCore.mjs");
const STATUS_TS = path.join(PLUGIN_DIR, "src", "status", "status.ts");
const DIAGNOSTICS_TS = path.join(PLUGIN_DIR, "src", "status", "diagnostics.ts");
const REGISTRY_TS = path.join(PLUGIN_DIR, "src", "registry.ts");
const FTL_DIR = path.join(PLUGIN_DIR, "addon", "locale");

const BASE = "http://127.0.0.1:8765";

async function loadCore() {
  return import(pathToFileURL(STATUS_CORE).href);
}

function readSource(file) {
  return fs.readFileSync(file, "utf8");
}

/** Parse one .ftl into name → value. Comments and blanks are skipped. */
function parseFtl(locale) {
  const file = path.join(FTL_DIR, locale, "addon.ftl");
  const entries = new Map();
  for (const line of readSource(file).split(/\r?\n/)) {
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

test("the dot has three settled colours plus a distinct checking state", async () => {
  const core = await loadCore();
  assert.deepEqual(
    [core.DOT_STATES.CHECKING, core.DOT_STATES.ONLINE, core.DOT_STATES.OUTDATED, core.DOT_STATES.OFFLINE],
    ["checking", "online", "outdated", "offline"],
  );
  assert.deepEqual(Object.keys(core.DOT_VIEWS).sort(), [
    "checking",
    "offline",
    "online",
    "outdated",
  ]);
  // 绿 / 黄 / 灰: the three settled colours must be distinguishable values,
  // and the transient checking dot must never be mistaken for a verdict.
  const settled = ["online", "outdated", "offline"].map(
    (state) => core.DOT_VIEWS[state].color.toLowerCase(),
  );
  assert.equal(new Set(settled).size, 3, "settled dots must have distinct colours");
  for (const state of ["checking", "online", "outdated", "offline"]) {
    assert.ok(core.DOT_VIEWS[state].color);
    assert.match(core.DOT_VIEWS[state].labelKey, /^status-dot-/);
  }
  assert.ok(Object.isFrozen(core.DOT_VIEWS));
});

test("S4 health verdicts map onto the dot exactly as the plan specifies", async () => {
  const core = await loadCore();
  // 绿 = 在线且版本兼容.
  assert.equal(core.dotStateOf(AVAILABLE), "online");
  // 黄 = 版本过旧 (and, honestly, any other reachable-but-incompatible answer:
  // a grey dot would claim the service is offline, which is false).
  assert.equal(
    core.dotStateOf({ state: "unsupported", reason: "version-too-old", ok: false }),
    "outdated",
  );
  assert.equal(
    core.dotStateOf({ state: "unsupported", reason: "api-version-mismatch", ok: false }),
    "outdated",
  );
  assert.equal(
    core.dotStateOf({ state: "unsupported", reason: "malformed-response", ok: false }),
    "outdated",
  );
  // 灰 = 离线 (no response, or a non-2xx answer on the port).
  assert.equal(
    core.dotStateOf({ state: "offline", reason: "unreachable", ok: false }),
    "offline",
  );
  assert.equal(
    core.dotStateOf({ state: "offline", reason: "http-500", ok: false }),
    "offline",
  );
  // Unknown input never claims green.
  for (const unknown of [null, undefined, {}, { state: "future-state" }, 42]) {
    assert.equal(core.dotStateOf(unknown), "offline", `unexpected state for ${unknown}`);
  }
});

test("the dot view carries colour, label, reason and the versions involved", async () => {
  const core = await loadCore();
  const pending = core.dotView(null, { pending: true });
  assert.equal(pending.state, "checking");
  assert.equal(pending.blinking, true);
  assert.equal(pending.labelKey, "status-dot-checking");

  const online = core.dotView(AVAILABLE);
  assert.equal(online.state, "online");
  assert.equal(online.blinking, false);
  assert.equal(online.serviceVersion, "0.1.0");
  assert.equal(online.minServiceVersion, "0.1.0");
  assert.equal(online.labelKey, "status-dot-online");
  // An online dot has no reason to explain.
  assert.equal(online.reason, null);

  const outdated = core.dotView({
    state: "unsupported",
    reason: "version-too-old",
    ok: false,
    serviceVersion: "0.0.8",
  });
  assert.equal(outdated.state, "outdated");
  assert.equal(outdated.reason, "version-too-old");
  assert.equal(outdated.reasonKey, "status-reason-version-old");
  assert.equal(outdated.serviceVersion, "0.0.8");

  const offline = core.dotView({ state: "offline", reason: "http-404", ok: false });
  assert.equal(offline.state, "offline");
  assert.equal(offline.reasonKey, "status-reason-http");

  assert.equal(core.reasonKey("api-version-mismatch"), "status-reason-api-mismatch");
  assert.equal(core.reasonKey("no-such-reason"), "status-reason-unknown");
  assert.equal(core.reasonKey(null), "status-reason-unknown");
});

test("the service-version comparison is a pure function at tested boundaries", async () => {
  const core = await loadCore();
  // Equality with the floor is compatible, one patch below is not.
  assert.equal(core.versionRelation("0.1.0").relation, "ok");
  assert.equal(core.versionRelation("0.0.9").relation, "too-old");
  assert.equal(core.versionRelation("1.0.0").relation, "ok");
  assert.equal(core.versionRelation("0.1.0-beta").relation, "ok");

  // A max bound turns the same comparison into the other direction.
  const bounded = { minServiceVersion: "0.1.0", maxServiceVersion: "0.1.5" };
  assert.equal(core.versionRelation("0.1.5", bounded).relation, "ok");
  assert.equal(core.versionRelation("0.1.6", bounded).relation, "too-new");

  // An absent service version is "unknown", never "ok".
  for (const missing of [null, undefined, "", "1.0", "v1.2.3", 7]) {
    assert.equal(core.versionRelation(missing).relation, "unknown", `unexpected for ${missing}`);
  }

  // The floor is S4's, not a second constant in this feature.
  const s4 = await import(pathToFileURL(SERVICE_CORE).href);
  assert.equal(core.minServiceVersionOf(), s4.DEFAULT_REQUIREMENTS.minServiceVersion);

  // An explicit null disables the bound; a malformed one is loud.
  assert.equal(
    core.versionRelation("0.0.1", { minServiceVersion: null }).relation,
    "ok",
  );
  assert.throws(
    () => core.versionRelation("0.1.0", { minServiceVersion: "latest" }),
    /minServiceVersion is not a version/,
  );
  assert.throws(
    () => core.versionRelation("0.1.0", { maxServiceVersion: "0.x" }),
    /maxServiceVersion is not a version/,
  );
});

test("the diagnostic port comes from the origin actually in use", async () => {
  const core = await loadCore();
  assert.equal(core.portOf(BASE), 8765);
  assert.equal(core.portOf(`${BASE}/`), 8765);
  assert.equal(core.portOf("http://127.0.0.1"), 80);
  assert.equal(core.portOf("https://127.0.0.1"), 443);
  assert.equal(core.portOf("http://127.0.0.1:8766/api"), 8766);
  for (const invalid of ["", null, undefined, "127.0.0.1:8765", "http://127.0.0.1:0", "http://127.0.0.1:99999"]) {
    assert.equal(core.portOf(invalid), null, `unexpected port for ${invalid}`);
  }
});

test("Ollama reachability is derived from the service, never probed directly", async () => {
  const core = await loadCore();
  // kbapp/integrations/models.py sets installed to a boolean when Ollama
  // answered /api/tags and to null when it did not.
  assert.equal(core.ollamaState({ items: [{ installed: false }] }), "reachable");
  assert.equal(core.ollamaState({ items: [{ installed: null }, { installed: true }] }), "reachable");
  assert.equal(core.ollamaState({ items: [{ installed: null }] }), "unreachable");
  for (const unknown of [null, undefined, {}, { items: [] }, { items: "x" }, []]) {
    assert.equal(core.ollamaState(unknown), "unknown", `unexpected state for ${unknown}`);
  }
});

test("the last sync time prefers the most specific timestamp", async () => {
  const core = await loadCore();
  assert.equal(
    core.lastSyncAt({ export_sync: { updated: "2026-11-18T09:00:00+08:00" } }),
    "2026-11-18T09:00:00+08:00",
  );
  assert.equal(core.lastSyncAt({ update_finished_at: "2026-11-17T09:00:00" }), "2026-11-17T09:00:00");
  assert.equal(core.lastSyncAt({ index_published_at: "2026-11-16T09:00:00" }), "2026-11-16T09:00:00");
  assert.equal(core.lastSyncAt({ export_sync: { status: "synced" } }), null);
  for (const empty of [null, undefined, {}, [], "x"]) {
    assert.equal(core.lastSyncAt(empty), null);
  }
  // Minute precision, deterministic across platforms.
  assert.equal(core.displayStamp("2026-11-18T09:07:31+08:00"), "2026-11-18 09:07");
  assert.equal(core.displayStamp("2026-11-18 09:07"), "2026-11-18 09:07");
  assert.equal(core.displayStamp("not-a-date"), "not-a-date");
  assert.equal(core.displayStamp(null), null);
});

test("diagnostic rows cover the four required facts and fail closed", async () => {
  const core = await loadCore();
  const healthy = core.diagnosticRows({
    health: AVAILABLE,
    baseUrl: BASE,
    // The service's own model report: installed is null only when it could
    // not reach Ollama.
    models: { items: [{ installed: true }] },
    status: { export_sync: { updated: "2026-11-18T09:00:00+08:00" } },
  });
  assert.deepEqual(
    healthy.map((row) => row.id),
    ["service-version", "requirement", "version-relation", "port", "ollama", "last-sync"],
    "every required diagnostic row is present, in a stable order",
  );
  assert.deepEqual(healthy.map((row) => row.labelKey), core.DIAGNOSTIC_ROWS.map((row) => row.labelKey));
  const byId = Object.fromEntries(healthy.map((row) => [row.id, row]));
  assert.equal(byId["service-version"].value, "0.1.0");
  assert.equal(byId.requirement.value, "0.1.0");
  assert.equal(byId["version-relation"].valueKey, "status-diag-version-ok");
  assert.equal(byId.port.value, "8765");
  assert.equal(byId.ollama.valueKey, "status-diag-ollama-reachable");
  assert.equal(byId["last-sync"].value, "2026-11-18 09:00");
  assert.deepEqual(core.failedRows(healthy), []);

  // Offline: the popup still lists every check, and the service rows are bad.
  const offline = core.diagnosticRows({
    health: { state: "offline", reason: "unreachable", ok: false },
    baseUrl: BASE,
    models: null,
    status: null,
  });
  assert.equal(offline.length, core.DIAGNOSTIC_ROWS.length);
  const down = Object.fromEntries(offline.map((row) => [row.id, row]));
  assert.equal(down["service-version"].tone, "bad");
  assert.equal(down["service-version"].valueKey, "status-diag-unavailable");
  assert.equal(down["version-relation"].tone, "bad");
  assert.equal(down.ollama.valueKey, "status-diag-ollama-unknown");
  assert.equal(down["last-sync"].valueKey, "status-diag-never-synced");
  // The port is still knowable: it is local configuration, not a service answer.
  assert.equal(down.port.value, "8765");
  assert.ok(core.failedRows(offline).length >= 2, "offline failures are listed");

  // Version too old: yellow in the dot, and the popup names the reason.
  const old = core.diagnosticRows({
    health: {
      state: "unsupported",
      reason: "version-too-old",
      ok: false,
      serviceVersion: "0.0.9",
    },
    baseUrl: BASE,
    models: null,
    status: null,
  });
  const oldById = Object.fromEntries(old.map((row) => [row.id, row]));
  assert.equal(oldById["service-version"].value, "0.0.9");
  assert.equal(oldById["version-relation"].valueKey, "status-diag-version-old");
  assert.equal(oldById["version-relation"].tone, "warn");
  assert.ok(core.failedRows(old).some((row) => row.id === "version-relation"));

  // Every row is either a literal value or a Fluent key — never neither.
  for (const row of healthy.concat(offline, old)) {
    assert.ok(
      row.value !== null || row.valueKey !== null,
      `diagnostic row ${row.id} must render something`,
    );
    assert.match(row.labelKey, /^status-diag-/);
  }
});

test("the context menu greys only the web workbench", async () => {
  const core = await loadCore();
  for (const reachable of ["online", "outdated"]) {
    const items = core.menuItems(reachable);
    assert.deepEqual(items.map((item) => item.id), ["workbench", "diagnostics"]);
    assert.equal(items.every((item) => item.enabled), true, `${reachable} enables both`);
  }
  for (const unreachable of ["offline", "checking"]) {
    const items = core.menuItems(unreachable);
    const byId = Object.fromEntries(items.map((item) => [item.id, item]));
    assert.equal(byId.workbench.enabled, false, `${unreachable} greys the workbench`);
    assert.equal(byId.diagnostics.enabled, true, "diagnostics is the way out of a grey dot");
  }
  for (const item of core.STATUS_MENU) {
    assert.match(item.labelKey, /^status-menu-/);
  }
  assert.equal(core.workbenchUrl(BASE), `${BASE}/`);
  assert.equal(core.workbenchUrl(`${BASE}/`), `${BASE}/`);
  assert.equal(core.workbenchUrl(""), null);
  assert.equal(core.workbenchUrl(null), null);
});

test("every Fluent key the status layer uses exists in both bundles", async () => {
  const core = await loadCore();
  const used = new Set();
  for (const view of Object.values(core.DOT_VIEWS)) {
    used.add(view.labelKey);
  }
  for (const key of Object.values(core.REASON_KEYS)) {
    used.add(key);
  }
  for (const key of Object.values(core.RELATION_KEYS)) {
    used.add(key);
  }
  for (const key of Object.values(core.OLLAMA_KEYS)) {
    used.add(key);
  }
  for (const row of core.DIAGNOSTIC_ROWS) {
    used.add(row.labelKey);
  }
  for (const item of core.STATUS_MENU) {
    used.add(item.labelKey);
  }
  // The ad-hoc keys used by src/status/status.ts; extracted from the source so
  // a forgotten key fails here instead of rendering as a raw id at runtime.
  const source = readSource(STATUS_TS);
  for (const match of source.matchAll(/\bt\("([^"]+)"\)/g)) {
    used.add(match[1]);
  }
  assert.ok(used.size > 20, "the status layer declares its strings as keys");
  for (const locale of ["en-US", "zh-CN"]) {
    const ftl = parseFtl(locale);
    for (const key of used) {
      assert.ok(ftl.has(key), `${locale} is missing ${key}`);
      assert.ok(ftl.get(key).trim().length > 0, `${locale} ${key} is empty`);
    }
  }
  // Every status key belongs to this feature's namespace.
  for (const key of used) {
    assert.match(key, /^status-[a-z0-9-]+$/, `unexpected status key ${key}`);
  }
});

test("the status dot is registered through the plugin's single entry", () => {
  const registry = readSource(REGISTRY_TS);
  assert.match(
    registry,
    /import \{ createStatusDot \} from "\.\/status\/status";/,
    "registry must import the status dot from its module",
  );
  assert.match(
    registry,
    /const statusDot = createStatusDot\(win\);[\s\S]*?register\(win, "status-dot", \(\) => \{[\s\S]*?statusDot\.dispose\(\);/,
    "the dot must be a window registration with a disposer",
  );
});

test("the dot probes through S4 and polls sequentially, never on an interval", () => {
  const status = readSource(STATUS_TS);
  const diagnostics = readSource(DIAGNOSTICS_TS);

  // One request path: the S4 client's probe (GET /api/version, the S4 request
  // discipline). No direct Zotero.HTTP anywhere in this feature.
  assert.match(diagnostics, /serviceClient\(\)\.probe\(\)/);
  assert.doesNotMatch(status, /Zotero\.HTTP/, "the dot must not open its own HTTP path");
  assert.doesNotMatch(diagnostics, /Zotero\.HTTP/);

  // Sequential timeout, not setInterval: a slow service cannot stack probes.
  assert.match(status, /win\.setTimeout\(/);
  assert.doesNotMatch(status, /setInterval/);
  assert.match(status, /inFlight/);

  // Extra reads happen only for a compatible service, and each is optional.
  assert.match(diagnostics, /health\.state === "available"/);
  assert.match(diagnostics, /optionalGet\(STATUS_ENDPOINTS\.status\)/);
  assert.match(diagnostics, /optionalGet\(STATUS_ENDPOINTS\.models\)/);

  // The poll cadence is a deliberate constant, not a per-render choice.
  assert.ok(
    fs.readFileSync(STATUS_CORE, "utf8").includes("STATUS_POLL_MS = 30000"),
  );
});

test("no visible status string is hardcoded in the TypeScript edge", () => {
  const status = readSource(STATUS_TS);
  // A quoted literal assigned to textContent is exactly the string that would
  // stay English in the zh-CN build; every such assignment must go through
  // t() or through a variable built from t().
  // A quoted non-empty literal assigned to textContent is exactly the string
  // that would stay English in the zh-CN build; `= ""` (clearing) is fine.
  assert.doesNotMatch(status, /textContent\s*=\s*["'][^"']+["']/);
  assert.doesNotMatch(
    status,
    /setAttribute\(\s*["'](?:tooltiptext|label|aria-label)["']\s*,\s*["']/,
  );
  for (const match of status.matchAll(/textContent\s*=\s*`([^`]*)`/g)) {
    assert.match(match[1], /\bt\(/, "a template literal must still resolve through t()");
  }
  assert.match(status, /t\(view\.labelKey\)/);
});
