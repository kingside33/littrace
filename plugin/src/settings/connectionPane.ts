import { t } from "../utils/locale";
import { serviceClient } from "../service-client";
import { serviceBaseUrlFromPrefs } from "../prefs/serviceOrigin";
import { openWindowDiagnostics } from "../status/status";
import { canOpenHostLink, openHostLink } from "../tabs/tabs";
import { savePayloadFrom, settingsViewFrom, versionViewFrom } from "./settingsCore.mjs";

/**
 * The connection group's extra rows: the version line, the Zotero data
 * directory, and the two jumps to the local service's other doors.
 *
 * Nothing here writes a secret, so none of it is platform-gated — a macOS user
 * can point the service at their Zotero directory and open the workbench even
 * though they cannot store a cloud key (see settingsPane.applyKeyGate).
 *
 * All three reuse what the shell already has:
 *   - the version line renders the diagnostics the pane's single refresh
 *     already collected, instead of probing /api/version a second time;
 *   - the workbench and diagnostics buttons use the same host-link and
 *     status-dot entry points as the menus (U6), so the greying rules match.
 *
 * `loadConnection` takes the settings view and the diagnostic rows as
 * arguments rather than fetching them: both groups read the same two things,
 * and opening the pane used to issue six requests of which two pairs were
 * byte-identical. src/settings/paneRefresh.ts owns the one read.
 */

const IDS = {
  version: "littrace-prefs-version",
  zoteroDir: "littrace-prefs-zotero-dir",
  choose: "littrace-prefs-zotero-dir-choose",
  pickerHint: "littrace-prefs-zotero-dir-picker-hint",
  workbench: "littrace-prefs-open-workbench",
  diagnostics: "littrace-prefs-open-diagnostics",
} as const;

function report(error: unknown, what: string): void {
  Zotero.logError(
    error instanceof Error
      ? new Error(`littrace 设置页(${what}): ${error.message}`)
      : new Error(`littrace 设置页(${what}): ${String(error)}`),
  );
}

function inputOf(doc: Document, id: string): HTMLInputElement | null {
  const node = doc.getElementById(id);
  return node ? (node as unknown as HTMLInputElement) : null;
}

function buttonOf(doc: Document, id: string): HTMLButtonElement | null {
  const node = doc.getElementById(id);
  return node ? (node as unknown as HTMLButtonElement) : null;
}

/**
 * Paint the version line from the diagnostics rows.
 *
 * The facts come from rows the diagnostic popup already computes, so the pane
 * cannot disagree with the popup about what version is running. The relation
 * word is the diagnostic row's own Fluent key — no fourth vocabulary for the
 * same three states.
 *
 * Three separate spans rather than one concatenation: "服务 0.1.0" and
 * "要求 ≥ 0.1.0" are different facts, and a separator that exists only to join
 * them would read as a third one.
 */
export function paintVersion(doc: Document, rows: unknown): void {
  const row = doc.getElementById(IDS.version);
  if (!row) {
    return;
  }
  const view = versionViewFrom(rows);
  const parts: Array<{ text: string; tone: string | null }> = [];
  if (view.serviceVersion) {
    parts.push({ text: t("prefs-version-service", { version: view.serviceVersion }), tone: null });
  }
  if (view.minServiceVersion) {
    parts.push({
      text: t("prefs-version-requirement", { version: view.minServiceVersion }),
      tone: null,
    });
  }
  if (view.relationKey) {
    parts.push({ text: t(view.relationKey), tone: view.tone });
  }
  row.replaceChildren();
  row.removeAttribute("data-l10n-id");
  for (const part of parts) {
    const span = doc.createElementNS("http://www.w3.org/1999/xhtml", "span");
    span.textContent = part.text;
    if (part.tone) {
      span.setAttribute("data-tone", part.tone);
    }
    row.append(span);
  }
}

/** Paint the connection rows from one shared refresh. Never throws. */
export function renderConnection(
  doc: Document,
  settings: unknown,
  rows: unknown,
): void {
  if (settings !== undefined && settings !== null) {
    const view = settingsViewFrom(settings);
    const box = inputOf(doc, IDS.zoteroDir);
    if (box) {
      box.value = typeof view.fields.zotero_dir === "string" ? view.fields.zotero_dir : "";
    }
  }
  paintVersion(doc, rows);
}

/** Save just the directory, leaving every other field as the service has it. */
export async function saveZoteroDir(doc: Document): Promise<void> {
  const box = inputOf(doc, IDS.zoteroDir);
  if (!box) {
    return;
  }
  const payload = savePayloadFrom({ zotero_dir: box.value }, "connection");
  try {
    await serviceClient().post("/api/settings", payload);
  } catch (error) {
    report(error, "保存数据目录");
  }
}

