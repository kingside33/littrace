/**
 * Pure logic for the toolbar service status dot and its diagnostic popup (U5).
 *
 * Deliberately plain ESM with no DOM and no Zotero globals, so
 * tests/test_plugin_status.cjs can run the real decision table with
 * `node --test`, no npm install and no Zotero process. src/status/status.ts
 * only turns these values into DOM nodes and one probe through the S4 client.
 *
 * ── The three dot colours, and why they are only three ───────────────────
 *
 * The product contract is 绿 = 在线且版本兼容, 黄 = 版本过旧, 灰 = 离线. The S4
 * client already answers the same question with three *health* states
 * (available / unsupported / offline, see serviceClientCore.mjs). Those are
 * not the same three: `unsupported` also covers "reachable but speaking a
 * different api_version" and malformed answers.
 *
 * A grey dot says "服务离线", and that would be false for a reachable service.
 * So the dot keeps three colours and maps:
 *
 *   available            → online   (green)
 *   unsupported          → outdated (yellow; 版本过旧 is the main case)
 *   offline / no verdict → offline  (grey)
 *
 * The exact reason (version-too-old, api-version-mismatch, …) is carried
 * alongside the colour and rendered in the tooltip and the diagnostic popup.
 * The colour is a summary; the popup is where the user finds the truth.
 *
 * ── Version comparison ───────────────────────────────────────────────────
 *
 * `versionRelation()` is the "service_version vs plugin minimum" rule as a
 * pure function. It delegates the actual numeric comparison to S4's
 * parseVersion/compareVersions, so there is exactly one version parser in the
 * plugin and the status layer cannot drift from what `probe()` decided. The
 * minimum requirement is likewise read from S4's DEFAULT_REQUIREMENTS: the
 * status popup and the client must never disagree about the floor.
 */

import {
  DEFAULT_REQUIREMENTS,
  HEALTH_STATES,
  compareVersions,
  parseVersion,
} from "../serviceClientCore.mjs";

/** Dot states. `checking` is the transient state before the first verdict. */
export const DOT_STATES = Object.freeze({
  CHECKING: "checking",
  ONLINE: "online",
  OUTDATED: "outdated",
  OFFLINE: "offline",
});

/**
 * Colour and label per dot state. Plain frozen data: the tests assert the
 * three settled colours are distinct and that every label key is a Fluent key
 * present in both bundles.
 */
export const DOT_VIEWS = Object.freeze({
  checking: Object.freeze({
    state: "checking",
    color: "#8a8a8a",
    labelKey: "status-dot-checking",
  }),
  online: Object.freeze({
    state: "online",
    color: "#2e7d32",
    labelKey: "status-dot-online",
  }),
  // Yellow rather than amber-on-brand: the dot sits in Zotero's own toolbar
  // and must read as a system warning, not as littrace chrome.
  outdated: Object.freeze({
    state: "outdated",
    color: "#b26a00",
    labelKey: "status-dot-outdated",
  }),
  offline: Object.freeze({
    state: "offline",
    color: "#8a8a8a",
    labelKey: "status-dot-offline",
  }),
});

/**
 * Health reason → Fluent key. S4 returns `http-<code>` for a non-2xx answer,
 * which is one reason class here ("the port answered, but not our service").
 */
export const REASON_KEYS = Object.freeze({
  unreachable: "status-reason-unreachable",
  "http-error": "status-reason-http",
  "malformed-response": "status-reason-malformed",
  "malformed-version": "status-reason-malformed",
  "api-version-mismatch": "status-reason-api-mismatch",
  "version-too-old": "status-reason-version-old",
  "version-too-new": "status-reason-version-new",
});

/** The Fluent key for one S4 health reason; never null. */
export function reasonKey(reason) {
  if (typeof reason === "string" && reason.startsWith("http-")) {
    return REASON_KEYS["http-error"];
  }
  return REASON_KEYS[reason] ?? "status-reason-unknown";
}

/**
 * The offline verdict, as S4's assessHealth() would return it for a transport
 * failure (`status: 0`). One definition because three call sites need it: the
 * collector's catch, the dot's catch, and the tests. A hand-written literal in
 * each place is exactly how the "status" field gets forgotten.
 */
export function offlineHealth(reason = "unreachable") {
  return { state: HEALTH_STATES.OFFLINE, reason, ok: false, status: 0 };
}

