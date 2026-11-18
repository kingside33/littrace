import { config } from "../../package.json";
import { t } from "../utils/locale";
import { ftlId } from "../prefs/prefsCore.mjs";
import { PANE_ID } from "../prefs/prefs";
import { serviceBaseUrlFromPrefs } from "../prefs/serviceOrigin";
import { probeHealth } from "../status/diagnostics";
import { openWindowDiagnostics } from "../status/status";
import { offlineHealth } from "../status/statusCore.mjs";
import { workbenchUrl } from "../itemSectionsCore.mjs";
import { openFunctionTab } from "../tabs/tabs";
import {
  EMPTY_COMPARE_SET,
  FEEDBACK_KEYS,
  PROBE_TTL_MS,
  compareAddFeedback,
  compareRunRequest,
  compareSetAdd,
  compareReadiness,
  createVerdictCache,
  entryStateOf,
  guideView,
  GUIDE_STATUS_KEYS,
  menuSpecs,
  paperIdOf,
  registeredEntries,
  serviceStateOf,
  targetsOf,
} from "./menuCore.mjs";
import type {
  CompareSet,
  MenuContextInput,
  MenuEntry,
  MenuServiceState,
  MenuSpec,
  PaperTarget,
} from "./menuCore.mjs";
import type { HealthVerdict } from "../serviceClientCore.mjs";

/**
 * The item / tools menus and the offline guide card (U6).
 *
 * Every decision that can be made without Zotero lives in menuCore.mjs and is
 * covered by tests/test_plugin_menus.cjs: the contract table, the two gates
 * that grey an entry, the comparison set and its 2–4 bound, the guide card's
 * copy keys. This file is the edge: it turns the table into
 * `Zotero.MenuManager.registerMenu()` options, derives the menu context from
 * the live selection and the S4 health verdict, and runs the commands.
 *
 * ── Registration is process-wide, not per window ────────────────────────
 *
 * `Zotero.MenuManager` keeps ONE registry keyed by `menuID`
 * (xpcom/pluginAPI/menuManager.js: `updateMenuPopup()` reads it for every
 * main window; `unregisterMenu()` removes the items from all of them).
 * Registering once per main window would therefore be a duplicate id, so the
 * menus are registered in `registerWindowIndependent()` through
 * `registerGlobal()` — the same rule as the item columns and the preference
 * pane (ADR 0009). The window a command needs is derived from the menu
 * element the hook hands us, never from a captured one.
 *
 * ── Greying │ onShowing + one probe ─────────────────────────────────────
 *
 * `onShowing` is synchronous, so the entries are painted from the last
 * settled verdict and a probe starts in the background; when it lands, every
 * menu element still on screen is repainted through its own captured
 * `context.setEnabled`. The verdict is cached for PROBE_TTL_MS, so opening
 * the submenu and then the tools menu costs one request, and the status dot
 * remains the only periodic poller (no second timer here).
 *
 * ── The guide card ─────────────────────────────────────────────────────
 *
 * Greyed entries cannot be clicked, so the explanation lives behind one entry
 * that always stays enabled (「本地服务未运行 — 查看启动引导」). The card is a
 * XUL `panel` owned by this module; `openDiagnostics()` on the status dot is
 * reused for 「服务诊断」 instead of growing a second diagnostic surface.
 */

const HTML_NS = "http://www.w3.org/1999/xhtml";
const POPUPSET_ID = "littrace-menu-popupset";
const GUIDE_PANEL_ID = "littrace-menu-guide-panel";
const GUIDE_STYLE_ID = "littrace-menu-guide-style";

const GUIDE_STYLE = `
#${GUIDE_PANEL_ID} { max-width: 460px; }
.littrace-menu-guide { padding: 10px 12px; }
.littrace-menu-guide-title { font-weight: 600; margin-bottom: 6px; }
.littrace-menu-guide-status { margin-bottom: 8px; color: #6b6b6b; }
.littrace-menu-guide-line { margin: 3px 0; }
.littrace-menu-guide-actions { margin-top: 10px; display: flex; gap: 8px; }
`;

/** The manager surface this file touches; absent on a Zotero without it. */
interface MenuManagerAPI {
  registerMenu(options: Record<string, unknown>): unknown;
  unregisterMenu(menuID: string): unknown;
}

/** One registered menu, plus what it takes to release it. */
interface MenuHandle {
  menuID: string;
  dispose(): void;
}

