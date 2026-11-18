import { t } from "../utils/locale";
import { serviceBaseUrlFromPrefs } from "../prefs/serviceOrigin";
import { collectDiagnostics, probeHealth } from "./diagnostics";
import {
  STATUS_MENU,
  STATUS_POLL_MS,
  dotView,
  failedRows,
  menuItems,
  offlineHealth,
  workbenchUrl,
} from "./statusCore.mjs";
import type { DiagnosticRow, DotView } from "./statusCore.mjs";
import type { HealthVerdict } from "../serviceClientCore.mjs";

/**
 * The toolbar service status dot and its diagnostic popup (U5).
 *
 * Everything decidable lives in statusCore.mjs and is covered by
 * tests/test_plugin_status.cjs: the three-colour mapping, the version
 * comparison, the diagnostic row model and the context-menu enable rules.
 * This file is the edge that turns those values into DOM and makes one probe
 * through the S4 client. It never classifies a response itself.
 *
 * ── Host placement ──────────────────────────────────────────────────────
 *
 * The dot is appended to the main window's tab toolbar
 * (`#zotero-tabs-toolbar`, chrome/content/zotero/zoteroPane.xhtml), the row
 * that already hosts the sync button, so it sits where users look for
 * background status. That node is a host internal, so a missing toolbar is a
 * degradation, not a crash: the feature logs and the window keeps working
 * (真机验收 confirms the placement).
 *
 * ── Polling ─────────────────────────────────────────────────────────────
 *
 * One sequential probe at a time, then a long timeout (STATUS_POLL_MS). No
 * interval timer, so a slow or stuck service can never stack overlapping
 * probes, and the timer is registered for disposal with the rest of this
 * window's resources. The status vocabulary is S4's: `probe()` is a GET
 * /api/version through the one client, with `errorDelayMax: 0`.
 */

const HTML_NS = "http://www.w3.org/1999/xhtml";
const ROOT_ID = "littrace-status-dot";
const SIGNAL_ID = "littrace-status-dot-signal";
const MENU_ID = "littrace-status-menu";
const PANEL_ID = "littrace-status-panel";
const POPUPSET_ID = "littrace-popupset";
const STYLE_ID = "littrace-status-style";

const STYLE_TEXT = `
#${SIGNAL_ID} {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background-color: #8a8a8a;
}
#${SIGNAL_ID}[data-pending="true"] {
  animation: littrace-status-blink 1s ease-in-out infinite;
}
@keyframes littrace-status-blink {
  0%, 100% { opacity: 0.35; }
  50% { opacity: 1; }
}
#${PANEL_ID} {
  min-width: 320px;
  max-width: 460px;
}
.littrace-status-panel-title {
  font-weight: 600;
  margin-bottom: 6px;
}
.littrace-status-panel-row {
  margin: 2px 0;
}
.littrace-status-panel-row[data-tone="bad"] { color: #b3261e; }
.littrace-status-panel-row[data-tone="warn"] { color: #8a5300; }
.littrace-status-panel-row[data-tone="unknown"] { color: #6b6b6b; }
.littrace-status-panel-failures {
  margin-top: 8px;
  font-weight: 600;
}
.littrace-status-panel-actions {
  margin-top: 8px;
}
`;

export interface StatusDot {
  /** Probe once and repaint. Safe to call at any time; no-op after dispose. */
  refresh(): Promise<void>;
  /** Remove the dot, menu, panel, listeners, timers and style this host owns. */
  dispose(): void;
}

function report(error: unknown, what: string): void {
  Zotero.logError(
    error instanceof Error
      ? new Error(`littrace 状态点(${what}): ${error.message}`)
      : new Error(`littrace 状态点(${what}): ${String(error)}`),
  );
}

function html(doc: Document, className?: string): HTMLDivElement {
  const node = doc.createElementNS(HTML_NS, "div") as HTMLDivElement;
  if (className) {
    node.className = className;
  }
  return node;
}

/** XUL element creation with a fallback, so a non-XUL document degrades. */
function xul(doc: Document, tag: string): any {
  const factory = (doc as unknown as { createXULElement?: (name: string) => Element })
    .createXULElement;
  if (typeof factory === "function") {
    return factory.call(doc, tag);
  }
  return doc.createElement(tag);
}