/**
 * S4 health verdict → dot state. Anything that is not a recognised verdict
 * (null, a throw, a future state string) is grey: the dot never claims green
 * without positive evidence.
 */
export function dotStateOf(health) {
  const state =
    health && typeof health === "object" ? health.state : null;
  if (state === HEALTH_STATES.AVAILABLE) {
    return DOT_STATES.ONLINE;
  }
  if (state === HEALTH_STATES.UNSUPPORTED) {
    return DOT_STATES.OUTDATED;
  }
  return DOT_STATES.OFFLINE;
}

/**
 * The full view model for one dot rendering: colour, label key, the exact
 * reason key, the versions involved and the enabled flags for the context
 * menu. `pending` is the initial "first probe still in flight" state, which
 * blinks and is then replaced by a settled verdict.
 */
export function dotView(health, options = {}) {
  const pending = options.pending === true;
  const state = pending ? DOT_STATES.CHECKING : dotStateOf(health);
  const base = DOT_VIEWS[state];
  const verdict = health && typeof health === "object" ? health : {};
  const reason = typeof verdict.reason === "string" ? verdict.reason : null;
  return {
    state,
    color: base.color,
    labelKey: base.labelKey,
    reason,
    reasonKey: reasonKey(reason),
    serviceVersion:
      typeof verdict.serviceVersion === "string" ? verdict.serviceVersion : null,
    minServiceVersion: minServiceVersionOf(options.requirements),
    blinking: pending,
  };
}

/** The plugin's minimum acceptable service version, single-sourced from S4. */
export function minServiceVersionOf(requirements = {}) {
  const given = requirements || {};
  return "minServiceVersion" in given
    ? given.minServiceVersion
    : DEFAULT_REQUIREMENTS.minServiceVersion;
}

/** Max bound, same "absent means default" rule as S4 (ADR 0021). */
function maxServiceVersionOf(requirements = {}) {
  const given = requirements || {};
  return "maxServiceVersion" in given
    ? given.maxServiceVersion
    : DEFAULT_REQUIREMENTS.maxServiceVersion;
}

/**
 * Compare a service version against the plugin's requirement window.
 *
 * Returns `{ relation, serviceVersion, minServiceVersion, maxServiceVersion }`
 * where relation is:
 *   ok        within [min, max]
 *   too-old   below the minimum        (the "版本过旧显黄" case)
 *   too-new   above the maximum        (the plugin is older than the service)
 *   unknown   serviceVersion is absent or unparsable; never "ok"
 *
 * A malformed requirement is a programming error, not a runtime condition:
 * it throws, exactly like assessHealth() in S4.
 */
export function versionRelation(serviceVersion, requirements = {}) {
  const min = minServiceVersionOf(requirements);
  const max = maxServiceVersionOf(requirements);
  const result = {
    relation: "unknown",
    serviceVersion:
      typeof serviceVersion === "string" ? serviceVersion : null,
    minServiceVersion: min,
    maxServiceVersion: max,
  };
  const parsed = parseVersion(serviceVersion);
  if (!parsed) {
    return result;
  }
  if (min !== null && !parseVersion(min)) {
    throw new TypeError(
      `littrace status: minServiceVersion is not a version: ${JSON.stringify(min)}`,
    );
  }
  if (max !== null && !parseVersion(max)) {
    throw new TypeError(
      `littrace status: maxServiceVersion is not a version: ${JSON.stringify(max)}`,
    );
  }
  if (min !== null && compareVersions(parsed, parseVersion(min)) < 0) {
    return { ...result, relation: "too-old" };
  }
  if (max !== null && compareVersions(parsed, parseVersion(max)) > 0) {
    return { ...result, relation: "too-new" };
  }
  return { ...result, relation: "ok" };
}

/** Fluent key per version relation, for the diagnostic row. */
export const RELATION_KEYS = Object.freeze({
  ok: "status-diag-version-ok",
  "too-old": "status-diag-version-old",
  "too-new": "status-diag-version-new",
  unknown: "status-diag-version-unknown",
});

/**
 * Port number of a service base URL, or null.
 *
 * The service is loopback-only (kbapp/core/config.py defaults port 8765), so
 * a URL without an explicit port means "the scheme default" (80/443), not
 * "no port" — reporting `null` there would blank a real value.
 */
