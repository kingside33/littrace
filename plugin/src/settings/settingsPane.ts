import { t } from "../utils/locale";
import { serviceClient } from "../service-client";
import { platformName } from "../prefs/prefs";
import {
  BUDGET_MODES,
  clearKeyPayload,
  savePayloadFrom,
  settingsViewFrom,
  testResultView,
  SETTINGS_FIELDS,
} from "./settingsCore.mjs";
import type { SettingsView, TestResultView } from "./settingsCore.mjs";
import { platformCard } from "../prefs/prefsCore.mjs";

/**
 * The settings page's server-side half: the cloud AI group.
 *
 * Everything that decides *what to send* lives in settingsCore.mjs and is
 * covered by tests/test_plugin_settings.cjs. This file owns exactly three
 * things that need a Zotero process: reading and writing the preferences
 * window's DOM, calling the S4 client (the one request path with its token
 * cache), and deciding that a platform cannot carry a key at all.
 *
 * Two rules borrowed from the rest of the shell:
 *   - **No probe on load.** `POST /api/settings/test` runs only when the user
 *     clicks 「测试连接」. SECURITY.md says a request without a user action is a
 *     bug, and a pane that pings a metered endpoint on open would be one.
 *   - **A generation counter drops stale answers**, so a slow load cannot
 *     overwrite a fresh save and a re-opened pane cannot be painted by its
 *     predecessor (the same shape as prefsPane.refresh, prefs.ts:176-195).
 */

const IDS = {
  group: "littrace-prefs-cloud",
  platform: "littrace-prefs-cloud-platform",
  base: "littrace-prefs-cloud-base",
  model: "littrace-prefs-cloud-model",
  key: "littrace-prefs-cloud-key",
  clear: "littrace-prefs-cloud-clear",
  clearHint: "littrace-prefs-cloud-clear-hint",
  proxy: "littrace-prefs-cloud-proxy",
  mode: "littrace-prefs-cloud-budget-mode",
  budget: "littrace-prefs-cloud-daily-budget",
  inputPrice: "littrace-prefs-cloud-input-price",
  outputPrice: "littrace-prefs-cloud-output-price",
  enabled: "littrace-prefs-cloud-enabled",
  save: "littrace-prefs-cloud-save",
  test: "littrace-prefs-cloud-test",
  status: "littrace-prefs-cloud-status",
} as const;

/** Field key → element id, so settingsCore's table stays the row authority. */
const FIELD_IDS: Record<string, string> = {
  api_base: IDS.base,
  model: IDS.model,
  api_key: IDS.key,
  cloud_proxy: IDS.proxy,
  budget_mode: IDS.mode,
  daily_budget: IDS.budget,
  input_price: IDS.inputPrice,
  output_price: IDS.outputPrice,
  cloud_enabled: IDS.enabled,
};

/** The three inputs limited mode owns; the mode switch greys them out. */
const LIMITED_ONLY = [IDS.budget, IDS.inputPrice, IDS.outputPrice];

/** Lifecycle strings the status row can show. */
const STATUS_ROLES = {
  loading: "prefs-cloud-loading",
  loaded: "prefs-cloud-loaded",
  loadFailed: "prefs-cloud-load-failed",
  saving: "prefs-cloud-saving",
  saved: "prefs-cloud-saved",
  saveFailed: "prefs-cloud-save-failed",
  testing: "prefs-cloud-testing",
  testFailed: "prefs-cloud-test-failed",
  clearDone: "prefs-cloud-clear-done",
  clearFailed: "prefs-cloud-clear-failed",
} as const;

type StatusRole = keyof typeof STATUS_ROLES;

function report(error: unknown, what: string): void {
  Zotero.logError(
    error instanceof Error
      ? new Error(`littrace 设置页(${what}): ${error.message}`)
      : new Error(`littrace 设置页(${what}): ${String(error)}`),
  );
}

function input(doc: Document, id: string): HTMLInputElement | null {
  const node = doc.getElementById(id);
  return node ? (node as unknown as HTMLInputElement) : null;
}

function select(doc: Document, id: string): HTMLSelectElement | null {
  const node = doc.getElementById(id);
  return node ? (node as unknown as HTMLSelectElement) : null;
}

/** The detail a ServiceClientError carries, or its message. */
function detailOf(error: unknown): string {
  const detail = (error as { detail?: unknown })?.detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  return error instanceof Error ? error.message : String(error);
}

/** Paint one status line. Always through Fluent; never a literal. */
function setStatus(doc: Document, role: StatusRole, args?: Record<string, unknown>): void {
  const row = doc.getElementById(IDS.status);
  if (!row) {
    return;
  }
  row.textContent = t(STATUS_ROLES[role], args);
  row.removeAttribute("data-tone");
}

/**
 * Text for one probe result, including the two conditional notes.
 *
 * Written out with literal keys so tests/test_plugin_settings.cjs can scan this
 * file and fail when a key escapes the settingsCore registry.
 */