function report(error: unknown, what: string): void {
  Zotero.logError(
    error instanceof Error
      ? new Error(`littrace 菜单(${what}): ${error.message}`)
      : new Error(`littrace 菜单(${what}): ${String(error)}`),
  );
}

function menuManager(): MenuManagerAPI | null {
  const api = (Zotero as { MenuManager?: Partial<MenuManagerAPI> }).MenuManager;
  if (!api || typeof api.registerMenu !== "function") {
    return null;
  }
  return api as MenuManagerAPI;
}

/**
 * One instance per plugin process. The comparison set is in-memory on
 * purpose: it is a staging area for one compare run, not library data, and
 * it must not survive a Zotero restart (the menu can always rebuild it).
 */
let compareEntries: CompareSet = EMPTY_COMPARE_SET;
const verdictCache = createVerdictCache({ ttl: PROBE_TTL_MS });

/** `entry id → entry` so a captured hook can recover its contract row. */
const ENTRY_BY_ID = new Map<string, MenuEntry>(
  registeredEntries().map((entry) => [entry.id, entry]),
);

/** The context of the last `onShowing` per entry, for the late repaint. */
const liveContexts = new Map<string, any>();

/** Bumped on dispose so a late probe/recheck cannot repaint a dead plugin. */
let generation = 0;
let probing: Promise<void> | null = null;

/** The current menu-facing service state (CHECKING before the first probe). */
function serviceState(): MenuServiceState {
  return serviceStateOf(verdictCache.read());
}

/** The selection in menu terms: how many addressable paper targets. */
function selectionInput(items: unknown): { count: number; skipped: number } {
  const { targets, skipped } = targetsOf(items);
  return { count: targets.length, skipped };
}

/** Everything the pure rules need, from live state. */
function menuInput(items: unknown): MenuContextInput {
  const selection = selectionInput(items);
  return {
    service: serviceState(),
    selection: selection.count,
    compareSize: compareEntries.length,
  };
}

/** The window a menu hook belongs to. */
function windowOf(context: any): Window | null {
  const doc = context?.menuElem?.ownerDocument;
  const win = doc?.defaultView;
  if (win) {
    return win as Window;
  }
  const windows = Zotero.getMainWindows?.();
  const fallback = Array.isArray(windows) ? windows[0] : undefined;
  return (fallback as Window) ?? null;
}

/** Call one of MenuManager's context setters without letting it throw. */
function safeSet(context: any, name: string, value: boolean): void {
  const setter = context?.[name];
  if (typeof setter !== "function") {
    return;
  }
  try {
    setter.call(context, value);
  } catch (error) {
    report(error, name);
  }
}

/**
 * Paint the label (and, for an entry, the reason tooltip) onto one menu
 * element.
 *
 * `l10nID` alone relies on the window's Fluent overlay resolving the plugin's
 * bundle; painting the same message from `t()` guarantees a visible label even
 * when that overlay has not loaded the resource yet, and it can never differ
 * from the l10nID text because both come from the same message. Menus are
 * rebuilt on every open, so a locale switch is picked up on the next open.
 */
function paintElement(menuElem: any, label: string, reason = ""): void {
  if (!menuElem || typeof menuElem.setAttribute !== "function") {
    return;
  }
  try {
    menuElem.setAttribute("label", label);
    menuElem.setAttribute("tooltiptext", reason ? `${label} — ${reason}` : label);
  } catch {
    // 元素已销毁: 没有 label/tooltip 可设置。
  }
}

/**
 * Paint one entry from the current context: enabled, visible, and a tooltip
 * carrying the grey-out reason. Without the tooltip the greying is silent,
 * and a silently greyed entry is indistinguishable from a broken one.
 */
function applyEntryState(entry: MenuEntry, context: any): void {
  const state = entryStateOf(entry, menuInput(context?.items));
  safeSet(context, "setEnabled", state.enabled);
  safeSet(context, "setVisible", state.visible);
  paintElement(
    context?.menuElem,
    t(entry.labelKey),
    state.reasonKey ? t(state.reasonKey) : "",
  );
}

/** Repaint every menu element that is still on screen. */
function repaintLiveContexts(): void {
  for (const [id, context] of liveContexts) {
    const entry = ENTRY_BY_ID.get(id);
    if (!entry) {
      continue;
    }
    try {
      applyEntryState(entry, context);
    } catch (error) {
      report(error, `刷新 ${id}`);
    }
  }
}