export function portOf(baseUrl) {
  if (typeof baseUrl !== "string" || baseUrl.trim().length === 0) {
    return null;
  }
  const match =
    /^([a-z][a-z0-9+.-]*):\/\/(?:\[[^\]]*\]|[^/?#:]*)(?::(\d{1,5}))?/i.exec(
      baseUrl.trim(),
    );
  if (!match) {
    return null;
  }
  const scheme = match[1].toLowerCase();
  const explicit = match[2];
  const port = explicit === undefined ? (scheme === "https" ? 443 : scheme === "http" ? 80 : null) : Number(explicit);
  if (port === null || !Number.isInteger(port) || port < 1 || port > 65535) {
    return null;
  }
  return port;
}

/** Ollama reachability as derived from `GET /api/models`. */
export const OLLAMA_STATES = Object.freeze({
  REACHABLE: "reachable",
  UNREACHABLE: "unreachable",
  UNKNOWN: "unknown",
});

/**
 * Ollama reachability from the service's model report.
 *
 * `kbapp/integrations/models.py:210-233` sets `installed` to null when the
 * service's own `/api/tags` probe failed and to a boolean when Ollama
 * answered. So: a boolean means Ollama answered; all-null means it did not;
 * anything else (no items, wrong shape) is unknown. The plugin never talks to
 * Ollama directly — there is exactly one local-service origin, and the
 * service owns the model runtime choice (ollama vs native llama-server).
 */
export function ollamaState(models) {
  if (!models || typeof models !== "object" || Array.isArray(models)) {
    return OLLAMA_STATES.UNKNOWN;
  }
  const items = Array.isArray(models.items) ? models.items : null;
  if (!items || items.length === 0) {
    return OLLAMA_STATES.UNKNOWN;
  }
  if (items.some((item) => item && typeof item.installed === "boolean")) {
    return OLLAMA_STATES.REACHABLE;
  }
  return OLLAMA_STATES.UNREACHABLE;
}

/** Fluent keys for the Ollama row. */
export const OLLAMA_KEYS = Object.freeze({
  reachable: "status-diag-ollama-reachable",
  unreachable: "status-diag-ollama-unreachable",
  unknown: "status-diag-ollama-unknown",
});

/**
 * Export-sync states, mirrored from kbapp/workflows/publication.py sync_exports()
 * (`value = dict(status='running', ...)` → `status='synced'` → `status='failed'`).
 * Every one of the three refreshes `updated`, so `updated` alone says nothing
 * about whether the sync succeeded.
 */
export const SYNC_STATES = Object.freeze({
  SYNCED: "synced",
  RUNNING: "running",
  FAILED: "failed",
  UNKNOWN: "unknown",
});

/** Fluent keys for the last-sync row's state text. */
export const SYNC_KEYS = Object.freeze({
  synced: "status-diag-sync-synced",
  running: "status-diag-sync-running",
  failed: "status-diag-sync-failed",
  unknown: "status-diag-sync-unknown",
});

/**
 * The last sync, as a state + timestamp pair rather than a bare timestamp.
 *
 * `kbapp/workflows/publication.py:44-55` writes `export_sync` three times per
 * run: `running` before the work, then `synced` or `failed` — and all three
 * carry a fresh `updated`. Reading only `updated` therefore reports a failed
 * or still-running export as "上次同步 <时刻>" with a green tone, which is the
 * one reading a user would act on.
 *
 * The fallbacks are different fields with different meanings: `update_finished_at`
 * is only written after a completed update (update.py:174), and
 * `index_published_at` only on a successful index publish. Both are successes
 * by construction, so they report `synced`.
 *
 * Returns `{ state, at, error }`; `at` is the raw timestamp as the service
 * reported it (the renderer applies `displayStamp`), or null when nothing has
 * ever synced.
 */