/**
 * Open Zotero's own directory picker.
 *
 * `FilePicker` is **not** a global on the preferences window: it is an ES
 * module export. Zotero's own preferences panes load it the same way —
 * `chrome/content/zotero/preferences/preferences_advanced.js` does
 * `var { FilePicker } = ChromeUtils.importESModule('chrome://zotero/content/modules/filePicker.mjs')`.
 * Reaching for `doc.defaultView.FilePicker` finds `undefined` every time, which
 * would leave the button permanently dead with nothing but a console line.
 *
 * The bare XPCOM `nsIFilePicker` is also wrong here: its `init()` takes a
 * browsing context, so passing a window raises NS_ERROR_INVALID_ARG. Zotero's
 * wrapper does that unwrapping itself, which is why it takes the window back.
 *
 * There is no other picker in this repository, so this path stays a real-machine
 * acceptance item. A failure is reported **and** written into the field's own
 * hint line, so the user is not left with a button that silently does nothing.
 */
export async function chooseZoteroDir(doc: Document): Promise<void> {
  const box = inputOf(doc, IDS.zoteroDir);
  const pickerModule = loadFilePickerModule();
  if (!pickerModule) {
    showPickerFailure(doc);
    report(new Error("chrome://zotero/content/modules/filePicker.mjs 不可用"), "选择数据目录");
    return;
  }
  let picked: string | null = null;
  try {
    const picker = new pickerModule.FilePicker();
    picker.init(doc.defaultView, t("prefs-zotero-dir-picker-title"), picker.modeGetFolder);
    if ((await picker.show()) !== picker.returnOK) {
      return;
    }
    picked = typeof picker.file === "string" ? picker.file : String(picker.file ?? "");
  } catch (error) {
    showPickerFailure(doc);
    report(error, "选择数据目录");
    return;
  }
  if (!picked || !box) {
    return;
  }
  box.value = picked;
  const hint = doc.getElementById(IDS.pickerHint);
  if (hint) {
    hint.hidden = true;
  }
  await saveZoteroDir(doc);
}

/** The Zotero filePicker module, or null when this build does not have it. */
function loadFilePickerModule(): { FilePicker: new () => any } | null {
  const chromeUtils = (globalThis as { ChromeUtils?: { importESModule?: (uri: string) => unknown } })
    .ChromeUtils;
  if (typeof chromeUtils?.importESModule !== "function") {
    return null;
  }
  try {
    const mod = chromeUtils.importESModule(
      "chrome://zotero/content/modules/filePicker.mjs",
    ) as { FilePicker?: new () => any } | null;
    return mod && typeof mod.FilePicker === "function" ? { FilePicker: mod.FilePicker } : null;
  } catch {
    return null;
  }
}

/**
 * Make a picker failure visible.
 *
 * The pane already has a Fluent string for this, and the field stays editable —
 * a silent no-op is the one outcome the repository's 「an entry either works or
 * says why」 rule forbids.
 */
function showPickerFailure(doc: Document): void {
  const hint = doc.getElementById(IDS.pickerHint);
  if (hint) {
    hint.hidden = false;
    hint.textContent = t("prefs-zotero-dir-picker-failed");
  }
}

/** Open the web workbench in the user's browser. */
export function openWorkbench(): void {
  const href = `${serviceBaseUrlFromPrefs()}/`;
  if (!openHostLink(href)) {
    report(new Error(`无法打开工作台 ${href}`), "打开工作台");
  }
}

/** Open this window's diagnostic popup, or say why there is none. */
export function openDiagnostics(doc: Document): void {
  const win = doc.defaultView;
  if (win && openWindowDiagnostics(win)) {
    return;
  }
  // Same rule as the tools menu (U6): an entry either works or says why.
  const row = doc.getElementById(IDS.diagnostics);
  if (row) {
    row.setAttribute("data-unavailable", "true");
    row.textContent = t("prefs-open-unavailable");
  }
}

/** Wire the connection group's new controls. The first read comes from the pane. */
export function mountConnection(doc: Document): void {
  const choose = buttonOf(doc, IDS.choose);
  if (choose) {
    choose.addEventListener("click", () => {
      void chooseZoteroDir(doc);
    });
  }
  const dirBox = inputOf(doc, IDS.zoteroDir);
  if (dirBox) {
    // Saving on change matches the cloud group's explicit save for the fields a
    // user edits together, while this row has exactly one field.
    dirBox.addEventListener("change", () => {
      void saveZoteroDir(doc);
    });
  }
  const workbench = buttonOf(doc, IDS.workbench);
  if (workbench) {
    workbench.addEventListener("click", () => openWorkbench());
    // The menus grey the workbench entry when the host cannot launch a link: an
    // error page in the browser is a worse failure than a disabled button.
    if (!canOpenHostLink()) {
      workbench.disabled = true;
    }
  }
  const diagnostics = buttonOf(doc, IDS.diagnostics);
  if (diagnostics) {
    diagnostics.addEventListener("click", () => openDiagnostics(doc));
  }
}