/**
 * Probe once (shared by every menu open) and repaint the live elements.
 * The cache TTL keeps a burst of menu opens down to one request; the S4
 * client keeps a thrown probe from ever looking like a green service.
 */
function refreshVerdict(force = false): Promise<void> {
  if (probing) {
    return probing;
  }
  if (!force && !verdictCache.stale()) {
    return Promise.resolve();
  }
  const started = generation;
  probing = (async () => {
    let health: HealthVerdict;
    try {
      health = await probeHealth();
    } catch (error) {
      // probe() resolves by contract; a throw is a programming error, and
      // the honest state is offline, never green.
      report(error, "健康探测");
      health = offlineHealth() as HealthVerdict;
    }
    verdictCache.write(health);
    if (started !== generation) {
      return;
    }
    repaintLiveContexts();
  })().finally(() => {
    probing = null;
  });
  return probing;
}

/* ── Feedback ────────────────────────────────────────────────────────── */

/**
 * One transient confirmation line. The menu closes as soon as a command
 * fires, so the outcome of a fire-and-forget action must land somewhere the
 * user sees; `Zotero.ProgressWindow` is the host's own channel for this.
 * A missing ProgressWindow is reported, never swallowed.
 */
function feedback(window: Window | null, key: string, args?: Record<string, unknown>): void {
  try {
    const Ctor = (Zotero as { ProgressWindow?: new (options?: unknown) => any })
      .ProgressWindow;
    if (typeof Ctor !== "function") {
      report(new Error("Zotero.ProgressWindow 不可用,无法显示操作结果"), "提示");
      return;
    }
    const win = new Ctor({ window: window ?? undefined, closeOnClick: true });
    win.changeHeadline(t(FEEDBACK_KEYS.headline));
    win.addDescription(t(key, args));
    win.show();
    win.startCloseTimer(4000);
  } catch (error) {
    report(error, "提示");
  }
}

/* ── Commands ────────────────────────────────────────────────────────── */

/**
 * Open one function tab, falling back to the system browser when the tab
 * host refused. The browser page is the same service page, so this is a real
 * alternative rather than a dead end; the failure itself is still reported.
 */
function openTab(window: Window, id: string, paper = ""): boolean {
  const options = paper ? { paper } : {};
  if (openFunctionTab(window, id, options)) {
    return true;
  }
  try {
    const url = workbenchUrl(serviceBaseUrlFromPrefs(), id, paper);
    Zotero.launchURL(url);
    return true;
  } catch (error) {
    report(error, `打开 ${id}`);
    feedback(window, FEEDBACK_KEYS.tabUnavailable);
    return false;
  }
}

/** First usable target of the current selection, or null. */
function firstTarget(items: unknown): PaperTarget | null {
  const { targets } = targetsOf(items);
  return targets.length > 0 ? targets[0] : null;
}

function runEvidence(window: Window, items: unknown): void {
  const { targets } = targetsOf(items);
  if (targets.length === 0) {
    feedback(window, FEEDBACK_KEYS.selectFirst);
    return;
  }
  // One selected paper scopes the search page to it; a multi-selection opens
  // the unscoped page rather than silently picking one of them.
  openTab(window, "search", targets.length === 1 ? paperIdOf(targets[0]) : "");
}

function runCompareAdd(window: Window, items: unknown): void {
  const { targets } = targetsOf(items);
  if (targets.length === 0) {
    feedback(window, FEEDBACK_KEYS.selectFirst);
    return;
  }
  const result = compareSetAdd(compareEntries, targets);
  compareEntries = result.entries;
  feedback(
    window,
    compareAddFeedback(result),
    { count: result.size, added: result.added, full: result.full },
  );
}

function runCompareClear(window: Window): void {
  compareEntries = EMPTY_COMPARE_SET;
  feedback(window, FEEDBACK_KEYS.compareCleared);
}

async function runCompareRun(window: Window): Promise<void> {
  const request = compareRunRequest(compareEntries);
  if (!request) {
    // The entry is gated on the same condition, so this is the "state moved
    // between showing and clicking" path; say why instead of doing nothing.
    feedback(window, compareReadiness(compareEntries).reasonKey ?? FEEDBACK_KEYS.selectFirst);
    return;
  }
  try {
    const result = await addon.service.submitTask(request.path, request.body);
    // `unknown` is not a success (S4 ADR 0020); the tab still opens so the
    // user can look at the run list themselves.
    feedback(
      window,
      result.outcome === "unknown"
        ? FEEDBACK_KEYS.compareUnknown
        : FEEDBACK_KEYS.compareSubmitted,
    );
    openTab(window, "research");
  } catch (error) {
    report(error, "提交对比");
    feedback(window, FEEDBACK_KEYS.compareUnknown);
  }
}

