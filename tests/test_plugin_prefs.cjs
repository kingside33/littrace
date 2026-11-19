"use strict";

// Anchors the settings page contract (U5: 设置页、Fluent 文案与服务状态点).
//
// Three artefacts have to agree, and each pair used to be a silent drift:
//   - plugin/src/prefs/prefsCore.mjs  the table (key, type, default, group,
//     Fluent label, whether the plugin actually reads it);
//   - plugin/addon/prefs.js           the declared defaults;
//   - plugin/addon/content/preferences.xhtml  the pane markup and its
//     declarative `preference="<full key>"` bindings.
//
// The Fluent namespace contract is data too: keys are bare in the .ftl files,
// the build adds `littrace-`, and ftlId() is the one place that knows it.
// Node built-ins only, no npm install, no Zotero process.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const ROOT = path.join(__dirname, "..");
const PLUGIN_DIR = path.join(ROOT, "plugin");
const PREFS_CORE = path.join(PLUGIN_DIR, "src", "prefs", "prefsCore.mjs");
const PREFS_TS = path.join(PLUGIN_DIR, "src", "prefs", "prefs.ts");
const SERVICE_ORIGIN_TS = path.join(PLUGIN_DIR, "src", "prefs", "serviceOrigin.ts");
const PREFS_JS = path.join(PLUGIN_DIR, "addon", "prefs.js");
const PANE_XHTML = path.join(PLUGIN_DIR, "addon", "content", "preferences.xhtml");
const REGISTRY_TS = path.join(PLUGIN_DIR, "src", "registry.ts");
const SERVICE_CLIENT_TS = path.join(PLUGIN_DIR, "src", "service-client.ts");
const TABS_TS = path.join(PLUGIN_DIR, "src", "tabs", "tabs.ts");
const SUPPORT_MATRIX = path.join(ROOT, "docs", "support-matrix.json");

