/**
 * The settings page's data (U5): pref key map, Fluent key namespace contract,
 * Pro placeholder state and the platform card.
 *
 * Deliberately plain ESM with no DOM and no Zotero globals, so
 * tests/test_plugin_prefs.cjs exercises the real table with `node --test`.
 * src/prefs/prefs.ts is the only adapter that touches Zotero.Prefs, and
 * addon/content/preferences.xhtml is the only place the table becomes markup.
 *
 * ── Why a table and not ad-hoc pref reads ───────────────────────────────
 *
 * Every feature gets its own pref, never a shared "advanced" switch. The
 * Pro column follows the same rule *before* any Pro feature exists, so the
 * M3 commercial layer inherits a shape instead of retrofitting one: adding a
 * Pro feature is one row here plus one checkbox in the pane, not a redesign.
 *
 * The table is also what the tests can hold against `addon/prefs.js` and the
 * pane markup: a pref that exists in only one of the three places is drift,
 * and drift is exactly the failure mode a UI-only feature would otherwise
 * hide until a real machine run.
 *
 * ── The Fluent namespace contract ───────────────────────────────────────
 *
 * Fluent resources share one namespace across all plugins
 * (`chrome/content/zotero/xpcom/plugins.js` rebuilds a single L10nFileSource
 * from every plugin's locale directories), so the plugin name is the
 * namespace: this addon's keys are bare in the `.ftl` files and the build
 * prefixes them with `littrace-`. The one place that knows the prefix is
 * `ftlId()` here and `messageId()` in src/readerPanelCore.mjs; the test pins
 * the two together. Every UI key in this module is declared as data so
 * "no hardcoded UI string" is checkable, not a review opinion.
 */

/** The build-time pref prefix. Must equal package.json's config.prefsPrefix. */
export const PREF_PREFIX = "extensions.zotero.littrace";

/** The Fluent namespace. Must equal package.json's config.addonRef. */
export const FTL_NAMESPACE = "littrace";

/** Separator the build inserts between the namespace and a key. */
export const FTL_SEPARATOR = "-";

/** `<namespace>-<key>`; already-prefixed keys are returned unchanged. */
export function ftlId(key) {
  const id = String(key ?? "").trim();
  if (!id) {
    throw new Error("littrace prefs: Fluent key must be a non-empty string");
  }
  const prefix = `${FTL_NAMESPACE}${FTL_SEPARATOR}`;
  return id.startsWith(prefix) ? id : `${prefix}${id}`;
}

/** The full preference key for a bare table key. */
export function prefKey(key) {
  const bare = String(key ?? "").trim();
  if (!bare) {
    throw new Error("littrace prefs: pref key must be a non-empty string");
  }
  return bare.startsWith(`${PREF_PREFIX}.`) ? bare : `${PREF_PREFIX}.${bare}`;
}

/**
 * The three groups the pane shows, in order. `core` is the lifecycle pref
 * (`enable`) and has no pane section: it belongs to the plugin, not to a user
 * decision about a feature.
 */
export const PREF_GROUPS = Object.freeze([
  Object.freeze({
    id: "connection",
    titleKey: "prefs-group-connection",
    descriptionKey: "prefs-group-connection-desc",
  }),
  Object.freeze({
    id: "integration",
    titleKey: "prefs-group-integration",
    descriptionKey: "prefs-group-integration-desc",
  }),
  Object.freeze({
    id: "pro",
    titleKey: "prefs-group-pro",
    descriptionKey: "prefs-group-pro-desc",
  }),
]);

/** Pane sidebar label; the pane's own heading reuses it. */
export const PANE_LABEL_KEY = "prefs-pane-label";

/**
 * One row per preference. Fields:
 *   key       bare key, joined with PREF_PREFIX by prefKey()
 *   group     one of PREF_GROUPS ids, or "core" for the lifecycle pref
 *   type      the JS type addon/prefs.js declares
 *   default   the value addon/prefs.js must declare
 *   ui        "text" | "checkbox" | null (null = not rendered in the pane)
 *   requiresPro  rendered disabled with 需 Pro and never read by the plugin
 *   labelKey / descriptionKey  Fluent keys (bare; ftlId() adds the namespace)
 *   readBy    a one-line pointer to the code that actually reads the pref
 */