function runLocate(window: Window, items: unknown): void {
  const target = firstTarget(items);
  if (!target) {
    feedback(window, FEEDBACK_KEYS.selectFirst);
    return;
  }
  openTab(window, "graph", paperIdOf(target));
}

/**
 * Submit a parse job for the selected paper.
 *
 * The id is resolved through `GET /api/v2/paper/sections` first: the service's
 * `/api/documents/process` looks the paper up with `store.resolve()`, which
 * does not map `z:<libraryID>:<key>` for a paper stored under a legacy id
 * (the same rule U2's parse button documents). Submitting the unresolved id
 * would be a 400 the user cannot act on.
 */
async function runParse(window: Window, items: unknown): Promise<void> {
  const target = firstTarget(items);
  if (!target) {
    feedback(window, FEEDBACK_KEYS.selectFirst);
    return;
  }
  const paper = paperIdOf(target);
  let resolved = "";
  try {
    const snapshot = await addon.service.get(
      `/api/v2/paper/sections?paper=${encodeURIComponent(paper)}`,
    );
    resolved =
      snapshot && typeof snapshot.paper_id === "string" ? snapshot.paper_id : "";
  } catch (error) {
    report(error, "解析前解析文献");
    feedback(window, FEEDBACK_KEYS.selectFirst);
    return;
  }
  if (!resolved) {
    feedback(window, FEEDBACK_KEYS.selectFirst);
    return;
  }
  try {
    const result = await addon.service.submitTask("/api/documents/process", {
      paper_id: resolved,
    });
    feedback(
      window,
      result.outcome === "unknown"
        ? FEEDBACK_KEYS.parseUnknown
        : FEEDBACK_KEYS.parseSubmitted,
    );
  } catch (error) {
    report(error, "提交解析");
    feedback(window, FEEDBACK_KEYS.parseUnknown);
  }
}

function runDiagnostics(window: Window): void {
  if (openWindowDiagnostics(window)) {
    return;
  }
  // No status dot in this window (host toolbar missing): the settings page
  // still shows the health row, so point there instead of doing nothing.
  feedback(window, FEEDBACK_KEYS.diagnosticsUnavailable);
  openPreferencesPane();
}

/** Open the plugin's preferences pane; failures are reported, not thrown. */
function openPreferencesPane(): void {
  try {
    const internal = (Zotero as {
      Utilities?: { Internal?: { openPreferences?: (paneID?: string) => void } };
    }).Utilities?.Internal;
    if (typeof internal?.openPreferences !== "function") {
      report(new Error("Zotero.Utilities.Internal.openPreferences 不可用"), "设置页");
      return;
    }
    internal.openPreferences(PANE_ID);
  } catch (error) {
    report(error, "设置页");
  }
}

const COMMANDS: Record<
  string,
  (window: Window, items: unknown) => void | Promise<void>
> = {
  evidence: runEvidence,
  "compare-add": (window, items) => runCompareAdd(window, items),
  "compare-run": (window) => runCompareRun(window),
  "compare-clear": (window) => runCompareClear(window),
  locate: (window, items) => runLocate(window, items),
  parse: (window, items) => runParse(window, items),
  guide: (window) => openGuide(window),
  "open-graph": (window) => {
    openTab(window, "graph");
  },
  diagnostics: (window) => runDiagnostics(window),
  "open-offline-graph": (window) => {
    // Not registered (see MENU_ENTRIES); kept loud in case a future edit
    // registers it before the service route exists.
    report(new Error("离线图谱入口尚未启用:服务端还没有 exports/evolution.html 的读取路由"), "离线图谱");
    feedback(window, FEEDBACK_KEYS.tabUnavailable);
  },
};

async function runCommand(
  entry: MenuEntry,
  context: any,
): Promise<void> {
  const window = windowOf(context);
  if (!window) {
    report(new Error(`找不到菜单所属窗口(${entry.id})`), "命令");
    return;
  }
  const handler = COMMANDS[entry.command];
  if (!handler) {
    report(new Error(`未知菜单命令 ${entry.command}`), "命令");
    return;
  }
  try {
    await handler(window, context?.items);
  } catch (error) {
    report(error, `命令 ${entry.command}`);
  }
}

