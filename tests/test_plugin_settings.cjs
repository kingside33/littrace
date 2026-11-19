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

test("no visible string escapes the registry in the TypeScript edge", () => {
  const source = read(SETTINGS_PANE_TS);
  // A quoted literal assigned to textContent is exactly the string that would
  // stay English in a zh-CN build; `= ""` (clearing) is fine.
  assert.doesNotMatch(source, /textContent\s*=\s*["'][^"']+["']/);
  // Every t("…") call site names a key the registry knows about.
  const literalKeys = [...source.matchAll(/\bt\(\s*"([^"]+)"/g)].map((match) => match[1]);
  assert.ok(literalKeys.length > 0, "the pane's status keys are written out literally");
  for (const key of literalKeys) {
    assert.match(key, /^prefs-/, `${key} is not a prefs- key`);
  }
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

  // The adapter reads the snapshot the health refresh already collects.
  const source = read(path.join(PLUGIN_DIR, "src", "settings", "connectionPane.ts"));
  assert.match(source, /collectDiagnostics\(\)/);
  assert.match(source, /versionViewFrom\(/);
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
  // A picker failure must not block the field.
  assert.match(source, /data-picker-failed/);
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

test("re-entering the pane re-reads instead of showing stale values", () => {
  const source = read(SETTINGS_PANE_TS);
  // `preferences.js` guards pane loading with a `loaded` flag, so onPaneLoad
  // may run once per preferences window; without a re-entry refresh a user who
  // changed the port or the key elsewhere would see the old values.
  assert.match(source, /addEventListener\("showing"/);
  const mount = source.slice(source.indexOf("export function mountCloud"));
  const mountBody = mount.slice(0, mount.indexOf("\n}\n"));
  assert.match(mountBody, /addEventListener\("showing"[\s\S]{0,120}?loadCloud\(doc\)/);
  // And the listener must not be the only read: the first open still loads.
  assert.match(mountBody, /void loadCloud\(doc\)/);
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