export const PREF_ENTRIES = Object.freeze([
  Object.freeze({
    key: "enable",
    group: "core",
    type: "bool",
    default: true,
    ui: null,
    requiresPro: false,
    labelKey: null,
    descriptionKey: null,
    readBy: "addon/prefs.js (scaffold lifecycle default)",
  }),
  Object.freeze({
    // A string pref on purpose: Zotero's <preference> binding writes the
    // input's string value (preferences.js `_syncToPrefOnModify`), so an int
    // pref would need a hand-written setter and break the declarative pane.
    // normalizePort() is the validator instead.
    key: "connection.port",
    group: "connection",
    type: "string",
    default: "8765",
    ui: "text",
    requiresPro: false,
    labelKey: "prefs-connection-port",
    descriptionKey: "prefs-connection-port-desc",
    readBy: "src/prefs/prefs.ts serviceBaseUrlFromPrefs → src/service-client.ts",
  }),
  Object.freeze({
    key: "integration.column.lineage",
    group: "integration",
    type: "bool",
    default: true,
    ui: "checkbox",
    requiresPro: false,
    labelKey: "prefs-integration-lineage-column",
    descriptionKey: "prefs-integration-lineage-column-desc",
    readBy: "src/registry.ts registerWindowIndependent",
  }),
  Object.freeze({
    key: "integration.column.reading",
    group: "integration",
    type: "bool",
    default: true,
    ui: "checkbox",
    requiresPro: false,
    labelKey: "prefs-integration-reading-column",
    descriptionKey: "prefs-integration-reading-column-desc",
    readBy: "src/registry.ts registerWindowIndependent",
  }),
  Object.freeze({
    key: "pro.cross-check-report",
    group: "pro",
    type: "bool",
    default: false,
    ui: "checkbox",
    requiresPro: true,
    labelKey: "prefs-pro-cross-check",
    descriptionKey: "prefs-pro-cross-check-desc",
    readBy: "M3 商业层(未实现;占位)",
  }),
  Object.freeze({
    key: "pro.parallel-driver",
    group: "pro",
    type: "bool",
    default: false,
    ui: "checkbox",
    requiresPro: true,
    labelKey: "prefs-pro-parallel-driver",
    descriptionKey: "prefs-pro-parallel-driver-desc",
    readBy: "M3 商业层(未实现;占位)",
  }),
]);

/** One entry by bare key, or null. */
export function prefEntry(key) {
  const bare = String(key ?? "").trim();
  if (!bare) {
    return null;
  }
  return PREF_ENTRIES.find((entry) => entry.key === bare) ?? null;
}

/** Table rows for one group, in table order. */
export function entriesForGroup(group) {
  return PREF_ENTRIES.filter((entry) => entry.group === group);
}

/** Every Fluent key the table references, deduplicated, table order. */
export function prefFluentKeys() {
  const keys = [];
  for (const entry of PREF_ENTRIES) {
    for (const key of [entry.labelKey, entry.descriptionKey]) {
      if (typeof key === "string" && key.length > 0 && !keys.includes(key)) {
        keys.push(key);
      }
    }
  }
  for (const group of PREF_GROUPS) {
    for (const key of [group.titleKey, group.descriptionKey]) {
      if (typeof key === "string" && key.length > 0 && !keys.includes(key)) {
        keys.push(key);
      }
    }
  }
  if (!keys.includes(PANE_LABEL_KEY)) {
    keys.push(PANE_LABEL_KEY);
  }
  return keys;
}

/** Static pane strings that are not tied to one table row. */
export const PANE_FLUENT_KEYS = Object.freeze([
  PANE_LABEL_KEY,
  "prefs-service-host",
  "prefs-service-host-value",
  "prefs-service-host-desc",
  "prefs-connection-health",
  "prefs-health-checking",
  "prefs-health-online",
  "prefs-health-outdated",
  "prefs-health-offline",
  "prefs-refresh",
  "prefs-diagnostics-hint",
  "prefs-integration-deeplink",
  "prefs-integration-deeplink-desc",
  "prefs-pro-status",
  "prefs-pro-not-running",
  "prefs-pro-authorized",
  "prefs-pro-unauthorized",
  "prefs-pro-unknown",
  "prefs-pro-activate",
  "prefs-pro-activate-hint",
  "prefs-pro-needed",
  "prefs-platform-unsupported",
  "prefs-platform-unsupported-desc",
  "prefs-first-run-note",
]);