/* ── MenuManager wiring ──────────────────────────────────────────────── */

function menuDataFor(entry: MenuEntry): Record<string, unknown> {
  return {
    menuType: "menuitem",
    // MenuManager writes this into data-l10n-id verbatim, and the build
    // prefixes every .ftl key with the addon namespace (ftlId is the one
    // place that knows the prefix).
    l10nID: ftlId(entry.labelKey),
    onShowing: (_event: unknown, context: any): void => {
      liveContexts.set(entry.id, context);
      applyEntryState(entry, context);
      void refreshVerdict();
    },
    onCommand: (_event: unknown, context: any): void => {
      void runCommand(entry, context);
    },
  };
}

function menusFor(spec: MenuSpec): Array<Record<string, unknown>> {
  const items = spec.entries.map((entry) => menuDataFor(ENTRY_BY_ID.get(entry.id)!));
  if (spec.shape === "submenu") {
    const labelKey = spec.labelKey!;
    return [
      {
        menuType: "submenu",
        l10nID: ftlId(labelKey),
        // Same label fallback as the entries: a submenu without a resolved
        // l10nID would render as a blank 「littrace」 row.
        onShowing: (_event: unknown, context: any): void => {
          paintElement(context?.menuElem, t(labelKey));
        },
        menus: items,
      },
    ];
  }
  return items;
}

function registerOne(spec: MenuSpec, api: MenuManagerAPI): MenuHandle | null {
  let result: unknown;
  try {
    result = api.registerMenu({
      menuID: spec.menuID,
      pluginID: config.addonID,
      target: spec.target,
      menus: menusFor(spec),
    });
  } catch (error) {
    report(error, `注册 ${spec.menuID}`);
    return null;
  }
  if (result === false) {
    report(new Error(`Zotero 拒绝了菜单 ${spec.menuID}`), "注册");
    return null;
  }
  return {
    menuID: spec.menuID,
    dispose: () => {
      try {
        api.unregisterMenu(spec.menuID);
      } catch (error) {
        report(error, `注销 ${spec.menuID}`);
      }
    },
  };
}

export interface MenusHandle {
  /** Disable/uninstall: unregister both menus and drop the guide card. */
  dispose(): void;
}

/**
 * Register the menus. Called only from src/registry.ts's
 * `registerWindowIndependent()`, so the registration is process-wide and the
 * returned disposer is owned by the registry.
 */
export function createMenus(): MenusHandle {
  const api = menuManager();
  if (!api) {
    // Zotero without MenuManager (7.x): the manifest already requires 8.0, so
    // this is a real-machine surprise, not an expected state. Report once and
    // keep the rest of the plugin alive.
    report(new Error("Zotero.MenuManager 不可用(需要 Zotero 8+)"), "注册");
    return { dispose() {} };
  }
  const handles: MenuHandle[] = [];
  for (const spec of menuSpecs()) {
    const handle = registerOne(spec, api);
    if (handle) {
      handles.push(handle);
    }
  }
  // One probe at startup so the first menu open usually has a verdict.
  void refreshVerdict(true);

  return {
    dispose() {
      generation += 1;
      verdictCache.clear();
      liveContexts.clear();
      compareEntries = EMPTY_COMPARE_SET;
      for (const handle of handles.splice(0, handles.length)) {
        handle.dispose();
      }
      disposeGuidePanels();
    },
  };
}

/* ── Offline guide card ──────────────────────────────────────────────── */

interface GuideHandle {
  show(): void;
  update(): void;
  dispose(): void;
}

const guides = new Map<Window, GuideHandle>();

function html(doc: Document, className?: string): HTMLDivElement {
  const node = doc.createElementNS(HTML_NS, "div") as HTMLDivElement;
  if (className) {
    node.className = className;
  }
  return node;
}

