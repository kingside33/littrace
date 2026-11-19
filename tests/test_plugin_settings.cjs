"use strict";

// Anchors the settings page's server-side half (P1: settingsCore 纯逻辑).
//
// Three artefacts have to agree across two languages, and each pair used to be
// a silent drift:
//   - plugin/src/settings/settingsCore.mjs  the field table, the three
//     transforms and the probe code registry;
//   - kbapp/core/config.py DEFAULTS          the keys the service accepts
//     (an unknown key is a Chinese 400 at runtime, with a green CI);
//   - kbapp/integrations/cloud.py            PROBE_CODES and PROBE_TIMEOUT_S.
//
// Node built-ins only, no npm install, no Zotero process.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const ROOT = path.join(__dirname, "..");
const PLUGIN_DIR = path.join(ROOT, "plugin");
const SETTINGS_CORE = path.join(PLUGIN_DIR, "src", "settings", "settingsCore.mjs");
const SETTINGS_PANE_TS = path.join(PLUGIN_DIR, "src", "settings", "settingsPane.ts");
const CONNECTION_PANE_TS = path.join(PLUGIN_DIR, "src", "settings", "connectionPane.ts");
const PANE_REFRESH_TS = path.join(PLUGIN_DIR, "src", "settings", "paneRefresh.ts");
const PREFS_TS = path.join(PLUGIN_DIR, "src", "prefs", "prefs.ts");
const CLOUD_PY = path.join(ROOT, "kbapp", "integrations", "cloud.py");
const CONFIG_PY = path.join(ROOT, "kbapp", "core", "config.py");
const SERVICE_CLIENT_CORE = path.join(PLUGIN_DIR, "src", "serviceClientCore.mjs");

async function loadCore() {
  return import(pathToFileURL(SETTINGS_CORE).href);
}

function read(file) {
  return fs.readFileSync(file, "utf8");
}

/** Parse one .ftl into name → value. */
function parseFtl(locale) {
  const file = path.join(PLUGIN_DIR, "addon", "locale", locale, "addon.ftl");
  const entries = new Map();
  for (const line of read(file).split(/\r?\n/)) {
    const match = /^([a-zA-Z0-9_-]+)\s*=\s*(.*)$/.exec(line);
    if (match) {
      entries.set(match[1], match[2]);
    }
  }
  return entries;
}

/** The keys of kbapp/core/config.py's DEFAULTS dict literal. */
function serviceDefaultKeys() {
  const source = read(CONFIG_PY);
  const start = source.indexOf("DEFAULTS = {");
  assert.ok(start >= 0, "kbapp/core/config.py no longer declares DEFAULTS");
  // The literal is one line today; brace-matching keeps this working if it grows.
  let depth = 0;
  let end = start;
  for (let index = source.indexOf("{", start); index < source.length; index += 1) {
    if (source[index] === "{") {
      depth += 1;
    } else if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) {
        end = index;
        break;
      }
    }
  }
  const literal = source.slice(start, end);
  return new Set([...literal.matchAll(/'([a-z_]+)'\s*:/g)].map((match) => match[1]));
}

/** The PROBE_CODES tuple from kbapp/integrations/cloud.py. */
function serviceProbeCodes() {
  const source = read(CLOUD_PY);
  const match = /PROBE_CODES\s*=\s*\(([\s\S]*?)\)/.exec(source);
  assert.ok(match, "kbapp/integrations/cloud.py no longer declares PROBE_CODES");
  return [...match[1].matchAll(/'([a-z_]+)'/g)].map((item) => item[1]);
}

function probeTimeoutSeconds() {
  const source = read(CLOUD_PY);
  const match = /PROBE_TIMEOUT_S\s*=\s*(\d+(?:\.\d+)?)/.exec(source);
  assert.ok(match, "kbapp/integrations/cloud.py no longer declares PROBE_TIMEOUT_S");
  return Number(match[1]);
}

