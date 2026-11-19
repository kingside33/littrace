/**
 * The settings page's server-side half: the cloud AI group's field table, the
 * three pure transforms between the service and the pane's form, and the probe
 * verdict mapping.
 *
 * Deliberately plain ESM with no Zotero, DOM or TypeScript globals, so
 * tests/test_plugin_settings.cjs exercises the real rules with `node --test`
 * and no build step. src/settings/settingsPane.ts is the only adapter that
 * touches the DOM and the S4 client; src/prefs/prefsCore.mjs keeps owning the
 * *Zotero pref* rows and stays untouched here.
 *
 * ── Why these are not prefs ─────────────────────────────────────────────
 *
 * Everything in SETTINGS_FIELDS lives in the service's `settings.json`, not in
 * `extensions.zotero.littrace.*`. Two consequences the tests hold onto:
 *   - these rows must NOT appear in prefsCore's PREF_ENTRIES, because that
 *     table's contract is "every visible row has a declarative
 *     `preference="<full key>"` binding", and a server-side field can never
 *     have one;
 *   - the keys must match `kbapp/core/config.py`'s DEFAULTS verbatim, since
 *     `store.save_settings` rejects unknown keys with a Chinese 400 that a
 *     typo would only reveal at runtime. The test scans both sides.
 *
 * ── The three rules that are easy to get wrong ──────────────────────────
 *
 * 1. Formatting: `GET /api/settings` returns floats for prices and ints for
 *    ports, and `0` is indistinguishable from "never set" unless the view
 *    turns it into an empty input. It also returns `0.0` for `daily_budget`,
 *    which the limited budget mode refuses, so showing "0" would invite a
 *    value that cannot be saved.
 * 2. Payload: `None` means "keep the existing key" for `api_key` /
 *    `openalex_key`, but for every other key `store.save_settings` would store
 *    the string "None" or answer 500. So the payload may never contain null.
 * 3. Clearing: the public view reports the key as a boolean, so the form
 *    cannot express "delete this key". Omitting the key on save would make a
 *    saved key undeletable forever, which is a security gap when a key leaks;
 *    hence the explicit, single-purpose clear payload.
 */

/** Storage group the pane renders; one section today, kept as data for later. */
export const SETTINGS_GROUPS = Object.freeze([
  Object.freeze({
    id: "cloud",
    titleKey: "prefs-group-cloud",
    descriptionKey: "prefs-group-cloud-desc",
  }),
  Object.freeze({
    id: "connection",
    titleKey: "prefs-group-connection",
    descriptionKey: "prefs-group-connection-desc",
  }),
]);

/**
 * One row per server-side field, in render order.
 *
 * `key`     — the exact `kbapp/core/config.py` DEFAULTS key
 * `type`    — how the pane renders and reads it
 * `sensitive` — the service returns a boolean for it, never the value
 * `labelKey` / `descriptionKey` — bare Fluent keys
 */
export const SETTINGS_FIELDS = Object.freeze([
  Object.freeze({
    key: "api_base",
    type: "text",
    sensitive: false,
    labelKey: "prefs-cloud-base",
    descriptionKey: "prefs-cloud-base-desc",
  }),
  Object.freeze({
    key: "model",
    type: "text",
    sensitive: false,
    labelKey: "prefs-cloud-model",
    descriptionKey: "prefs-cloud-model-desc",
  }),
  Object.freeze({
    key: "api_key",
    type: "password",
    sensitive: true,
    labelKey: "prefs-cloud-key",
    descriptionKey: "prefs-cloud-key-desc",
  }),
  Object.freeze({
    key: "cloud_proxy",
    type: "text",
    sensitive: false,
    labelKey: "prefs-cloud-proxy",
    descriptionKey: "prefs-cloud-proxy-desc",
  }),
  Object.freeze({
    key: "budget_mode",
    type: "select",
    sensitive: false,
    labelKey: "prefs-cloud-budget-mode",
    descriptionKey: "prefs-cloud-budget-mode-desc",
  }),
  Object.freeze({
    key: "daily_budget",
    type: "number",
    sensitive: false,
    labelKey: "prefs-cloud-daily-budget",
    descriptionKey: "prefs-cloud-daily-budget-desc",
  }),
  Object.freeze({
    key: "input_price",
    type: "number",
    sensitive: false,
    labelKey: "prefs-cloud-input-price",
    descriptionKey: "prefs-cloud-input-price-desc",
  }),
  Object.freeze({
    key: "output_price",
    type: "number",
    sensitive: false,
    labelKey: "prefs-cloud-output-price",
    descriptionKey: "prefs-cloud-output-price-desc",
  }),
  Object.freeze({
    key: "cloud_enabled",
    type: "bool",
    sensitive: false,
    labelKey: "prefs-cloud-enabled",
    descriptionKey: "prefs-cloud-enabled-desc",
  }),
]);