function xul(doc: Document, tag: string): any {
  const factory = (doc as unknown as { createXULElement?: (name: string) => Element })
    .createXULElement;
  if (typeof factory === "function") {
    return factory.call(doc, tag);
  }
  return doc.createElement(tag);
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

function button(doc: Document, labelKey: string, onClick: () => void): HTMLButtonElement {
  const node = doc.createElementNS(HTML_NS, "button") as HTMLButtonElement;
  node.textContent = t(labelKey);
  node.addEventListener("click", onClick);
  return node;
}

function buildGuide(window: Window): GuideHandle | null {
  const doc = window.document;
  const host = popupsetOf(doc);
  if (!host) {
    report(new Error("未找到可挂载引导卡的文档根节点"), "引导卡");
    return null;
  }
  if (!doc.getElementById(GUIDE_STYLE_ID)) {
    const style = doc.createElementNS(HTML_NS, "style") as HTMLStyleElement;
    style.id = GUIDE_STYLE_ID;
    style.textContent = GUIDE_STYLE;
    (doc.head ?? doc.documentElement).append(style);
  }

  const panel = xul(doc, "panel");
  panel.id = GUIDE_PANEL_ID;
  panel.setAttribute("type", "arrow");
  panel.setAttribute("noautofocus", "true");
  const box = html(doc, "littrace-menu-guide");
  const title = html(doc, "littrace-menu-guide-title");
  const status = html(doc, "littrace-menu-guide-status");
  const body = html(doc, "littrace-menu-guide-body");
  const actions = html(doc, "littrace-menu-guide-actions");
  box.append(title, status, body, actions);
  panel.append(box);
  host.append(panel);

  const actionById = new Map(guideView().actions.map((action) => [action.id, action]));
  const settings = button(doc, actionById.get("settings")!.labelKey, () => {
    openPreferencesPane();
    hide();
  });
  const recheck = button(doc, actionById.get("recheck")!.labelKey, () => {
    recheck.disabled = true;
    status.textContent = t(GUIDE_STATUS_KEYS.checking);
    void refreshVerdict(true).then(() => {
      update();
      recheck.disabled = false;
    });
  });
  const close = button(doc, actionById.get("close")!.labelKey, () => {
    hide();
  });
  actions.append(settings, recheck, close);

  function update(): void {
    const view = guideView({ service: serviceState() });
    title.textContent = t(view.titleKey);
    status.textContent = t(
      GUIDE_STATUS_KEYS[view.service] ?? GUIDE_STATUS_KEYS.offline,
    );
    body.textContent = "";
    for (const key of view.bodyKeys) {
      const line = html(doc, "littrace-menu-guide-line");
      line.textContent = t(key);
      body.append(line);
    }
  }

  function hide(): void {
    try {
      panel.hidePopup?.();
    } catch (error) {
      report(error, "关闭引导卡");
    }
  }

  function show(): void {
    update();
    // Anchor to the host toolbar both this card and U5's dot live next to;
    // the menu element that opened the card is already gone by the time the
    // command runs, so it cannot be the anchor. The document element is the
    // last resort — a visible card beats a swallowed click.
    const anchor =
      doc.getElementById("zotero-tabs-toolbar") ??
      doc.getElementById("zotero-toolbar") ??
      doc.documentElement;
    try {
      panel.openPopup(anchor, "after_end", 0, 0, false, false);
      return;
    } catch (error) {
      report(error, "打开引导卡");
    }
    try {
      panel.openPopupAtScreen(240, 160, false);
    } catch (error) {
      report(error, "打开引导卡");
    }
  }

  return {
    show,
    update,
    dispose() {
      try {
        panel.remove();
      } catch (error) {
        report(error, "移除引导卡");
      }
    },
  };
}

/**
 * Open (or focus) this window's guide card.
 *
 * One card per window: a second click focuses the open one instead of
 * stacking panels, and the card is reclaimed with the menus at shutdown.
 */
export function openGuide(window: Window): void {
  let handle = guides.get(window);
  if (!handle) {
    const built = buildGuide(window);
    if (!built) {
      return;
    }
    handle = built;
    guides.set(window, handle);
  }
  handle.show();
}

/** Drop every guide panel this module created (plugin shutdown). */
export function disposeGuidePanels(): void {
  for (const [window, handle] of Array.from(guides.entries())) {
    guides.delete(window);
    handle.dispose();
  }
  // The popupset may be shared with U5's status dot; remove it only when this
  // feature is its last child.
  for (const window of Zotero.getMainWindows?.() ?? []) {
    try {
      const set = (window as Window).document.getElementById(POPUPSET_ID);
      if (set && set.childElementCount === 0) {
        set.remove();
      }
    } catch (error) {
      report(error, "清理 popupset");
    }
  }
}

/** Test/real-machine hook: the current comparison set, in paper ids. */
export function comparisonSet(): ReadonlyArray<string> {
  return Object.freeze(compareEntries.map(paperIdOf));
}