export function probeStatusText(view: TestResultView): string {
  const parts = [t(view.labelKey)];
  if (view.modelListed === false) {
    parts.push(t("prefs-cloud-model-not-listed"));
  }
  if (view.networkRoute === "system_fallback") {
    parts.push(t("prefs-probe-proxy-fallback"));
  }
  const text = parts.join(" ");
  return view.detail ? `${text} ${t("prefs-probe-detail", { detail: view.detail })}` : text;
}

function setProbeStatus(doc: Document, view: TestResultView): void {
  const row = doc.getElementById(IDS.status);
  if (!row) {
    return;
  }
  row.textContent = probeStatusText(view);
  row.setAttribute("data-tone", view.tone);
}

/** Fill the form from one settings view. */
function fillForm(doc: Document, view: SettingsView): void {
  for (const field of SETTINGS_FIELDS) {
    const id = FIELD_IDS[field.key];
    if (!id) {
      continue;
    }
    const value = view.fields[field.key];
    if (field.type === "bool") {
      const box = doc.getElementById(id) as HTMLInputElement | null;
      if (box) {
        box.checked = value === true;
      }
      continue;
    }
    if (field.sensitive) {
      // The service answers with a boolean. The box stays empty: filling it
      // would put a value there the user could save back as a new key.
      const box = input(doc, id);
      if (box) {
        box.value = "";
      }
      continue;
    }
    if (field.type === "select") {
      const box = select(doc, id);
      if (box) {
        box.value = typeof value === "string" ? value : BUDGET_MODES[0];
      }
      continue;
    }
    const box = input(doc, id);
    if (box) {
      box.value = typeof value === "string" ? value : "";
    }
  }
  applyBudgetMode(doc);
}

/** Grey out the three numbers unlimited mode does not use. */
function applyBudgetMode(doc: Document): void {
  const mode = select(doc, IDS.mode);
  const limited = (mode?.value ?? BUDGET_MODES[0]) !== "unlimited";
  for (const id of LIMITED_ONLY) {
    const box = input(doc, id);
    if (box) {
      box.disabled = !limited;
    }
  }
}

/** Read the form back into the shape settingsCore expects. */
function readForm(doc: Document): Record<string, unknown> {
  const form: Record<string, unknown> = {};
  for (const field of SETTINGS_FIELDS) {
    const id = FIELD_IDS[field.key];
    if (!id) {
      continue;
    }
    if (field.type === "bool") {
      const box = doc.getElementById(id) as HTMLInputElement | null;
      form[field.key] = box ? box.checked === true : false;
      continue;
    }
    const box = field.type === "select" ? select(doc, id) : input(doc, id);
    form[field.key] = box ? String(box.value ?? "") : "";
  }
  return form;
}

/**
 * Whether this platform can protect a stored key.
 *
 * docs/support-matrix.json is the platform authority. The reason is concrete
 * but narrower than "the platform is unsupported": `protect()` in
 * kbapp/core/secrets.py returns early for an empty value and only reaches the
 * `os.name != 'nt'` guard for a **non-empty** key. So reading the settings,
 * saving the address/model/prices and probing a keyless loopback endpoint all
 * work off Windows — only storing a key does not.
 */
function canProtectKey(): boolean {
  return platformCard(platformName()).supported;
}

/**
 * Disable the one control that cannot work here, and show why.
 *
 * Deliberately narrower than greying the whole group: disabling everything
 * would claim the free features of the group are unavailable on this platform,
 * which is not true and cannot be acted on by the user.
 */
function applyKeyGate(doc: Document): void {
  if (canProtectKey()) {
    return;
  }
  const card = doc.getElementById(IDS.platform);
  if (card) {
    card.hidden = false;
  }
  // The key box and its clear button are the two controls that write a secret.
  for (const id of [IDS.key, IDS.clear]) {
    const node = input(doc, id);
    if (node) {
      node.disabled = true;
    }
  }
  const clear = doc.getElementById(IDS.clear);
  if (clear) {
    (clear as unknown as HTMLButtonElement).disabled = true;
  }
}

/** Per-pane generation; a newer action invalidates older answers. */
const generations = new WeakMap<Document, number>();

function nextGeneration(doc: Document): number {
  const value = (generations.get(doc) ?? 0) + 1;
  generations.set(doc, value);
  return value;
}

function stillCurrent(doc: Document, generation: number): boolean {
  return generations.get(doc) === generation && doc.documentElement.isConnected;
}

/**
 * Read the current settings into the form.
 *
 * Never throws into the preferences window: a service that is offline shows a
 * readable line, which is the whole point of having the row.
 */
export async function loadCloud(doc: Document): Promise<void> {
  const generation = nextGeneration(doc);
  applyKeyGate(doc);
  setStatus(doc, "loading");
  try {
    const data = await serviceClient().get("/api/settings");
    if (!stillCurrent(doc, generation)) {
      return;
    }
    fillForm(doc, settingsViewFrom(data));
    setStatus(doc, "loaded");
  } catch (error) {
    if (!stillCurrent(doc, generation)) {
      return;
    }
    report(error, "读取云端设置");
    setStatus(doc, "loadFailed", { detail: detailOf(error) });
  }
}