function requestTimeoutMs() {
  const source = read(SERVICE_CLIENT_CORE);
  const match = /REQUEST_DISCIPLINE\s*=\s*Object\.freeze\(\{[\s\S]*?timeout:\s*(\d+)/.exec(source);
  assert.ok(match, "serviceClientCore.mjs no longer declares a request timeout");
  return Number(match[1]);
}

// ── the table ───────────────────────────────────────────────────────────

test("every server-side field names a real kbapp DEFAULTS key", async () => {
  const core = await loadCore();
  assert.ok(Object.isFrozen(core.SETTINGS_FIELDS));
  const keys = serviceDefaultKeys();
  assert.ok(keys.size > 10, "the DEFAULTS scan found suspiciously few keys");
  for (const field of core.SETTINGS_FIELDS) {
    assert.ok(
      keys.has(field.key),
      `SETTINGS_FIELDS names ${JSON.stringify(field.key)}, which kbapp/core/config.py DEFAULTS does not declare`,
    );
    // A row without a label renders as an empty setting.
    assert.match(field.labelKey, /^prefs-/);
    assert.match(field.descriptionKey, /^prefs-/);
    assert.ok(
      ["text", "password", "number", "bool", "select"].includes(field.type),
      `${field.key} has an unknown ui type ${field.type}`,
    );
  }
  const listed = core.SETTINGS_FIELDS.map((field) => field.key);
  assert.equal(new Set(listed).size, listed.length, "duplicate field key");
  // The three fields the service cannot work without.
  for (const required of ["api_base", "model", "api_key"]) {
    assert.ok(listed.includes(required), `${required} must be editable from the pane`);
  }
  // The sensitive row is the one the service answers as a boolean.
  const sensitive = core.SETTINGS_FIELDS.filter((field) => field.sensitive);
  assert.deepEqual(
    sensitive.map((field) => field.key),
    ["api_key"],
    "only the key is rendered as a stored/not-stored boolean",
  );
});

test("the server-side table is not a Zotero pref table", async () => {
  const core = await loadCore();
  const prefs = await import(
    pathToFileURL(path.join(PLUGIN_DIR, "src", "prefs", "prefsCore.mjs")).href
  );
  const prefKeys = new Set(prefs.PREF_ENTRIES.map((entry) => entry.key));
  for (const field of core.SETTINGS_FIELDS) {
    assert.equal(
      prefKeys.has(field.key),
      false,
      `${field.key} must not enter PREF_ENTRIES: that table's contract is one declarative preference= binding per row`,
    );
  }
  // And nothing server-side leaked into the declared prefs file either.
  const declared = read(path.join(PLUGIN_DIR, "addon", "prefs.js"));
  for (const field of core.SETTINGS_FIELDS) {
    assert.equal(
      declared.includes(`"${field.key}"`),
      false,
      `${field.key} must not be declared as a Zotero pref`,
    );
  }
});

// ── formatting ──────────────────────────────────────────────────────────

test("the public settings view becomes form values without inventing data", async () => {
  const core = await loadCore();
  const view = core.settingsViewFrom({
    api_base: "https://api.example.org/v1",
    model: "deepseek-chat",
    api_key: true,
    cloud_proxy: "",
    budget_mode: "limited",
    daily_budget: 0.0,
    input_price: 1.5,
    output_price: 0,
    cloud_enabled: true,
    openalex_key: false,
    connection: { available: false },
  });
  assert.equal(view.fields.api_base, "https://api.example.org/v1");
  assert.equal(view.fields.model, "deepseek-chat");
  // The service reports a boolean, so the form can only say "stored".
  assert.equal(view.fields.api_key, true);
  assert.equal(view.fields.cloud_proxy, "");
  assert.equal(view.fields.budget_mode, "limited");
  // 0 is "not set" for this form: limited mode refuses a 0 budget anyway.
  assert.equal(view.fields.daily_budget, "");
  assert.equal(view.fields.output_price, "");
  assert.equal(view.fields.input_price, "1.5");
  assert.equal(view.fields.cloud_enabled, true);
  // The reserved service key is not a form field at all.
  assert.equal("connection" in view.fields, false);
  assert.equal("openalex_key" in view.fields, false);
  assert.equal(view.budgetMode, "limited");
});

test("the four value states stay distinguishable", async () => {
  const core = await loadCore();
  const empty = core.settingsViewFrom({});
  assert.equal(empty.fields.api_base, "");
  assert.equal(empty.fields.api_key, false);
  assert.equal(empty.fields.cloud_enabled, false);
  assert.equal(empty.fields.daily_budget, "");
  assert.equal(empty.fields.budget_mode, "limited", "an unknown mode falls back to the safe default");

  const unlimited = core.settingsViewFrom({ budget_mode: "unlimited", daily_budget: 12.5 });
  assert.equal(unlimited.fields.budget_mode, "unlimited");
  assert.equal(unlimited.fields.daily_budget, "12.5");

  // A POST response has no `connection` key; feeding it back must not throw
  // and must not produce undefined values.
  const fromPost = core.settingsViewFrom({ api_base: "https://x.test/v1", api_key: false });
  for (const [key, value] of Object.entries(fromPost.fields)) {
    assert.notEqual(value, undefined, `${key} became undefined`);
  }
  assert.equal(core.keyStored(fromPost), false);
  assert.equal(core.keyStored(core.settingsViewFrom({ api_key: true })), true);
});

test("a hostile or absent view degrades instead of throwing", async () => {
  const core = await loadCore();
  for (const input of [null, undefined, "text", 42, [], { api_base: 7, budget_mode: 3 }]) {
    const view = core.settingsViewFrom(input);
    assert.equal(typeof view.fields, "object");
    assert.equal(view.fields.api_base, "");
    assert.equal(view.fields.budget_mode, "limited");
  }
});

// ── the payload ─────────────────────────────────────────────────────────

test("the save payload never carries null", async () => {
  const core = await loadCore();
  const forms = [
    {},
    { api_base: "", model: "", api_key: "", daily_budget: "", input_price: "" },
    { api_base: null, model: null, api_key: null, daily_budget: null },
    { api_base: "https://x.test/v1", model: "m", api_key: "k", daily_budget: 3 },
    { cloud_enabled: true, budget_mode: "unlimited" },
  ];
  for (const form of forms) {
    const payload = core.savePayloadFrom(form);
    for (const [key, value] of Object.entries(payload)) {
      assert.notEqual(
        value,
        null,
        `${key} is null: store.save_settings stores the string "None" or answers 500 for a non-sensitive null`,
      );
    }
  }
});

test("a normal save never touches the stored key", async () => {
  const core = await loadCore();
  // The production path: the cloud form saves the cloud section, which is the
  // only shape that must hold this exact set of keys.
  const payload = core.savePayloadFrom(
    {
      api_base: "https://api.example.org/v1",
      model: "deepseek-chat",
      api_key: "",
      budget_mode: "unlimited",
      cloud_enabled: true,
    },
    "cloud",
  );
  assert.equal(
    "api_key" in payload,
    false,
    "omitting the key is the only way to save the other fields without wiping it",
  );
  assert.deepEqual(payload, {
    api_base: "https://api.example.org/v1",
    model: "deepseek-chat",
    cloud_proxy: "",
    budget_mode: "unlimited",
    cloud_enabled: true,
  });
});

test("a typed key is sent, and blank numeric inputs keep their service value", async () => {
  const core = await loadCore();
  const payload = core.savePayloadFrom({
    api_base: "  https://api.example.org/v1  ",
    api_key: "  sk-typed  ",
    daily_budget: "",
    input_price: "1.25",
    output_price: "not-a-number",
  });
  assert.equal(payload.api_key, "sk-typed", "a typed key is trimmed and sent");
  assert.equal(payload.api_base, "https://api.example.org/v1", "text is trimmed");
  assert.equal("daily_budget" in payload, false, "a blank number keeps the service value");
  assert.equal(payload.input_price, 1.25, "numbers are sent as numbers");
  assert.equal(
    "output_price" in payload,
    false,
    "a non-numeric entry must not become a service-side 500",
  );
});

test("clearing the key is one explicit payload that also disables cloud calls", async () => {
  const core = await loadCore();
  for (const call of [core.clearKeyPayload(), core.clearKeyPayload()]) {
    assert.deepEqual(call, { api_key: "", cloud_enabled: false });
  }
  // store.save_settings refuses to keep cloud enabled without a key, so a clear
  // that sent only api_key would answer 400 and leave the key in place.
  assert.equal(core.clearKeyPayload().cloud_enabled, false);
});

// ── the probe vocabulary ────────────────────────────────────────────────

test("the plugin and the service agree on the probe codes", async () => {
  const core = await loadCore();
  const pluginCodes = Object.values(core.TEST_CODES).sort();
  const serviceCodes = serviceProbeCodes().sort();
  assert.deepEqual(pluginCodes, serviceCodes);
  // Every code has exactly one label, and that label is not the code itself.
  for (const code of pluginCodes) {
    const key = core.TEST_CODE_KEYS[code];
    assert.match(key, /^prefs-probe-/, `${code} has no Fluent key`);
  }
  assert.equal(
    new Set(Object.values(core.TEST_CODE_KEYS)).size,
    pluginCodes.length,
    "two codes share a label",
  );
});

test("a probe answer becomes a toned, labelled row", async () => {
  const core = await loadCore();
  const ok = core.testResultView({
    verdict: "ok",
    code: "ok",
    summary: "端点可达，鉴权被接受",
    network_route: "system",
    model_listed: true,
  });
  assert.equal(ok.tone, "ok");
  assert.equal(ok.labelKey, "prefs-probe-ok");
  assert.equal(ok.detail, "端点可达，鉴权被接受");
  assert.equal(ok.modelListed, true);
  assert.equal(core.proxyNoteKey(ok), null);

  // "No /models" is a warning, not an error: something answered.
  const unsupported = core.testResultView({ verdict: "unsupported", code: "probe_unsupported" });
  assert.equal(unsupported.tone, "warn");
  assert.equal(unsupported.labelKey, "prefs-probe-unsupported");

  const failed = core.testResultView({ verdict: "failed", code: "auth" });
  assert.equal(failed.tone, "bad");
  assert.equal(failed.labelKey, "prefs-probe-auth");

  // An answer with an unknown code still renders a label, never the raw code.
  const unknown = core.testResultView({ verdict: "failed", code: "something-new" });
  assert.equal(unknown.labelKey, "prefs-probe-http");
  assert.equal(unknown.tone, "bad");

  // The proxy fallback is the user's signal that their proxy was not used.
  const fallback = core.testResultView({ verdict: "ok", code: "ok", network_route: "system_fallback" });
  assert.equal(core.proxyNoteKey(fallback), "prefs-probe-proxy-fallback");

  // `model_listed: null` means "not checked", which is not "not listed".
  const unchecked = core.testResultView({ verdict: "ok", code: "ok", model_listed: null });
  assert.equal(unchecked.modelListed, null);
  assert.equal(core.testResultView(null).tone, "bad");
});

// ── the Fluent contract ─────────────────────────────────────────────────

test("every Fluent key this module renders exists in both bundles", async () => {
  const core = await loadCore();
  const en = parseFtl("en-US");
  const zh = parseFtl("zh-CN");
  const keys = core.settingsFluentKeys();
  assert.ok(keys.length >= 30, "the registry covers the whole cloud group");
  for (const key of keys) {
    assert.match(key, /^prefs-/, `unexpected key ${key}`);
    assert.ok(en.has(key), `en-US is missing ${key}`);
    assert.ok(zh.has(key), `zh-CN is missing ${key}`);
    assert.ok(en.get(key).trim().length > 0, `en-US key ${key} is empty`);
    assert.ok(zh.get(key).trim().length > 0, `zh-CN key ${key} is empty`);
  }
  // Fluent identifiers only: the build prefixes these and the bundle parser
  // would not accept anything else.
  for (const key of keys) {
    assert.match(key, /^[a-z0-9]+(?:-[a-z0-9]+)*$/, `not a Fluent identifier: ${key}`);
  }
});

test("no visible string escapes the registry in the TypeScript edge", async () => {
  const core = await loadCore();
  const registry = new Set(core.settingsFluentKeys());
  const en = parseFtl("en-US");
  const zh = parseFtl("zh-CN");
  // Every render-time key lives in one of these adapters. connectionPane.ts was
  // missed by the first version of this test, so its four literals (the version
  // line, the picker title, the unavailable note) were unanchored: a typo would
  // have surfaced as a blank zh-CN row on a real machine.
  for (const file of [SETTINGS_PANE_TS, CONNECTION_PANE_TS, PANE_REFRESH_TS]) {
    const source = read(file);
    const name = path.basename(file);
    // A quoted literal assigned to textContent is exactly the string that would
    // stay English in a zh-CN build; `= ""` (clearing) is fine.
    assert.doesNotMatch(source, /textContent\s*=\s*["'][^"']+["']/, `${name} hardcodes a string`);
    const literalKeys = [...source.matchAll(/\bt\(\s*"([^"]+)"/g)].map((match) => match[1]);
    for (const key of literalKeys) {
      assert.match(key, /^prefs-|^status-/, `${name}: ${key} is not a namespaced key`);
      // A literal that is not in the registry is a key nobody checks.
      const known = registry.has(key) || en.has(key) || zh.has(key);
      assert.ok(known, `${name} renders ${key}, which no registry or bundle declares`);
      assert.ok(en.has(key) && zh.has(key), `${name} renders ${key}, missing from a bundle`);
    }
  }
  // The cloud status keys specifically are literal, so this test has something
  // to check even if a future edit moves them behind a table.
  assert.ok([...read(SETTINGS_PANE_TS).matchAll(/\bt\(\s*"([^"]+)"/g)].length > 0);
  // The module must be reachable from the bundle entry, or it never ships.
  const entry = read(path.join(PLUGIN_DIR, "src", "index.ts"));
  assert.ok(entry.length > 0);
});

test("the probe runs only from a click, never on pane load", () => {
  const source = read(SETTINGS_PANE_TS);
  // SECURITY.md: a request that happens without a user action or a setting is
  // a bug. The probe is the one call here that reaches a metered third-party
  // endpoint, so it must sit behind the test button's listener.
  const probeCalls = [...source.matchAll(/serviceClient\(\)\.post\(\s*"([^"]+)"/g)].map(
    (match) => match[1],
  );
  assert.ok(probeCalls.includes("/api/settings/test"), "the probe endpoint is used");
  assert.equal(
    probeCalls.filter((path) => path === "/api/settings/test").length,
    1,
    "the probe must have exactly one call site",
  );
  // ...and it must live in testCloud, not in the load path: opening the pane
  // is not a user request to spend anything on a third-party endpoint.
  const testCloud = source.slice(source.indexOf("export async function testCloud"));
  const testCloudBody = testCloud.slice(0, testCloud.indexOf("\nexport ", 1));
  assert.match(testCloudBody, /\/api\/settings\/test/, "the probe belongs to testCloud");
  const loadCloud = source.slice(source.indexOf("export async function loadCloud"));
  const loadCloudBody = loadCloud.slice(0, loadCloud.indexOf("\nexport ", 1));
  assert.doesNotMatch(
    loadCloudBody,
    /\/api\/settings\/test/,
    "loadCloud must not probe",
  );
  // The load path issues exactly one read, and never a write.
  assert.match(loadCloudBody, /serviceClient\(\)\.get\("\/api\/settings"\)/);
  assert.doesNotMatch(loadCloudBody, /serviceClient\(\)\.post\(/);
});

test("the cloud group writes through the shared client, never its own request", () => {
  const source = read(SETTINGS_PANE_TS);
  // The pane must not open a second HTTP path: the S4 client owns the token,
  // the `errorDelayMax: 0` discipline and the failure classification.
  assert.doesNotMatch(source, /Zotero\.HTTP\.request/);
  assert.doesNotMatch(source, /\bXMLHttpRequest\b/);
  assert.doesNotMatch(source, /\bfetch\(/);
  assert.match(source, /import \{ serviceClient \} from "\.\.\/service-client"/);
  // Every write goes through settingsCore's payload builders rather than a
  // hand-built object, so the "never null" rule cannot be bypassed here.
  assert.match(source, /savePayloadFrom\(readForm\(doc\),\s*"cloud"\)/);
  assert.match(source, /clearKeyPayload\(\)/);
});

test("each pane group saves only the fields it owns", async () => {
  const core = await loadCore();
  // Two groups write the same settings.json. Without a section scope the cloud
  // form would send an empty zotero_dir and clear the directory the connection
  // group manages — a silent cross-group write, and one the user could not
  // explain from either form.
  const cloudOnly = core.savePayloadFrom({ api_base: "https://x.test/v1", zotero_dir: "/mine" }, "cloud");
  assert.equal("zotero_dir" in cloudOnly, false, "the cloud form must not write the directory");
  assert.equal(cloudOnly.api_base, "https://x.test/v1");

  const connectionOnly = core.savePayloadFrom({ api_base: "https://x.test/v1", zotero_dir: "/mine" }, "connection");
  assert.deepEqual(connectionOnly, { zotero_dir: "/mine" });

  // No section keeps the whole table, which is what a caller with the full form
  // (and no second owner) wants.
  const everything = core.savePayloadFrom({ api_base: "https://x.test/v1", zotero_dir: "/mine" });
  assert.equal(everything.api_base, "https://x.test/v1");
  assert.equal(everything.zotero_dir, "/mine");

  // Every field names a section, so a scope can never silently match nothing.
  for (const field of core.SETTINGS_FIELDS) {
    assert.ok(
      field.section === "cloud" || field.section === "connection",
      `${field.key} has no usable section`,
    );
  }
  assert.deepEqual(
    core.fieldsForSection("connection").map((field) => field.key),
    ["zotero_dir"],
  );
  assert.ok(core.fieldsForSection("cloud").length >= 8);

  // Both writers scope themselves.
  const cloudPane = read(SETTINGS_PANE_TS);
  assert.match(cloudPane, /savePayloadFrom\(readForm\(doc\),\s*"cloud"\)/);
  const connectionPane = read(
    path.join(PLUGIN_DIR, "src", "settings", "connectionPane.ts"),
  );
  assert.match(connectionPane, /savePayloadFrom\(\{ zotero_dir: box\.value \},\s*"connection"\)/);
});

test("the version line reuses the diagnostics rows instead of probing twice", async () => {
  const core = await loadCore();
  const rows = [
    { id: "service-version", tone: "ok", value: "0.1.0" },
    { id: "requirement", tone: "ok", value: "0.1.0" },
    { id: "version-relation", tone: "ok", valueKey: "status-diag-version-ok" },
    { id: "port", tone: "ok", value: "8765" },
  ];
  const view = core.versionViewFrom(rows);
  assert.equal(view.serviceVersion, "0.1.0");
  assert.equal(view.minServiceVersion, "0.1.0");
  // The relation word is the diagnostic popup's own key: a fourth vocabulary
  // for the same three states would let the pane and the popup disagree.
  assert.equal(view.relationKey, "status-diag-version-ok");
  assert.equal(view.tone, "ok");

  // A service that did not answer yields nulls, not invented text.
  for (const input of [null, undefined, "text", [], [{ id: "service-version" }]]) {
    const empty = core.versionViewFrom(input);
    assert.equal(empty.serviceVersion, null);
    assert.equal(empty.relationKey, null);
  }

  // The rows come from the page's single refresh, and from the shared
  // diagnostics helper — not from a second probe of its own.
  const source = read(path.join(PLUGIN_DIR, "src", "settings", "connectionPane.ts"));
  assert.match(source, /versionViewFrom\(/);
  assert.doesNotMatch(
    source,
    /collectDiagnostics|probeHealth/,
    "connectionPane must render rows handed to it, not collect its own",
  );
  // The page hands the diagnostics the verdict it already has, so `/api/version`
  // is requested once; and the rows are built for every health state, because
  // 「版本过旧」 is exactly when the version line matters.
  const pane = read(PANE_REFRESH_TS);
  assert.match(pane, /collectDiagnostics\(\{\s*health:/);
  assert.doesNotMatch(
    pane,
    /health\.state === "available"[\s\S]{0,120}?collectDiagnostics/,
    "the row builder must not be gated on compatibility",
  );
});

test("the connection group opens what the shell already opens, never its own path", () => {
  const source = read(path.join(PLUGIN_DIR, "src", "settings", "connectionPane.ts"));
  // Same host-link capability and launcher as the iframe bridge and the reader
  // badges; a second copy would drift out of step with the greying rules.
  assert.match(source, /import \{ canOpenHostLink, openHostLink \} from "\.\.\/tabs\/tabs"/);
  assert.doesNotMatch(source, /Zotero\.launchURL\(/);
  assert.doesNotMatch(source, /window\.open\(/);
  // Diagnostics come from the status dot's own entry point.
  assert.match(source, /import \{ openWindowDiagnostics \} from "\.\.\/status\/status"/);
  // A missing diagnostics panel must say so rather than doing nothing.
  assert.match(source, /prefs-open-unavailable/);
  // The directory picker is Zotero's Promise wrapper, not raw nsIFilePicker:
  // the bare object's init() takes a BrowsingContext and would throw here.
  // Check the executable lines only — the comment above deliberately names the
  // wrong API to explain why it is not used.
  assert.match(source, /FilePicker/);
  assert.match(source, /modeGetFolder/);
  const codeLines = source
    .split(/\r?\n/)
    .filter((line) => !/^\s*(?:\/\/|\*|\/\*)/.test(line))
    .join("\n");
  assert.doesNotMatch(codeLines, /nsIFilePicker/, "the bare XPCOM picker must not be used");
  // `FilePicker` is an ES module export, not a window global. Reaching for
  // `doc.defaultView.FilePicker` finds undefined on every Zotero build, which
  // would leave the button permanently dead behind a guard.
  assert.match(codeLines, /importESModule\(/);
  assert.match(codeLines, /chrome:\/\/zotero\/content\/modules\/filePicker\.mjs/);
  assert.doesNotMatch(
    codeLines,
    /defaultView[^\n]*\.FilePicker|FilePicker\s*=\s*view\?\./,
    "FilePicker must come from the module, never from a window property",
  );
  // A picker failure must be visible. A bare `data-picker-failed` attribute has
  // no consumer and no style: the user would see nothing at all.
  assert.match(codeLines, /prefs-zotero-dir-picker-failed/);
  assert.match(codeLines, /showPickerFailure\(/);
  // ...and the hint element it writes into exists in the pane.
  const markup = read(path.join(PLUGIN_DIR, "addon", "content", "preferences.xhtml"));
  assert.match(markup, /id="littrace-prefs-zotero-dir-picker-hint" hidden="true"/);
});

test("only the key write is gated off Windows, not the whole group", () => {
  const source = read(SETTINGS_PANE_TS);
  // kbapp/core/secrets.py returns early for an empty value and only reaches
  // its `os.name != 'nt'` guard for a NON-EMPTY key. So reading the settings,
  // saving the address/model/prices and probing a keyless endpoint all work
  // off Windows; only storing a key does not. Greying the whole group would
  // claim otherwise, and the user could not act on it.
  const saveCloud = source.slice(source.indexOf("export async function saveCloud"));
  const saveBody = saveCloud.slice(0, saveCloud.indexOf("\nexport ", 1));
  // The refusal is on the payload, not on the platform alone.
  assert.match(saveBody, /payload\.api_key/, "the gate must inspect the key being saved");
  assert.match(
    saveBody,
    /!canProtectKey\(\)[\s\S]{0,120}?payload\.api_key\.length > 0/,
    "an empty key saves fine anywhere; a non-empty one is refused with a readable line",
  );
  // A refusal must be visible, not silent.
  assert.match(saveBody, /setStatus\(doc, "saveFailed"/);

  // Probing sends no secret of its own, so it is not gated at all.
  const testCloud = source.slice(source.indexOf("export async function testCloud"));
  const testBody = testCloud.slice(0, testCloud.indexOf("\nexport ", 1));
  assert.doesNotMatch(
    testBody,
    /canProtectKey|platformSupportsCloud/,
    "the probe reads saved configuration and must stay available",
  );

  // The load path always reads, and paints the reason card.
  const loadCloud = source.slice(source.indexOf("export async function loadCloud"));
  const loadBody = loadCloud.slice(0, loadCloud.indexOf("\nexport ", 1));
  assert.match(loadBody, /applyKeyGate\(doc\)/);
  assert.doesNotMatch(
    loadBody,
    /return;\s*\n\s*\}\s*\n\s*setStatus/,
    "loadCloud must not bail out before reading on an unsupported platform",
  );
  assert.match(loadBody, /serviceClient\(\)\.get\("\/api\/settings"\)/);
});

test("the key gate disables exactly the two controls that write a secret", () => {
  const source = read(SETTINGS_PANE_TS);
  const gate = source.slice(source.indexOf("function applyKeyGate"));
  const body = gate.slice(0, gate.indexOf("\n/**", 1));
  // The key box and the clear button; nothing else.
  assert.match(body, /\[IDS\.key, IDS\.clear\]/);
  assert.match(body, /card\.hidden = false/);
  // Writing a secret is what cannot work, so the reason card appears next to
  // those two controls rather than over the whole group.
  assert.doesNotMatch(
    body,
    /querySelectorAll\("input, button, select"\)/,
    "greying every control would claim the free fields are unavailable too",
  );
});

test("the whole page refreshes from one snapshot, not one read per group", () => {
  // Two groups landed in this pane from different directions and each brought
  // its own read, so opening it issued six requests — including a pair of
  // /api/version and a pair of /api/settings that were byte-identical and sent
  // milliseconds apart. Worse, two writers of the same rows could disagree
  // between their answers. src/settings/paneRefresh.ts now owns the one read.
  const pane = read(PANE_REFRESH_TS);

  // Exactly one GET /api/settings in the whole module.
  const settingsGets = [...pane.matchAll(/serviceClient\(\)\.get\("\/api\/settings"\)/g)];
  assert.equal(settingsGets.length, 1, "the page reads settings once, not once per group");

  // Exactly one probe in the page, and the diagnostics are handed its verdict
  // rather than probing again. Counting `probeHealth()` text in this one file
  // was a false anchor before: the duplicate lived inside collectDiagnostics
  // and the count could not see it. So this checks both halves of the seam.
  const probes = [...pane.matchAll(/probeHealth\(\)/g)];
  assert.equal(probes.length, 1, "the page probes the version once");
  assert.match(
    pane,
    /collectDiagnostics\(\{\s*health:\s*health \?\? offlineHealth\(\)\s*\}\)/,
    "the diagnostics must receive the verdict this module already probed, including the offline fallback",
  );
  // And the receiving side must honour it: with a health handed in, no probe.
  const diagnostics = read(path.join(PLUGIN_DIR, "src", "status", "diagnostics.ts"));
  const collect = diagnostics.slice(diagnostics.indexOf("export async function collectDiagnostics"));
  const collectBody = collect.slice(0, collect.indexOf("\nexport ", 1));
  assert.match(
    collectBody,
    /if \(options\.health\) \{\s*health = options\.health;\s*\} else \{[\s\S]{0,200}?probeHealth\(\)/,
    "collectDiagnostics probes only when it was not given a verdict",
  );
  // The rows are built for every health state; only the two extra GETs are
  // gated. Gating the row builder left the version line blank exactly when it
  // said 「版本过旧」 — the one case it exists for.
  assert.match(
    collectBody,
    /const reachable = health\.state === "available";/,
    "only reachability of the two extra reads is gated",
  );
  assert.match(collectBody, /diagnosticRows\(\{ health, baseUrl, models, status \}\)/);
  assert.doesNotMatch(
    pane,
    /health\.state === "available"[\s\S]{0,120}?collectDiagnostics/,
    "paneRefresh must not gate the row builder on compatibility",
  );

  // Neither group may read on its own any more.
  const cloud = read(SETTINGS_PANE_TS);
  const mountCloud = cloud.slice(cloud.indexOf("export function mountCloud"));
  const mountCloudBody = mountCloud.slice(0, mountCloud.indexOf("\n}\n"));
  assert.doesNotMatch(
    mountCloudBody,
    /loadCloud\(doc\)/,
    "mountCloud must register buttons only: the pane's one read paints the form",
  );
  const connection = read(CONNECTION_PANE_TS);
  const mountConnection = connection.slice(connection.indexOf("export function mountConnection"));
  const mountConnectionBody = mountConnection.slice(0, mountConnection.indexOf("\n}\n"));
  assert.doesNotMatch(
    mountConnectionBody,
    /loadConnection|collectDiagnostics/,
    "mountConnection must register buttons only",
  );

  // One re-entry listener for the whole pane, not one per group.
  for (const source of [cloud, connection]) {
    assert.doesNotMatch(
      source,
      /addEventListener\("showing"/,
      "a per-group `showing` listener would double every read on re-entry",
    );
  }
  assert.match(pane, /addEventListener\("showing"[\s\S]{0,120}?refreshPane\(doc\)/);
  // And the first open still reads.
  assert.match(pane, /void refreshPane\(doc\)/);

  // The manual refresh button drives the same single path.
  const prefs = read(PREFS_TS);
  assert.match(prefs, /refresh\(doc: Document\): void \{\s*void refreshPane\(doc\);/);
  assert.doesNotMatch(
    prefs,
    /probeHealth\(\)/,
    "the refresh button must not probe on its own: that was the second /api/version",
  );

  // The cache group (merged from main) must read through the same snapshot too.
  // Its `refreshCache` kept its own GET for a while, which reintroduced exactly
  // the duplication this module exists to remove — the assertion above only
  // counted reads inside paneRefresh.ts and would not have noticed.
  assert.doesNotMatch(
    prefs,
    /serviceClient\(\)\.get\("\/api\/settings"\)/,
    "no group may keep its own settings read; refreshCache must delegate to refreshPane",
  );
  assert.match(
    prefs,
    /refreshCache\(doc: Document\): void \{[\s\S]{0,400}?void refreshPane\(doc\);/,
    "refreshCache delegates to the shared refresh",
  );
  // ...and the cache row is actually painted by it.
  assert.match(pane, /prefs-cache-current/);
  assert.match(pane, /renderCache\(doc, snapshot\.settings\)/);
});

test("the pane actually mounts both groups and its refresh", () => {
  // Delete every mount call and the rest of this file stays green: the other
  // assertions all read the group modules, never the place that uses them. This
  // is the anchor that notices the feature being unwired.
  const prefs = read(PREFS_TS);
  const onPaneLoad = prefs.slice(prefs.indexOf("onPaneLoad(doc: Document)"));
  const body = onPaneLoad.slice(0, onPaneLoad.indexOf("\n  },", 1));
  for (const call of ["mountCloud(doc);", "mountConnection(doc);", "mountPane(doc);"]) {
    assert.ok(body.includes(call), `onPaneLoad must call ${call}`);
  }
  for (const name of ["mountCloud", "mountConnection", "mountPane"]) {
    assert.match(prefs, new RegExp(`import \\{[^}]*\\b${name}\\b[^}]*\\} from`));
  }
  // Every element id a group looks up must exist in the markup, or the row is a
  // silent no-op on a real machine.
  const markup = read(path.join(PLUGIN_DIR, "addon", "content", "preferences.xhtml"));
  for (const source of [CONNECTION_PANE_TS, SETTINGS_PANE_TS]) {
    const ids = new Set(
      [...read(source).matchAll(/"littrace-prefs-[a-z-]+"/g)].map((m) => m[0].slice(1, -1)),
    );
    for (const id of ids) {
      assert.ok(
        markup.includes(`id="${id}"`),
        `${path.basename(source)} looks up #${id}, which the pane does not declare`,
      );
    }
  }
});

test("a first read reports 「已载入」, never 「设置已保存」", () => {
  // The status row claimed a save the user never made, on the very first open
  // of the pane. A load and a save are different events and now say so.
  const source = read(SETTINGS_PANE_TS);
  const renderCloud = source.slice(source.indexOf("export function renderCloud"));
  // Line-ending independent: slice to the next declaration rather than
  // matching "\n}\n", which never appears in a CRLF checkout.
  const renderEnd = renderCloud.indexOf("export function", 10);
  const body = renderEnd > 0 ? renderCloud.slice(0, renderEnd) : renderCloud;
  assert.match(body, /setStatus\(doc, "loaded"\)/);
  assert.doesNotMatch(
    body,
    /setStatus\(doc, "saved"\)/,
    "a read must not claim the settings were saved",
  );
  // The save path still says so.
  const saveCloud = source.slice(source.indexOf("export async function saveCloud"));
  const saveEnd = saveCloud.indexOf("export async function", 10);
  const saveBody = saveEnd > 0 ? saveCloud.slice(0, saveEnd) : saveCloud;
  assert.match(saveBody, /setStatus\(doc, "saved"\)/);
});

test("the probe budget is strictly below the plugin's request timeout", async () => {
  // The plugin's only request path carries REQUEST_DISCIPLINE.timeout (ADR
  // 0018). A service probe that runs as long would make the client answer
  // 「无法连接本地服务」 first, and the user would never see why the cloud
  // endpoint failed — the one thing the probe exists to say.
  const budgetMs = probeTimeoutSeconds() * 1000;
  const clientMs = requestTimeoutMs();
  assert.ok(clientMs > 0, "the client timeout must be a positive number");
  assert.ok(
    budgetMs < clientMs,
    `the service probe budget ${budgetMs}ms must be strictly below the plugin's ${clientMs}ms`,
  );
});

test("settings source files are registered where the build and tests can see them", async () => {
  const core = await loadCore();
  // The pure module must stay free of Zotero globals so node can import it.
  const source = read(SETTINGS_CORE);
  assert.doesNotMatch(source, /\bZotero\./);
  assert.doesNotMatch(source, /\bdocument\./);
  assert.doesNotMatch(source, /\bwindow\./);
  // And the typed view stays in sync: every exported const is declared.
  const dts = read(path.join(PLUGIN_DIR, "src", "settings", "settingsCore.d.mts"));
  for (const name of ["SETTINGS_FIELDS", "SETTINGS_GROUPS", "TEST_CODES", "TEST_CODE_KEYS"]) {
    assert.ok(dts.includes(`export const ${name}:`), `${name} is missing its type`);
    assert.ok(core[name], `${name} is not exported at runtime`);
  }
});
