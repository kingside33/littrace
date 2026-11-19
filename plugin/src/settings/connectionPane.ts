import { t } from "../utils/locale";
import { serviceClient } from "../service-client";
import { serviceBaseUrlFromPrefs } from "../prefs/serviceOrigin";
import { collectDiagnostics } from "../status/diagnostics";
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
 *   - the version line re-reads the diagnostics the health refresh already
 *     collects, instead of probing /api/version a second time;
 *   - the workbench and diagnostics buttons use the same host-link and
 *     status-dot entry points as the menus (U6), so the greying rules match.
 */

const IDS = {
  version: "littrace-prefs-version",
  zoteroDir: "littrace-prefs-zotero-dir",
  choose: "littrace-prefs-zotero-dir-choose",
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

/** Per-pane generation; a newer refresh invalidates older answers. */
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
 * Paint the version line from one diagnostics snapshot.
 *
 * The facts come from rows the diagnostic popup already computes, so the pane
 * cannot disagree with the popup about what version is running. The relation
 * word is the diagnostic row's own Fluent key — no fourth vocabulary for the
 * same three states.
 *
 * Two separate lines rather than one concatenation: "服务 0.1.0" and
 * "要求 ≥ 0.1.0" are different facts, and a separator string that exists only
 * to join them reads as a third fact.
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

/** Read the directory and version rows. Never throws into the pane. */
export async function loadConnection(doc: Document): Promise<void> {
  const generation = nextGeneration(doc);
  try {
    const settings = await serviceClient().get("/api/settings");
    if (!stillCurrent(doc, generation)) {
      return;
    }
    const view = settingsViewFrom(settings);
    const box = inputOf(doc, IDS.zoteroDir);
    if (box) {
      box.value = typeof view.fields.zotero_dir === "string" ? view.fields.zotero_dir : "";
    }
  } catch (error) {
    // The health row and the cloud status row both report service trouble;
    // this row simply stays empty rather than repeating the same failure.
    report(error, "读取数据目录");
  }
  try {
    const snapshot = await collectDiagnostics();
    if (!stillCurrent(doc, generation)) {
      return;
    }
    paintVersion(doc, snapshot.rows);
  } catch (error) {
    report(error, "读取服务版本");
  }
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
 * `window.FilePicker` is Zotero's Promise wrapper around nsIFilePicker
 * (chrome/content/zotero/modules/filePicker.mjs). The bare XPCOM object is the
 * wrong tool here: its `init()` takes a BrowsingContext, so passing a Window
 * raises NS_ERROR_INVALID_ARG. There is no other picker in this repository, so
 * this path is a real-machine acceptance item, not a tested one.
 *
 * A failure degrades visibly but does not block: the field itself is editable.
 */
export async function chooseZoteroDir(doc: Document): Promise<void> {
  const box = inputOf(doc, IDS.zoteroDir);
  const view = doc.defaultView as unknown as { FilePicker?: new () => any } | null;
  const FilePicker = view?.FilePicker;
  if (typeof FilePicker !== "function") {
    report(new Error("Zotero.FilePicker 不可用"), "选择数据目录");
    return;
  }
  let picked: string | null = null;
  try {
    const picker = new FilePicker();
    picker.init(view, t("prefs-zotero-dir-picker-title"), picker.modeGetFolder);
    if ((await picker.show()) !== picker.returnOK) {
      return;
    }
    picked = typeof picker.file === "string" ? picker.file : String(picker.file ?? "");
  } catch (error) {
    report(error, "选择数据目录");
    if (box) {
      box.setAttribute("data-picker-failed", "true");
    }
    return;
  }
  if (!picked || !box) {
    return;
  }
  box.value = picked;
  box.removeAttribute("data-picker-failed");
  await saveZoteroDir(doc);
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

/** Wire the connection group's new controls and run the first read. */
export function mountConnection(doc: Document): void {
  const choose = buttonOf(doc, IDS.choose);
  if (choose) {
    choose.addEventListener("click", () => {
      void chooseZoteroDir(doc);
    });
  }
  const bluebox = inputOf(doc, IDS.zoteroDir);
  if (bluebox) {
    // Saving on change keeps this row consistent with the rest of the pane,
    // which has an explicit save for the group of fields a user edits together.
    bluebox.addEventListener("change", () => {
      void saveZoteroDir(doc);
    });
  }
  const workbench = buttonOf(doc, IDS.workbench);
  if (workbench) {
    workbench.addEventListener("click", () => openWorkbench());
    // The menus grey the workbench entry when the service is unreachable: an
    // error page in the browser is a worse failure than a disabled button.
    if (!canOpenHostLink()) {
      workbench.disabled = true;
    }
  }
  const diagnostics = buttonOf(doc, IDS.diagnostics);
  if (diagnostics) {
    diagnostics.addEventListener("click", () => openDiagnostics(doc));
  }
  void loadConnection(doc);
}