function toolbarOf(win: Window): Element | null {
  const doc = win.document;
  return (
    doc.getElementById("zotero-tabs-toolbar") ??
    doc.getElementById("zotero-toolbar") ??
    null
  );
}

function popupsetOf(doc: Document): Element | null {
  const existing = doc.getElementById(POPUPSET_ID);
  if (existing) {
    return existing;
  }
  const root = doc.documentElement;
  if (!root) {
    return null;
  }
  const set = xul(doc, "popupset");
  set.id = POPUPSET_ID;
  root.append(set);
  return set;
}

/**
 * Create the status dot for one main window. Called only from
 * src/registry.ts's registerAll, so the returned disposer is always owned by
 * the registry; there is no second creation path.
 */
export function createStatusDot(win: Window): StatusDot {
  const doc = win.document;
  const disposers: Array<() => void> = [];
  let disposed = false;
  let timer: number | null = null;
  let inFlight = false;
  let view: DotView = dotView(null, { pending: true });

  const toolbar = toolbarOf(win);
  if (!toolbar) {
    // No host toolbar: log once and return a no-op handle. Throwing here
    // would abort the rest of registerAll and cost every other feature.
    report(new Error("未找到 Zotero 工具栏节点,状态点未挂载"), "挂载");
    return { async refresh() {}, dispose() {} };
  }

  const style = doc.createElementNS(HTML_NS, "style") as HTMLStyleElement;
  style.id = STYLE_ID;
  style.textContent = STYLE_TEXT;
  (doc.head ?? doc.documentElement).append(style);

  const popupset = popupsetOf(doc);

  const button = xul(doc, "toolbarbutton");
  button.id = ROOT_ID;
  button.className = "zotero-tb-button";
  button.setAttribute("tabindex", "-1");
  button.setAttribute("image", "");
  const signal = doc.createElementNS(HTML_NS, "span") as HTMLSpanElement;
  signal.id = SIGNAL_ID;
  button.append(signal);
  toolbar.append(button);

  const menu = xul(doc, "menupopup");
  menu.id = MENU_ID;
  const menuNodes = new Map<string, any>();
  for (const item of STATUS_MENU) {
    const node = xul(doc, "menuitem");
    node.id = `${MENU_ID}-${item.id}`;
    node.setAttribute("label", t(item.labelKey));
    node.addEventListener("command", () => {
      onMenuCommand(item.id);
    });
    menu.append(node);
    menuNodes.set(item.id, node);
  }
  const onMenuShowing = (): void => {
    for (const item of menuItems(view.state)) {
      const node = menuNodes.get(item.id);
      if (node) {
        node.disabled = !item.enabled;
      }
    }
  };
  menu.addEventListener("popupshowing", onMenuShowing);
  (popupset ?? toolbar).append(menu);
  // `context` is the only wiring needed for right-click; left-click opens the
  // diagnostic panel directly (both are 真机验收 items).
  button.setAttribute("context", MENU_ID);

  const panel = xul(doc, "panel");
  panel.id = PANEL_ID;
  panel.setAttribute("type", "arrow");
  panel.setAttribute("noautofocus", "true");
  (popupset ?? toolbar).append(panel);

  const onClick = (event: Event): void => {
    // Left click only: the context menu handles right click through `context`.
    if ((event as MouseEvent).button !== undefined && (event as MouseEvent).button !== 0) {
      return;
    }
    openDiagnostics();
  };
  button.addEventListener("click", onClick);

  paint();

  function paint(): void {
    signal.style.backgroundColor = view.color;
    signal.setAttribute("data-pending", view.blinking ? "true" : "false");
    const label = t(view.labelKey);
    const reason =
      view.state === "checking" || view.state === "online"
        ? ""
        : ` — ${t(view.reasonKey)}`;
    const tooltip = `${label}${reason}`;
    button.setAttribute("tooltiptext", tooltip);
    button.setAttribute("aria-label", tooltip);
  }

  function schedule(): void {
    if (disposed) {
      return;
    }
    if (timer !== null) {
      win.clearTimeout(timer);
    }
    timer = win.setTimeout(() => {
      timer = null;
      void refresh();
    }, STATUS_POLL_MS);
  }

  async function refresh(): Promise<void> {
    if (disposed || inFlight) {
      return;
    }
    inFlight = true;
    try {
      const health: HealthVerdict = await probeHealth();
      view = dotView(health);
    } catch (error) {
      // probe() resolves by contract; a throw means a programming error, and
      // the honest dot is grey, never green.
      report(error, "探测");
      view = dotView(offlineHealth());
    } finally {
      inFlight = false;
      if (!disposed) {
        paint();
        schedule();
      }
    }
  }

  function onMenuCommand(id: string): void {
    if (id === "workbench") {
      const url = workbenchUrl(serviceBaseUrlFromPrefs());
      if (!url) {
        report(new Error("服务地址为空,无法打开工作台"), "工作台");
        return;
      }
      try {
        Zotero.launchURL(url);
      } catch (error) {
        report(error, "打开工作台");
      }
      return;
    }
    if (id === "diagnostics") {
      openDiagnostics();
    }
  }

  function openDiagnostics(): void {
    if (disposed) {
      return;
    }
    renderPanel(null);
    try {
      if (typeof panel.openPopup === "function") {
        panel.openPopup(button, "after_end", 0, 0, false, false);
      }
    } catch (error) {
      report(error, "打开诊断");
    }
    void loadDiagnostics();
  }

  async function loadDiagnostics(): Promise<void> {
    let snapshot: Awaited<ReturnType<typeof collectDiagnostics>> | null = null;
    try {
      snapshot = await collectDiagnostics();
    } catch (error) {
      report(error, "诊断");
    }
    if (disposed) {
      return;
    }
    if (snapshot) {
      // The popup's version row and the dot must agree; reuse the verdict the
      // diagnostic probe just produced instead of probing a second time.
      view = dotView(snapshot.health);
      paint();
      renderPanel(snapshot.rows);
    } else {
      renderPanel(null, true);
    }
  }

  function rowNode(row: DiagnosticRow): HTMLElement {
    const node = html(doc, "littrace-status-panel-row");
    node.setAttribute("data-tone", row.tone);
    const value = row.value ?? (row.valueKey ? t(row.valueKey) : "");
    node.textContent = `${t(row.labelKey)}: ${value}`;
    return node;
  }

  function renderPanel(rows: DiagnosticRow[] | null, failed = false): void {
    panel.textContent = "";
    const box = html(doc);
    const title = html(doc, "littrace-status-panel-title");
    title.textContent = t("status-diag-title");
    box.append(title);
    if (!rows) {
      const line = html(doc, "littrace-status-panel-row");
      line.setAttribute("data-tone", "unknown");
      line.textContent = failed ? t("status-diag-failed") : t("status-diag-checking");
      box.append(line);
    } else {
      for (const row of rows) {
        box.append(rowNode(row));
      }
      const failures = failedRows(rows);
      if (failures.length > 0) {
        const line = html(doc, "littrace-status-panel-failures");
        line.textContent = t("status-diag-failures");
        box.append(line);
        for (const row of failures) {
          const item = html(doc, "littrace-status-panel-row");
          item.setAttribute("data-tone", row.tone);
          item.textContent = t(row.labelKey);
          box.append(item);
        }
      }
    }
    const actions = html(doc, "littrace-status-panel-actions");
    const refreshButton = doc.createElementNS(HTML_NS, "button") as HTMLButtonElement;
    refreshButton.textContent = t("status-diag-refresh");
    refreshButton.addEventListener("click", () => {
      renderPanel(null);
      void loadDiagnostics();
    });
    actions.append(refreshButton);
    box.append(actions);
    panel.append(box);
  }

  disposers.push(() => {
    button.removeEventListener("click", onClick);
    menu.removeEventListener("popupshowing", onMenuShowing);
    button.remove();
    menu.remove();
    panel.remove();
    style.remove();
    if (popupset && popupset.childElementCount === 0) {
      popupset.remove();
    }
  });

  void refresh();

  return {
    refresh,
    dispose() {
      if (disposed) {
        return;
      }
      disposed = true;
      if (timer !== null) {
        win.clearTimeout(timer);
        timer = null;
      }
      // Drop the table first, then run: a throwing disposer must not stop the
      // rest (the same rule the tab host's cleanup ledger follows).
      const pending = disposers.splice(0, disposers.length);
      for (const dispose of pending) {
        try {
          dispose();
        } catch (error) {
          report(error, "清理");
        }
      }
    },
  };
}