/**
 * Paint the cloud form from a view the shared refresh already fetched.
 *
 * `view === null` means the read failed; `error` is then the reason and the
 * status row shows it instead of a form of blanks that would look like "every
 * field is empty".
 */
export function renderCloud(doc: Document, view: SettingsView | null, error: unknown): void {
  applyKeyGate(doc);
  if (view === null) {
    setStatus(doc, "loadFailed", { detail: detailOf(error) });
    return;
  }
  fillForm(doc, view);
  // A load is not a save: this row must not claim 「设置已保存」 for an action
  // the user never took.
  setStatus(doc, "loaded");
}

/** Drop the form's DOM state when the pane closes. */
export function resetCloud(doc: Document): void {
  generations.delete(doc);
}

/** Save the form, then re-read so every row reflects what the service stored. */
export async function saveCloud(doc: Document): Promise<void> {
  const generation = nextGeneration(doc);
  const payload = savePayloadFrom(readForm(doc), "cloud");
  if (!canProtectKey() && typeof payload.api_key === "string" && payload.api_key.length > 0) {
    // The one write that cannot succeed here. Refuse it locally with a readable
    // line instead of letting the service answer a opaque 500.
    setStatus(doc, "saveFailed", { detail: t("prefs-cloud-key-platform-unsupported") });
    return;
  }
  setStatus(doc, "saving");
  try {
    await serviceClient().post("/api/settings", payload);
    if (!stillCurrent(doc, generation)) {
      return;
    }
    // POST answers without the `connection` key; one more GET keeps every row
    // consistent with what was actually stored.
    const fresh = await serviceClient().get("/api/settings");
    if (!stillCurrent(doc, generation)) {
      return;
    }
    fillForm(doc, settingsViewFrom(fresh));
    setStatus(doc, "saved");
  } catch (error) {
    if (!stillCurrent(doc, generation)) {
      return;
    }
    report(error, "保存云端设置");
    setStatus(doc, "saveFailed", { detail: detailOf(error) });
  }
}

/** Delete the stored key; the payload also turns cloud calls off. */
export async function clearKey(doc: Document): Promise<void> {
  const generation = nextGeneration(doc);
  if (!canProtectKey()) {
    // Clearing is a write of an empty key, which needs no DPAPI. But the
    // control is disabled here because the button only makes sense next to a
    // key this platform could never have stored — say so rather than no-op.
    setStatus(doc, "clearFailed", { detail: t("prefs-cloud-key-platform-unsupported") });
    return;
  }
  const hint = doc.getElementById(IDS.clearHint);
  if (hint) {
    hint.hidden = false;
  }
  try {
    await serviceClient().post("/api/settings", clearKeyPayload());
    if (!stillCurrent(doc, generation)) {
      return;
    }
    const fresh = await serviceClient().get("/api/settings");
    if (!stillCurrent(doc, generation)) {
      return;
    }
    fillForm(doc, settingsViewFrom(fresh));
    setStatus(doc, "clearDone");
  } catch (error) {
    if (!stillCurrent(doc, generation)) {
      return;
    }
    report(error, "清除云端密钥");
    setStatus(doc, "clearFailed", { detail: detailOf(error) });
  }
}

/** Probe the saved endpoint. Only ever runs from a click. */
export async function testCloud(doc: Document): Promise<void> {
  const generation = nextGeneration(doc);
  // No platform gate: probing reads the saved configuration and sends no
  // secret of its own, so it works wherever the service does.
  setStatus(doc, "testing");
  try {
    const data = await serviceClient().post("/api/settings/test");
    if (!stillCurrent(doc, generation)) {
      return;
    }
    setProbeStatus(doc, testResultView(data));
  } catch (error) {
    if (!stillCurrent(doc, generation)) {
      return;
    }
    report(error, "测试云端连接");
    setStatus(doc, "testFailed", { detail: detailOf(error) });
  }
}

/**
 * Wire the group's buttons and run the first read.
 *
 * Called from prefsPane.onPaneLoad. Every element is looked up defensively so a
 * Zotero build that renames ids degrades to a missing row rather than a throw
 * inside the preferences window.
 */
export function mountCloud(doc: Document): void {
  const save = doc.getElementById(IDS.save);
  if (save) {
    save.addEventListener("click", () => {
      void saveCloud(doc);
    });
  }
  const clear = doc.getElementById(IDS.clear);
  if (clear) {
    clear.addEventListener("click", () => {
      void clearKey(doc);
    });
  }
  const test = doc.getElementById(IDS.test);
  if (test) {
    test.addEventListener("click", () => {
      void testCloud(doc);
    });
  }
  const mode = doc.getElementById(IDS.mode);
  if (mode) {
    mode.addEventListener("change", () => applyBudgetMode(doc));
  }
}