/** The two budget modes `store.save_settings` accepts. */
export const BUDGET_MODES = Object.freeze(["limited", "unlimited"]);

/** The raw keys `GET /api/settings` may carry; everything else is ignored. */
const KNOWN_KEYS = new Set(SETTINGS_FIELDS.map((field) => field.key));

/** A field by key, or null. */
export function settingsField(key) {
  const bare = String(key ?? "").trim();
  if (!bare) {
    return null;
  }
  return SETTINGS_FIELDS.find((field) => field.key === bare) ?? null;
}

/**
 * `GET /api/settings` (public view) → the pane's form values.
 *
 * Returns `{fields: {key: string|boolean}, budgetMode}` where numbers are
 * strings ready for `<input type="number">` and `""` means "not set".
 *
 * Unknown keys are dropped on purpose: the public view carries a `connection`
 * object (reserved, never written by kbapp) and posting it back would be
 * rejected as an unknown setting.
 */
export function settingsViewFrom(publicSettings) {
  const source =
    publicSettings && typeof publicSettings === "object" && !Array.isArray(publicSettings)
      ? publicSettings
      : {};
  const fields = {};
  for (const field of SETTINGS_FIELDS) {
    const raw = source[field.key];
    if (field.type === "bool") {
      fields[field.key] = raw === true;
      continue;
    }
    if (field.sensitive) {
      // The service answers with a boolean; there is no value to put in the box.
      fields[field.key] = raw === true;
      continue;
    }
    if (field.type === "number") {
      // 0 is "not set" for this form: daily_budget 0 is refused by limited
      // mode, and showing "0" would invite a value that cannot be saved.
      const value = typeof raw === "number" ? raw : Number(raw);
      fields[field.key] = Number.isFinite(value) && value !== 0 ? String(value) : "";
      continue;
    }
    if (field.type === "select") {
      const value = typeof raw === "string" ? raw : "";
      fields[field.key] = BUDGET_MODES.includes(value) ? value : BUDGET_MODES[0];
      continue;
    }
    // Text fields are strings on the wire. Anything else is a malformed answer,
    // and rendering `String(raw)` would invent a value the user then saves.
    fields[field.key] = typeof raw === "string" ? raw : "";
  }
  return { fields, budgetMode: fields.budget_mode };
}

/** Whether the last `GET` says a key is stored. */
export function keyStored(view) {
  return Boolean(view && view.fields && view.fields.api_key === true);
}

/**
 * The pane's form → the `POST /api/settings` body.
 *
 * Rules, each of which the tests pin:
 *   - never emits `null` (a null non-sensitive value would be stored as the
 *     string "None" or raise a 500);
 *   - omits `api_key` when blank, so saving the other fields never wipes it;
 *   - omits a number field whose input is blank, so the service keeps its
 *     current value instead of receiving 0;
 *   - sends `budget_mode` and `cloud_enabled` always, because they are the two
 *     switches the pane owns unconditionally.
 */
export function savePayloadFrom(form) {
  const source =
    form && typeof form === "object" && !Array.isArray(form) ? form : {};
  const payload = {};
  for (const field of SETTINGS_FIELDS) {
    const raw = source[field.key];
    if (field.sensitive) {
      const text = typeof raw === "string" ? raw.trim() : "";
      if (text) {
        payload[field.key] = text;
      }
      continue;
    }
    if (field.type === "bool") {
      payload[field.key] = raw === true;
      continue;
    }
    if (field.type === "number") {
      const text =
        typeof raw === "number" ? String(raw) : typeof raw === "string" ? raw.trim() : "";
      if (!text) {
        continue;
      }
      const value = Number(text);
      // A non-numeric entry is a user error the pane must not send: the service
      // would turn it into a 500 instead of a readable validation message.
      if (Number.isFinite(value)) {
        payload[field.key] = value;
      }
      continue;
    }
    if (field.type === "select") {
      const value = typeof raw === "string" ? raw : "";
      payload[field.key] = BUDGET_MODES.includes(value) ? value : BUDGET_MODES[0];
      continue;
    }
    // Text fields are sent even when empty: clearing an address must be
    // expressible. Only the sensitive field and blank numbers are omitted.
    payload[field.key] = typeof raw === "string" ? raw.trim() : raw == null ? "" : String(raw);
  }
  return payload;
}

/**
 * The one payload that deletes a stored key.
 *
 * `cloud_enabled: false` is not optional: `store.save_settings` refuses to
 * leave cloud enabled without a key, so clearing it alone would answer 400
 * and the key would stay. Sending both makes the clear operation succeed and
 * leaves the service in a state that says what happened.
 */
export function clearKeyPayload() {
  return { api_key: "", cloud_enabled: false };
}