/** The loopback host the plugin talks to. Not configurable: the service binds it. */
export const SERVICE_HOST = "127.0.0.1";

/** The service's default port, mirroring kbapp/core/config.py `port`. */
export const DEFAULT_SERVICE_PORT = 8765;

/**
 * A pref value as a port number, or the fallback.
 *
 * The port is user-editable text, so anything that is not an integer in
 * 1..65535 falls back to the default. Returning the fallback rather than
 * throwing keeps a typo from breaking every request; the description tells
 * the user the default applies.
 */
export function normalizePort(value, fallback = DEFAULT_SERVICE_PORT) {
  const text = typeof value === "number" ? String(value) : String(value ?? "").trim();
  if (!/^\d{1,5}$/.test(text)) {
    return fallback;
  }
  const port = Number(text);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return fallback;
  }
  return port;
}

/** The service origin for one port value (loopback only). */
export function serviceBaseUrl(port) {
  return `http://${SERVICE_HOST}:${normalizePort(port)}`;
}

/**
 * Pro group status from the commercial layer's `GET /license/status`.
 *
 * The plugin never verifies a licence itself and never reads a licence file
 * (architecture channel ②: the commercial layer owns that). M1 has no
 * commercial process at all, so `null` — "no answer" — must render as
 * 「商业层未运行」, never as 「未授权」: the user has not been told they lack a
 * licence, they have been told a process is missing.
 */
export const PRO_STATES = Object.freeze({
  NOT_RUNNING: "not-running",
  AUTHORIZED: "authorized",
  UNAUTHORIZED: "unauthorized",
  UNKNOWN: "unknown",
});

/** Fluent key per Pro state. */
export const PRO_KEYS = Object.freeze({
  "not-running": "prefs-pro-not-running",
  authorized: "prefs-pro-authorized",
  unauthorized: "prefs-pro-unauthorized",
  unknown: "prefs-pro-unknown",
});

export function licenseView(response) {
  if (response === null || response === undefined) {
    return { state: PRO_STATES.NOT_RUNNING, labelKey: PRO_KEYS["not-running"] };
  }
  if (typeof response !== "object" || Array.isArray(response)) {
    return { state: PRO_STATES.UNKNOWN, labelKey: PRO_KEYS.unknown };
  }
  if (response.authorized === true) {
    return { state: PRO_STATES.AUTHORIZED, labelKey: PRO_KEYS.authorized };
  }
  if (response.authorized === false) {
    return { state: PRO_STATES.UNAUTHORIZED, labelKey: PRO_KEYS.unauthorized };
  }
  return { state: PRO_STATES.UNKNOWN, labelKey: PRO_KEYS.unknown };
}

/**
 * The platform card, mirroring docs/support-matrix.json (the single source of
 * platform truth). M1 ships Windows x64 only; macOS and Linux get an explicit
 * "暂不支持" card instead of a pane whose settings silently do nothing.
 */
export const SUPPORTED_PLATFORMS = Object.freeze(["win"]);

export function platformCard(platform) {
  if (SUPPORTED_PLATFORMS.includes(platform)) {
    return { supported: true, titleKey: null, descriptionKey: null };
  }
  return {
    supported: false,
    titleKey: "prefs-platform-unsupported",
    descriptionKey: "prefs-platform-unsupported-desc",
  };
}

/**
 * Prefs that gate a registration: read at startup, so a change takes effect
 * on the next Zotero start. Kept here as data so the pane description and the
 * reader cannot disagree about the "requires restart" note.
 */
export const STARTUP_ONLY_KEYS = Object.freeze([
  "integration.column.lineage",
  "integration.column.reading",
]);