export function lastSync(status) {
  const empty = { state: SYNC_STATES.UNKNOWN, at: null, error: null };
  if (!status || typeof status !== "object" || Array.isArray(status)) {
    return empty;
  }
  const sync = status.export_sync;
  if (sync && typeof sync === "object" && !Array.isArray(sync)) {
    const at =
      typeof sync.updated === "string" && sync.updated.trim() ? sync.updated.trim() : null;
    if (at) {
      const state =
        sync.status === SYNC_STATES.SYNCED
          ? SYNC_STATES.SYNCED
          : sync.status === SYNC_STATES.RUNNING
            ? SYNC_STATES.RUNNING
            : sync.status === SYNC_STATES.FAILED
              ? SYNC_STATES.FAILED
              : SYNC_STATES.UNKNOWN;
      return {
        state,
        at,
        error: typeof sync.error === "string" && sync.error.trim() ? sync.error.trim() : null,
      };
    }
  }
  for (const field of ["update_finished_at", "index_published_at"]) {
    const value = status[field];
    if (typeof value === "string" && value.trim()) {
      return { state: SYNC_STATES.SYNCED, at: value.trim(), error: null };
    }
  }
  return empty;
}

/**
 * Last-successful-sync timestamp from `GET /api/status`, or null.
 *
 * Kept as the narrow "just the instant" view of `lastSync()` for callers that
 * only want a stamp; the diagnostic row uses `lastSync()` because the state
 * is what distinguishes a success from a failure.
 */
export function lastSyncAt(status) {
  return lastSync(status).at;
}

/**
 * "YYYY-MM-DD HH:mm" for an ISO-ish timestamp, or the original text when it
 * is not in that shape. Returns null for an absent value. Deliberately not
 * locale-formatted: the same instant must render identically in tests and on
 * every platform, and the popup only needs minute precision.
 */
export function displayStamp(value) {
  if (typeof value !== "string" || value.trim().length === 0) {
    return null;
  }
  const text = value.trim();
  const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(text);
  return match ? `${match[1]} ${match[2]}` : text;
}

/** Diagnostic row ids, in display order. */
export const DIAGNOSTIC_ROWS = Object.freeze([
  Object.freeze({ id: "service-version", labelKey: "status-diag-service-version" }),
  Object.freeze({ id: "requirement", labelKey: "status-diag-requirement" }),
  Object.freeze({ id: "version-relation", labelKey: "status-diag-version-relation" }),
  Object.freeze({ id: "port", labelKey: "status-diag-port" }),
  Object.freeze({ id: "ollama", labelKey: "status-diag-ollama" }),
  Object.freeze({ id: "last-sync", labelKey: "status-diag-last-sync" }),
]);

/** Toned per row: `ok`/`warn`/`bad` drive the popup's highlighting. */
function row(id, tone, extra) {
  const spec = DIAGNOSTIC_ROWS.find((entry) => entry.id === id);
  return { id, labelKey: spec.labelKey, tone, value: null, valueKey: null, ...extra };
}

/**
 * The diagnostic popup's rows, as data.
 *
 * `input`:
 *   health     S4 health verdict (required; may be null before a probe)
 *   baseUrl    the service origin actually in use
 *   models     `GET /api/models` payload, or null when it was not fetched
 *   status     `GET /api/status` payload, or null
 *   requirements  the same requirement window handed to the client
 *
 * Every row is present even when the service is offline: the popup's job is
 * to say which checks failed, so a missing row would hide a failure. A row
 * with `tone: "unknown"` means "could not check", never "passed".
 *
 * Row fields: `value` is literal output (a version, a port, a timestamp),
 * `valueKey` is a Fluent key used instead of a value, `noteKey` is an extra
 * Fluent string appended after the value (the sync state word), and `detail`
 * is the service's own error text when it supplied one.
 */