/**
 * The probe verdict vocabulary, mirroring `kbapp/integrations/cloud.py`'s
 * PROBE_CODES. Kept as an explicit list here so a code added on the service
 * side without a Fluent label fails a test instead of rendering a raw code.
 */
export const TEST_CODES = Object.freeze({
  OK: "ok",
  NOT_CONFIGURED: "not_configured",
  UNREACHABLE: "unreachable",
  DNS: "dns",
  TLS_CERTIFICATE: "tls_certificate",
  TLS: "tls",
  TIMEOUT: "timeout",
  AUTH: "auth",
  REDIRECT_REFUSED: "redirect_refused",
  PROBE_UNSUPPORTED: "probe_unsupported",
  HTTP: "http",
});

/** Fluent key per probe code. Every code has exactly one. */
export const TEST_CODE_KEYS = Object.freeze({
  ok: "prefs-probe-ok",
  not_configured: "prefs-probe-not-configured",
  unreachable: "prefs-probe-unreachable",
  dns: "prefs-probe-dns",
  tls_certificate: "prefs-probe-tls-certificate",
  tls: "prefs-probe-tls",
  timeout: "prefs-probe-timeout",
  auth: "prefs-probe-auth",
  redirect_refused: "prefs-probe-redirect-refused",
  probe_unsupported: "prefs-probe-unsupported",
  http: "prefs-probe-http",
});

/** Three tones the status row can paint. */
export const TEST_TONES = Object.freeze({
  OK: "ok",
  WARN: "warn",
  BAD: "bad",
});

/**
 * `POST /api/settings/test` → the status row's view.
 *
 * The service's `verdict` is three-state on purpose (reachable+authenticated /
 * endpoint has no /models / failed); a boolean would have to lie about one of
 * them. `unsupported` paints as a warning rather than an error: something
 * answered, it just cannot confirm the model.
 *
 * `model_listed === false` is appended when the service could compare the
 * configured model against the endpoint's list: that is a real finding even
 * though the transport succeeded.
 */
export function testResultView(response) {
  const data =
    response && typeof response === "object" && !Array.isArray(response) ? response : {};
  const code = typeof data.code === "string" && data.code in TEST_CODE_KEYS ? data.code : "http";
  const verdict = typeof data.verdict === "string" ? data.verdict : "";
  let tone = TEST_TONES.BAD;
  if (verdict === "ok" || code === TEST_CODES.OK) {
    tone = TEST_TONES.OK;
  } else if (verdict === "unsupported") {
    tone = TEST_TONES.WARN;
  }
  return {
    tone,
    labelKey: TEST_CODE_KEYS[code],
    detail: typeof data.summary === "string" ? data.summary : null,
    // The route the request actually took; a proxy fallback is the user's
    // signal that their configured proxy was not used.
    networkRoute: typeof data.network_route === "string" ? data.network_route : null,
    modelListed: data.model_listed === true ? true : data.model_listed === false ? false : null,
  };
}

/** Fluent key for the proxy-fallback note, or null when it does not apply. */
export function proxyNoteKey(view) {
  return view && view.networkRoute === "system_fallback" ? "prefs-probe-proxy-fallback" : null;
}

/** Static pane strings this module's callers render, as data. */
export const PANE_FLUENT_KEYS = Object.freeze([
  "prefs-cloud-saving",
  "prefs-cloud-saved",
  "prefs-cloud-loading",
  "prefs-cloud-load-failed",
  "prefs-cloud-save-failed",
  "prefs-cloud-testing",
  "prefs-cloud-test-failed",
  "prefs-cloud-clear",
  "prefs-cloud-clear-done",
  "prefs-cloud-clear-failed",
  "prefs-cloud-clear-hint",
  "prefs-cloud-key-platform-unsupported",
  "prefs-cloud-save",
  "prefs-cloud-test",
  "prefs-cloud-model-not-listed",
  "prefs-probe-proxy-fallback",
  "prefs-probe-detail",
  "prefs-platform-cloud-unsupported",
  "prefs-platform-cloud-unsupported-desc",
]);

/** Every Fluent key this module references, in a stable order. */
export function settingsFluentKeys() {
  const keys = [];
  const push = (key) => {
    if (typeof key === "string" && key.length > 0 && !keys.includes(key)) {
      keys.push(key);
    }
  };
  for (const group of SETTINGS_GROUPS) {
    push(group.titleKey);
    push(group.descriptionKey);
  }
  for (const field of SETTINGS_FIELDS) {
    push(field.labelKey);
    push(field.descriptionKey);
  }
  for (const key of Object.values(TEST_CODE_KEYS)) {
    push(key);
  }
  for (const key of PANE_FLUENT_KEYS) {
    push(key);
  }
  return keys;
}