async function loadCore() {
  return import(pathToFileURL(PREFS_CORE).href);
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

/** Parse addon/prefs.js into name → declared value. */
function parsePrefsJs() {
  const entries = new Map();
  for (const match of read(PREFS_JS).matchAll(/^pref\("([^"]+)",\s*(.+?)\);\s*$/gm)) {
    const raw = match[2].trim();
    let value;
    if (raw === "true" || raw === "false") {
      value = raw === "true";
    } else if (/^".*"$/.test(raw)) {
      value = raw.slice(1, -1);
    } else {
      throw new Error(`unexpected pref literal in addon/prefs.js: ${raw}`);
    }
    entries.set(match[1], value);
  }
  return entries;
}

test("the table gives every feature its own pref and keeps Pro as placeholders", async () => {
  const core = await loadCore();
  assert.ok(Object.isFrozen(core.PREF_ENTRIES));
  const keys = core.PREF_ENTRIES.map((entry) => entry.key);
  assert.equal(new Set(keys).size, keys.length, "duplicate pref key");
  assert.deepEqual(core.PREF_GROUPS.map((group) => group.id), [
    "connection",
    "cloud",
    "integration",
    "cache",
    "pro",
  ]);
  // A group's `kind` says where its values live, and it must be checkable:
  // a `server` group has no PREF_ENTRIES rows (its fields are in
  // settingsCore's SETTINGS_FIELDS), and every `prefs` group has some.
  for (const group of core.PREF_GROUPS) {
    assert.ok(
      group.kind === "prefs" || group.kind === "server",
      `${group.id} has an unknown kind ${group.kind}`,
    );
  }
  const serverGroups = core.PREF_GROUPS.filter((group) => group.kind === "server");
  assert.deepEqual(serverGroups.map((group) => group.id), ["cloud"]);
  for (const group of serverGroups) {
    assert.equal(
      core.entriesForGroup(group.id).length,
      0,
      `${group.id} is a server group and must not carry Zotero pref rows`,
    );
  }
  for (const group of core.PREF_GROUPS) {
    if (group.kind === "prefs") {
      assert.ok(
        core.entriesForGroup(group.id).length > 0,
        `${group.id} is a prefs group and needs at least one row`,
      );
    }
  }

  const pro = core.entriesForGroup("pro");
  assert.ok(pro.length >= 2, "Pro group carries one pref per planned feature");
  for (const entry of pro) {
    assert.equal(entry.requiresPro, true, `${entry.key} must be gated`);
    assert.equal(entry.default, false, `${entry.key} must default to off`);
    assert.ok(entry.ui, `${entry.key} must render as a placeholder control`);
  }

  // Each non-Pro row is a real, readable feature switch with a Fluent label;
  // a row without a label would render as an empty setting.
  for (const entry of core.PREF_ENTRIES) {
    if (entry.group === "core") {
      assert.equal(entry.ui, null, "the lifecycle pref is not a pane row");
      continue;
    }
    assert.ok(entry.ui, `${entry.key} needs a UI mode`);
    assert.match(entry.labelKey, /^prefs-/);
    assert.match(entry.descriptionKey, /^prefs-/);
    assert.ok(entry.readBy.length > 0, `${entry.key} must name its reader`);
  }
  // No shared "advanced" catch-all switch: the Pro pattern is one per feature.
  assert.equal(core.PREF_ENTRIES.some((entry) => entry.key.includes("advanced")), false);
});

test("the pref and Fluent namespaces match the build configuration", async () => {
  const core = await loadCore();
  const pkg = JSON.parse(read(path.join(PLUGIN_DIR, "package.json")));
  assert.equal(core.PREF_PREFIX, pkg.config.prefsPrefix);
  assert.equal(core.FTL_NAMESPACE, pkg.config.addonRef);
  assert.equal(core.ftlId("status-dot-online"), `${pkg.config.addonRef}-status-dot-online`);
  assert.equal(core.ftlId(`${pkg.config.addonRef}-x`), `${pkg.config.addonRef}-x`);
  assert.equal(core.prefKey("connection.port"), `${pkg.config.prefsPrefix}.connection.port`);
  assert.throws(() => core.ftlId(""), /non-empty/);
});

test("addon/prefs.js declares exactly the table, with the table's defaults", async () => {
  const core = await loadCore();
  const declared = parsePrefsJs();
  const table = new Map(core.PREF_ENTRIES.map((entry) => [entry.key, entry]));
  assert.deepEqual([...declared.keys()].sort(), [...table.keys()].sort());
  for (const [key, value] of declared) {
    const entry = table.get(key);
    assert.equal(
      typeof value,
      entry.type === "bool" ? "boolean" : "string",
      `${key} has the wrong declared type`,
    );
    assert.equal(value, entry.default, `${key} default drifted from the table`);
  }
});

test("the pane binds exactly the visible table rows", async () => {
  const core = await loadCore();
  const markup = read(PANE_XHTML);
  const bound = [...markup.matchAll(/preference="([^"]+)"/g)].map((match) => match[1]);
  const expected = core.PREF_ENTRIES.filter((entry) => entry.ui !== null).map((entry) =>
    core.prefKey(entry.key),
  );
  assert.deepEqual(bound.sort(), expected.sort());
  for (const key of bound) {
    assert.ok(key.startsWith(`${core.PREF_PREFIX}.`), `${key} is not a full pref key`);
  }
  // The Pro controls are placeholders: present, bound, and disabled so the
  // free plugin never pretends a Pro switch is available.
  for (const entry of core.entriesForGroup("pro")) {
    const id = [...markup.matchAll(/id="([^"]+)"[^>]*preference="([^"]+)"/g)]
      .find((match) => match[2] === core.prefKey(entry.key));
    assert.ok(id, `missing control for ${entry.key}`);
    const block = markup.slice(markup.indexOf(id[0]));
    const end = block.indexOf("/>");
    assert.match(
      block.slice(0, end),
      /disabled="true"/,
      `${entry.key} must be disabled until the commercial layer ships`,
    );
  }
  // The M1 Pro status is 「商业层未运行」, never 「未授权」.
  assert.match(markup, /data-l10n-id="littrace-prefs-pro-not-running"/);
  // A platform card exists and starts hidden.
  assert.match(markup, /id="littrace-prefs-platform" hidden="true"/);
});

test("the pane draws the groups in the table's order, and only those", async () => {
  const core = await loadCore();
  const markup = read(PANE_XHTML);
  // The table does not order the pane — the markup does, by the order of its
  // h2 headings. Without this comparison a group added to PREF_GROUPS but
  // forgotten in the markup would be invisible to every other test, and one
  // drawn in the wrong place would only be caught by a human on a real machine.
  const drawn = [...markup.matchAll(/data-l10n-id="littrace-(prefs-group-[a-z-]+)"/g)]
    // The description line reuses the same prefix with a `-desc` suffix.
    .map((match) => match[1])
    .filter((key) => !key.endsWith("-desc"));
  assert.deepEqual(drawn, core.PREF_GROUPS.map((group) => group.titleKey));
  // Every group the table names is also drawn exactly once.
  assert.equal(new Set(drawn).size, drawn.length, "a group heading is drawn twice");
});

test("the cloud group is server-side: no pref bindings, its own platform card", async () => {
  const core = await loadCore();
  const markup = read(PANE_XHTML);
  const start = markup.indexOf('id="littrace-prefs-cloud"');
  assert.ok(start > 0, "the cloud group is missing from the pane");
  const end = markup.indexOf("</groupbox>", start);
  const group = markup.slice(start, end > start ? end : markup.length);
  // A declarative binding here would mean the value lives in Zotero prefs,
  // which it does not.
  assert.equal(
    group.includes("preference="),
    false,
    "the cloud group must not carry preference= bindings: its values live in the service's settings.json",
  );
  // The controls the wiring looks up by id must all be present.
  for (const id of [
    "littrace-prefs-cloud-base",
    "littrace-prefs-cloud-model",
    "littrace-prefs-cloud-key",
    "littrace-prefs-cloud-clear",
    "littrace-prefs-cloud-proxy",
    "littrace-prefs-cloud-budget-mode",
    "littrace-prefs-cloud-daily-budget",
    "littrace-prefs-cloud-input-price",
    "littrace-prefs-cloud-output-price",
    "littrace-prefs-cloud-enabled",
    "littrace-prefs-cloud-save",
    "littrace-prefs-cloud-test",
    "littrace-prefs-cloud-status",
  ]) {
    assert.ok(group.includes(`id="${id}"`), `missing control ${id}`);
  }
  // The reason card is present and starts hidden; loadCloud reveals it on a
  // platform where DPAPI cannot protect a key.
  assert.match(group, /id="littrace-prefs-cloud-platform" hidden="true"/);
  // The key box must never be a plain text field.
  assert.match(
    group,
    /id="littrace-prefs-cloud-key"[^>]*type="password"[\s\S]{0,80}?|type="password"[\s\S]{0,120}?id="littrace-prefs-cloud-key"/,
    "the API key input must be type=password",
  );
  // The two budget mode option values are exactly the strings
  // store.save_settings accepts.
  const settingsCore = await import(
    pathToFileURL(path.join(PLUGIN_DIR, "src", "settings", "settingsCore.mjs")).href
  );
  const options = [...group.matchAll(/<html:option value="([a-z]+)"/g)].map((match) => match[1]);
  assert.deepEqual(options, [...settingsCore.BUDGET_MODES]);
});

/**
 * Everything outside comments and outside tags, in one left-to-right pass.
 *
 * Deliberately not `markup.replace(/<!--...-->/g, "").replace(/<[^>]*>/g, "")`:
 * a chain of multi-character replacements is the sanitizer shape that can
 * produce a new marker for a later rule, and it cannot state the property
 * this test needs ("no text node anywhere in the pane"). One scanner with one
 * state transition says exactly what is being measured.
 */
function textOutsideTags(markup) {
  const chunks = [];
  let index = 0;
  while (index < markup.length) {
    const open = markup.indexOf("<", index);
    if (open === -1) {
      chunks.push(markup.slice(index));
      break;
    }
    chunks.push(markup.slice(index, open));
    if (markup.startsWith("<!--", open)) {
      const end = markup.indexOf("-->", open + 4);
      index = end === -1 ? markup.length : end + 3;
    } else {
      const end = markup.indexOf(">", open + 1);
      index = end === -1 ? markup.length : end + 1;
    }
  }
  return chunks.join("");
}

test("the pane markup contains no hardcoded UI text", async () => {
  const markup = read(PANE_XHTML);
  // Every visible string must come from Fluent, so the pane itself has no text.
  const text = textOutsideTags(markup);
  assert.equal(text.trim(), "", `unexpected literal text in the pane: ${text.trim()}`);
  // Every visible string is a Fluent reference in the plugin namespace, and
  // each one resolves in both bundles.
  const ids = [...markup.matchAll(/data-l10n-id="([^"]+)"/g)].map((match) => match[1]);
  assert.ok(ids.length >= 20, "the pane declares its strings as Fluent ids");
  const bundles = { "en-US": parseFtl("en-US"), "zh-CN": parseFtl("zh-CN") };
  for (const id of ids) {
    assert.match(id, /^littrace-/, `data-l10n-id must be namespaced: ${id}`);
    const key = id.slice("littrace-".length);
    for (const [locale, ftl] of Object.entries(bundles)) {
      assert.ok(ftl.has(key), `${locale} is missing ${key} (for ${id})`);
    }
  }
  // The pane loads the plugin's own Fluent bundle, not a second file.
  assert.match(markup, /<html:link rel="localization" href="littrace-addon\.ftl" \/>/);
});

test("the Fluent prefix contract is one rule, shared with the existing lookup", async () => {
  const core = await loadCore();
  const reader = await import(
    pathToFileURL(path.join(PLUGIN_DIR, "src", "readerPanelCore.mjs")).href
  );
  // ftlId() and the plugin's long-standing messageId() must agree, otherwise
  // the pane and the rest of the plugin would address different bundles.
  assert.equal(core.ftlId("section-evidence"), reader.messageId("section-evidence", core.FTL_NAMESPACE));
  assert.equal(core.ftlId("littrace-section-evidence"), "littrace-section-evidence");
  assert.throws(() => core.ftlId(""), /non-empty/);
  assert.throws(() => core.prefKey(""), /non-empty/);

  const en = parseFtl("en-US");
  const zh = parseFtl("zh-CN");
  assert.deepEqual([...en.keys()].sort(), [...zh.keys()].sort(), "both bundles carry the same keys");
  for (const [key, value] of en) {
    assert.ok(value.trim().length > 0, `empty value for ${key}`);
    // The prefix belongs to the build; a key already carrying it would become
    // `littrace-littrace-...` at runtime.
    assert.equal(key.startsWith("littrace-"), false, `${key} must stay bare in the .ftl`);
  }
  // The keys this feature adds stick to plain Fluent identifiers. Older
  // namespaces (e.g. `reading-class-NATIVE_TEXT`) are not renamed here: this
  // test anchors U5's contract, not a repository-wide rename.
  for (const key of en.keys()) {
    if (!/^(prefs|status)-/.test(key)) {
      continue;
    }
    assert.match(key, /^[a-z0-9]+(?:-[a-z0-9]+)*$/, `not a Fluent identifier: ${key}`);
  }
  // Every key the table and the static pane strings reference exists.
  const referenced = new Set([...core.prefFluentKeys(), ...core.PANE_FLUENT_KEYS]);
  for (const key of referenced) {
    assert.match(key, /^prefs-/, `unexpected prefs key ${key}`);
    assert.ok(en.has(key), `en-US is missing ${key}`);
    assert.ok(zh.has(key), `zh-CN is missing ${key}`);
  }
});

test("the port pref is validated, not trusted", async () => {
  const core = await loadCore();
  assert.equal(core.normalizePort("8765"), 8765);
  assert.equal(core.normalizePort(" 9000 "), 9000);
  assert.equal(core.normalizePort(8766), 8766);
  // Anything that is not an integer in range falls back to the service default.
  for (const invalid of ["", "abc", "0", "65536", "-1", "12.5", null, undefined, NaN, {}]) {
    assert.equal(
      core.normalizePort(invalid),
      core.DEFAULT_SERVICE_PORT,
      `unexpected port for ${JSON.stringify(invalid)}`,
    );
  }
  assert.equal(core.normalizePort("abc", 9000), 9000);
  // The origin is loopback-only: the host is not a preference.
  assert.equal(core.serviceBaseUrl("9000"), "http://127.0.0.1:9000");
  assert.equal(core.serviceBaseUrl("nope"), `http://127.0.0.1:${core.DEFAULT_SERVICE_PORT}`);
  assert.equal(core.SERVICE_HOST, "127.0.0.1");

  // The adapter reads the table entry, validates, and builds the same origin.
  const source = read(SERVICE_ORIGIN_TS);
  assert.match(source, /prefEntry\("connection\.port"\)/);
  assert.match(source, /Zotero\.Prefs\.get\(prefKey\(entry\.key\), true\)/);
  assert.match(source, /normalizePort\(raw \?\? entry\.default\)/);
  assert.match(source, /serviceBaseUrl\(servicePort\(\)\)/);
});

test("a port change rebuilds the shared client instead of probing the old port", async () => {
  const core = await loadCore();
  // PR #36 review finding (service-client.ts:107): the shared client pinned
  // its baseUrl at construction and lived until plugin shutdown, so every
  // caller except the iframe tabs kept probing the port it was born with,
  // while the diagnostic popup displayed the newly configured one.
  assert.deepEqual({ ...core.CLIENT_ORIGINS }, { REUSE: "reuse", REBUILD: "rebuild" });
  const same = "http://127.0.0.1:8765";
  assert.equal(core.clientOriginVerdict(same, same), "reuse");
  assert.equal(
    core.clientOriginVerdict(same, core.serviceBaseUrl("9000")),
    "rebuild",
    "a different configured port must not reuse the cached client",
  );
  // A trailing slash is the same origin for these purposes only if it is
  // normalised upstream; serviceBaseUrl never emits one, so a stray slash is
  // treated as a difference and rebuilds (cheap, never wrong).
  assert.equal(core.clientOriginVerdict(`${same}/`, same), "rebuild");
  // No usable origin on either side: rebuilding is the only branch that can
  // still reach a service.
  for (const [current, desired] of [
    [null, same],
    [same, null],
    ["", same],
    [same, ""],
    [undefined, undefined],
  ]) {
    assert.equal(core.clientOriginVerdict(current, desired), "rebuild");
  }

  // The adapter must consult the verdict and drop the cached token with the
  // client it discards (a new port is a new service process, new token).
  const source = read(SERVICE_CLIENT_TS);
  assert.match(source, /const desired = serviceBaseUrlFromPrefs\(\);/);
  assert.match(
    source,
    /clientOriginVerdict\(shared\.baseUrl, desired\) === CLIENT_ORIGINS\.REBUILD/,
    "serviceClient() must compare the configured origin against the live client's",
  );
  assert.match(
    source,
    /if \(shared\) \{[\s\S]*?resetServiceClient\(\);[\s\S]*?\}\s*shared = createClient\(\{ baseUrl: desired \}\);/,
    "rebuilding must drop the previous client (and its token) before creating the next",
  );
});

test("the Pro group never claims 未授权 without an answer", async () => {
  const core = await loadCore();
  // M1 has no commercial layer running: null is 「商业层未运行」.
  assert.deepEqual(core.licenseView(null), {
    state: "not-running",
    labelKey: "prefs-pro-not-running",
  });
  assert.deepEqual(core.licenseView(undefined), {
    state: "not-running",
    labelKey: "prefs-pro-not-running",
  });
  assert.equal(core.licenseView({ authorized: true }).state, "authorized");
  assert.equal(core.licenseView({ authorized: false }).state, "unauthorized");
  assert.equal(core.licenseView({}).state, "unknown");
  assert.equal(core.licenseView("yes").state, "unknown");
  // Every state has a key in both bundles (checked through the table test too).
  const en = parseFtl("en-US");
  for (const labelKey of Object.values(core.PRO_KEYS)) {
    assert.ok(en.has(labelKey), `missing ${labelKey}`);
  }
});

test("the platform card follows the repository support matrix", async () => {
  const core = await loadCore();
  const matrix = JSON.parse(read(SUPPORT_MATRIX));
  const primary = matrix.platforms.filter((platform) => platform.status === "primary");
  assert.equal(primary.length, 1, "exactly one primary platform");
  assert.match(primary[0].id, /^windows/, "M1 is Windows x64 only");
  assert.deepEqual([...core.SUPPORTED_PLATFORMS], ["win"]);

  assert.equal(core.platformCard("win").supported, true);
  for (const other of ["mac", "linux", "other"]) {
    const card = core.platformCard(other);
    assert.equal(card.supported, false, `${other} must not claim support`);
    assert.match(card.titleKey, /^prefs-platform-unsupported/);
    assert.ok(parseFtl("zh-CN").has(card.descriptionKey));
  }
  // The pane's card is driven by Zotero's own platform flags.
  const source = read(PREFS_TS);
  assert.match(source, /Zotero\.isWin/);
  assert.match(source, /card\.hidden = platformName\(\) === "win"/);
});

test("the settings page is registered through the plugin's single entry", async () => {
  const core = await loadCore();
  const registry = read(REGISTRY_TS);
  assert.match(
    registry,
    /import \{ prefBool, registerPreferencePane \} from "\.\/prefs\/prefs";/,
  );
  assert.match(registry, /registerGlobal\("preference-pane", registerPreferencePane\(\)\);/);

  const prefs = read(PREFS_TS);
  assert.match(prefs, /Zotero\.PreferencePanes/);
  assert.match(prefs, /src: "content\/preferences\.xhtml"/);
  assert.match(prefs, /pluginID: config\.addonID/);
  // The id is a named constant because the offline guide card (U6) navigates
  // to this pane with openPreferences(PANE_ID); both halves are asserted so
  // the constant cannot drift away from the id actually registered.
  assert.match(prefs, /export const PANE_ID = "littrace-preferences";/);
  assert.match(prefs, /id: PANE_ID,/);

  // The plugin's own pref-driven behaviours read the table, not a literal key.
  assert.match(prefs, /prefEntry\(key\)/);
  assert.match(prefs, /Zotero\.Prefs\.get\(prefKey\(entry\.key\), true\)/);
  assert.match(registry, /prefBool\("integration\.column\.lineage"\)/);
  assert.match(registry, /prefBool\("integration\.column\.reading"\)/);

  // The S4 client and the iframe tabs both take the origin from the pref.
  assert.match(read(SERVICE_CLIENT_TS), /baseUrl: options\.baseUrl \|\| serviceBaseUrlFromPrefs\(\)/);
  assert.match(read(TABS_TS), /baseUrl: serviceBaseUrlFromPrefs\(\)/);
  // The table's default port mirrors the service's own default.
  assert.equal(core.DEFAULT_SERVICE_PORT, 8765);
});

// ── Cache directory (issue #61) ─────────────────────────────────────────────
//
// The cache group is the first pref whose value travels to the service instead
// of staying in Zotero: the pane validates what it can (absolute or empty) and
// forwards the rest, because existence and writability are properties of the
// machine running the service.

test("the cache pref is declared once and wired to the server setting", async () => {
  const core = await loadCore();
  const entry = core.prefEntry("cache.dir");
  assert.ok(entry, "cache.dir must exist in the pref table");
  assert.equal(entry.group, "cache");
  assert.equal(entry.type, "string");
  assert.equal(entry.ui, "text");
  assert.equal(entry.default, "");
  assert.equal(entry.requiresPro, false);
  // The group must be declared, or the pane renders a section with no heading.
  assert.ok(
    core.PREF_GROUPS.some((group) => group.id === "cache"),
    "the cache group must be in PREF_GROUPS",
  );
  assert.equal(core.prefKey("cache.dir"), "extensions.zotero.littrace.cache.dir");
});

test("normalizeCacheDir accepts empty and absolute, refuses the rest", async () => {
  const { normalizeCacheDir } = await loadCore();
  const bs = String.fromCharCode(92);

  // Empty is the documented "use the server default" value, not an error.
  assert.deepEqual(normalizeCacheDir(""), { ok: true, value: "", reason: null });
  assert.deepEqual(normalizeCacheDir("   "), { ok: true, value: "", reason: null });
  assert.deepEqual(normalizeCacheDir(null), { ok: true, value: "", reason: null });

  // Absolute paths: forward and back slashes both normalize to forward, and a
  // trailing separator is stripped so repeated saves cannot create "x" and "x/".
  assert.deepEqual(normalizeCacheDir("/home/x/cache/"), { ok: true, value: "/home/x/cache", reason: null });
  assert.deepEqual(normalizeCacheDir("E:/cache/x/"), { ok: true, value: "E:/cache/x", reason: null });
  assert.deepEqual(normalizeCacheDir("E:" + bs + "cache" + bs + "x" + bs), { ok: true, value: "E:/cache/x", reason: null });
  // A bare drive root keeps its separator.
  assert.deepEqual(normalizeCacheDir("E:/"), { ok: true, value: "E:/", reason: null });

  // Relative paths would resolve against the *service's* cwd, not the user's,
  // so they are refused rather than guessed.
  for (const bad of ["rel/path", "~/cache", "C:", ".", ".."]) {
    const result = normalizeCacheDir(bad);
    assert.equal(result.ok, false, `${bad} must be refused`);
    assert.equal(result.reason, "cache-dir-not-absolute");
    assert.equal(result.value, "");
  }
});

test("formatBytes renders a human size and survives junk input", async () => {
  const { formatBytes } = await loadCore();
  assert.equal(formatBytes(0), "0 B");
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(5 * 1024 * 1024), "5.0 MB");
  // A missing/garbage size must not print NaN into the pane.
  assert.equal(formatBytes(null), "0 B");
  assert.equal(formatBytes("bad"), "0 B");
  assert.equal(formatBytes(-1), "0 B");
});

test("the pane markup has the cache field and the readout, and the .ftl files carry their keys", () => {
  const xhtml = read(PANE_XHTML);
  assert.match(
    xhtml,
    /preference="extensions\.zotero\.littrace\.cache\.dir"/,
    "the cache field must bind the full pref key declaratively",
  );
  assert.match(xhtml, /id="littrace-prefs-cache-dir"/);
  assert.match(xhtml, /id="littrace-prefs-cache-current"/);
  assert.match(xhtml, /id="littrace-prefs-cache-save"/);

  for (const locale of ["zh-CN", "en-US"]) {
    const ftl = read(path.join(PLUGIN_DIR, "addon", "locale", locale, "addon.ftl"));
    for (const key of [
      "prefs-group-cache",
      "prefs-group-cache-desc",
      "prefs-cache-dir",
      "prefs-cache-dir-desc",
      "prefs-cache-current",
      "prefs-cache-count",
      "prefs-cache-default",
      "prefs-cache-overridden",
      "prefs-cache-reuse",
      "prefs-cache-reuse-desc",
    ]) {
      assert.match(ftl, new RegExp(`^${key} =`, "m"), `${locale} is missing ${key}`);
    }
  }
});