export function diagnosticRows(input = {}) {
  const health = input.health && typeof input.health === "object" ? input.health : null;
  const healthState = health ? health.state : null;
  const reachable =
    healthState === HEALTH_STATES.AVAILABLE ||
    healthState === HEALTH_STATES.UNSUPPORTED;
  const requirements = input.requirements || {};
  const rows = [];

  // 1. The service version the client read from GET /api/version.
  const serviceVersion =
    health && typeof health.serviceVersion === "string"
      ? health.serviceVersion
      : null;
  const versionTone =
    healthState === HEALTH_STATES.AVAILABLE
      ? "ok"
      : healthState === HEALTH_STATES.UNSUPPORTED
        ? "warn"
        : "bad";
  rows.push(
    row("service-version", versionTone,
      serviceVersion
        ? { value: serviceVersion }
        : { valueKey: "status-diag-unavailable" }),
  );

  // 2. The floor this plugin build requires.
  const min = minServiceVersionOf(requirements);
  rows.push(
    row("requirement", "ok",
      typeof min === "string" && min.length > 0
        ? { value: min }
        : { valueKey: "status-diag-unavailable" }),
  );

  // 3. Do they agree? Only meaningful when a service version was read.
  if (!reachable || !serviceVersion) {
    rows.push(row("version-relation", healthState === null ? "unknown" : "bad",
      { valueKey: RELATION_KEYS.unknown }));
  } else {
    const relation = versionRelation(serviceVersion, requirements);
    rows.push(row("version-relation",
      relation.relation === "ok" ? "ok" : relation.relation === "unknown" ? "unknown" : "warn",
      { valueKey: RELATION_KEYS[relation.relation] }));
  }

  // 4. The port actually in use.
  const port = portOf(input.baseUrl);
  rows.push(
    row("port", port === null ? "warn" : "ok",
      port === null ? { valueKey: "status-diag-unavailable" } : { value: String(port) }),
  );

  // 5. Ollama, as reported by the service (or "could not check").
  const ollama = ollamaState(input.models);
  rows.push(
    row("ollama",
      ollama === OLLAMA_STATES.REACHABLE ? "ok" : ollama === OLLAMA_STATES.UNREACHABLE ? "warn" : "unknown",
      { valueKey: OLLAMA_KEYS[ollama] }),
  );

  // 6. Last sync. Only a `synced` export sync is a success: `running` and
  // `failed` both refresh the stamp, so they must not borrow the green tone.
  const sync = lastSync(input.status);
  if (sync.at === null) {
    rows.push(row("last-sync", "unknown", { valueKey: "status-diag-never-synced" }));
  } else {
    const tone =
      sync.state === SYNC_STATES.SYNCED
        ? "ok"
        : sync.state === SYNC_STATES.RUNNING
          ? "unknown"
          : "warn";
    rows.push(
      row("last-sync", tone, {
        // The instant is data; the state word is a Fluent string the renderer
        // appends through noteKey, so the row never carries a raw key as text.
        value: displayStamp(sync.at),
        noteKey: SYNC_KEYS[sync.state],
        // The failure text, when the service supplied one.
        detail: sync.error,
      }),
    );
  }

  return rows;
}

/** The failed / unverifiable rows, for the popup's "失败项" list. */
export function failedRows(rows) {
  if (!Array.isArray(rows)) {
    return [];
  }
  return rows.filter((entry) => entry.tone === "warn" || entry.tone === "bad");
}

/**
 * Right-click menu entries on the status dot.
 *
 * `needsReachable` is the only rule that greys an entry out: opening the web
 * workbench with no service shows a browser error page, which is a worse
 * failure than a greying; diagnostics must always stay clickable, because it
 * is the entry that explains why everything else is grey.
 */
export const STATUS_MENU = Object.freeze([
  Object.freeze({
    id: "workbench",
    labelKey: "status-menu-workbench",
    needsReachable: true,
  }),
  Object.freeze({
    id: "diagnostics",
    labelKey: "status-menu-diagnostics",
    needsReachable: false,
  }),
]);

/** Menu entries with their enabled flags for one dot state. */
export function menuItems(state) {
  const reachable = state === DOT_STATES.ONLINE || state === DOT_STATES.OUTDATED;
  return STATUS_MENU.map((entry) => ({
    id: entry.id,
    labelKey: entry.labelKey,
    enabled: entry.needsReachable ? reachable : true,
  }));
}

/** Web workbench URL (the service root page), or null for an empty origin. */
export function workbenchUrl(baseUrl) {
  if (typeof baseUrl !== "string" || baseUrl.trim().length === 0) {
    return null;
  }
  return `${baseUrl.trim().replace(/\/+$/, "")}/`;
}

/**
 * Endpoints the status layer reads. Only `version` goes through the polling
 * probe; the other two are fetched when the user opens the diagnostic popup,
 * so the toolbar dot stays one cheap request.
 */
export const STATUS_ENDPOINTS = Object.freeze({
  version: "/api/version",
  status: "/api/status?detail=false",
  models: "/api/models",
});

/**
 * Poll cadence for the dot. Long on purpose: the local service state changes
 * on user action, and a busy toolbar polling a single-process Python server
 * every second would be a cost with no benefit.
 */
export const STATUS_POLL_MS = 30000;
