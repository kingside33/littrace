import { config } from "../../package.json";
import { SERVICE_BASE_URL } from "../service-client";
import { t } from "../utils/locale";
import { listenLineageLocate } from "../columns/lineageCore.mjs";
import {
  DEFAULT_BRIDGE_MODE,
  DEFAULT_OPEN_MECHANISM,
  bridgePlan,
  createCleanupLedger,
  isDeepLink,
  locateRequestOf,
  resolveOpenPlan,
  tabById,
  tabNodeSpec,
} from "./tabsCore.mjs";
import type {
  CleanupLedger,
  TabEntry,
  TabNodeSpec,
} from "./tabsCore.mjs";

/**
 * The four iframe function tabs and the host bridge (U4).
 *
 * Every decision that can be made without Zotero lives in tabsCore.mjs and
 * is covered by tests/test_plugin_tabs.cjs: the tab registry, the hash
 * routes, the iframe node spec, the cleanup ledger, the deep-link predicate
 * and the two switch tables (open mechanism, bridge mode). This file is the
 * thin edge that turns those values into DOM and Zotero calls, and it is
 * split the same way the other features are: nothing below chooses *what* a
 * tab is, it only mounts it.
 *
 * ── The two switch points ───────────────────────────────────────────────
 *
 * OPEN_MECHANISM — how a tab is opened. `Zotero_Tabs.add` is an internal,
 * undocumented mechanism (M0 item ⑦: "非公开机制, 退路 = window.openDialog"),
 * so the fallback ships in the same change and this constant is the only
 * edit needed to change the bet.
 *
 * BRIDGE_MODE — the deep-link bridge. "capture" injects a capture-phase
 * listener into the iframe document (cross-origin DOM access is unverified:
 * M0 item ③); "native" injects nothing and lets the iframe's own
 * `<a href="zotero:…">` navigate so the system handles the protocol. When
 * capture injection is refused at runtime, the code reports it and leaves the
 * native anchors in charge — the same degradation, chosen per frame.
 *
 * ── Lifetime ────────────────────────────────────────────────────────────
 *
 * One host per main window, created by registerAll and disposed by
 * register(win, "iframe-tabs", …). Every resource the host owns — the
 * iframe node, the load listener, the injected capture listener, the focus
 * timer, the lineage listener — is registered in one CleanupLedger.
 * onMainWindowUnload and plugin shutdown both end in ledger.clear(), which
 * empties the table before running the disposers.
 */

/** THE switch point for how a tab is opened. */
const OPEN_MECHANISM = DEFAULT_OPEN_MECHANISM;

/** THE switch point for the deep-link bridge. */
const BRIDGE_MODE = DEFAULT_BRIDGE_MODE;

/** Shape of the internal Zotero_Tabs API this file touches (真机定案). */
interface ZoteroTabsAPI {
  add(options: Record<string, unknown>): unknown;
  close?(tabID: string): unknown;
  select?(tabID: string): unknown;
  _tabs?: Array<{ id?: string; container?: unknown }>;
}

/** One tab this host opened, with the two actions its host offers. */
interface OpenedTab {
  iframe: HTMLIFrameElement;
  /** Bring the hosting tab or window to the front; best effort. */
  focusHost: () => void;
  /** Release the hosting tab or window; must not call back into this host. */
  closeHost: () => void;
}

export interface TabHost {
  /** Open (or retarget) one function tab. False when it could not be opened. */
  open(id: string, options?: { paper?: string }): boolean;
  /** Reclaim every tab and every registered listener/timer for this window. */
  dispose(): void;
}

/** Hosts are keyed by main window: tabs are window-level UI. */
const hosts = new Map<Window, TabHost>();

/** Report a contract violation to the Zotero console instead of failing quietly. */
function report(error: unknown, context: string): void {
  Zotero.logError(
    error instanceof Error
      ? new Error(`littrace: ${context}: ${error.message}`)
      : new Error(`littrace: ${context}: ${String(error)}`),
  );
}

/** The window's Zotero_Tabs API, or null when this build does not expose it. */
function zoteroTabsOf(win: Window): ZoteroTabsAPI | null {
  const fromWindow = (win as unknown as { Zotero_Tabs?: ZoteroTabsAPI })
    .Zotero_Tabs;
  if (fromWindow && typeof fromWindow.add === "function") {
    return fromWindow;
  }
  const fromGlobal = (Zotero as { Zotero_Tabs?: ZoteroTabsAPI }).Zotero_Tabs;
  if (fromGlobal && typeof fromGlobal.add === "function") {
    return fromGlobal;
  }
  return null;
}

/**
 * Create (or reuse) the tab host for one main window. Called only from
 * src/registry.ts's registerAll, so a host never exists outside the plugin
 * registry and can never leak past unregisterWindow/unregisterAll.
 */
export function createTabHost(win: Window): TabHost {
  const existing = hosts.get(win);
  if (existing) {
    return existing;
  }
  const host = buildHost(win);
  hosts.set(win, host);
  return host;
}

/**
 * Reserved entry for the tools menu (U6). It refuses to create a host on
 * demand: a host built outside registerAll would not be reclaimed at window
 * unload or plugin disable.
 */
export function openFunctionTab(
  win: Window,
  id: string,
  options: { paper?: string } = {},
): boolean {
  const host = hosts.get(win);
  if (!host) {
    report(new Error(`窗口未注册 tab 宿主,无法打开 "${id}"`), "打开 tab");
    return false;
  }
  return host.open(id, options);
}

function buildHost(win: Window): TabHost {
  const ledger: CleanupLedger = createCleanupLedger((error, id) => {
    report(error, `清理 "${id}"`);
  });
  const opened = new Map<string, OpenedTab>();

  // A2's 「谱系」column dispatches this event on the item tree's document and
  // it bubbles to the window, so one listener per window is enough. This is
  // the entry the four tabs have until the tools menu lands (U6).
  ledger.add(
    "lineage-locate",
    listenLineageLocate(win, (detail: unknown) => {
      const request = locateRequestOf(detail);
      if (request) {
        openTab(win, request.tab, { paper: request.paper });
      }
    }),
  );

  function openTab(
    window: Window,
    id: string,
    options: { paper?: string } = {},
  ): boolean {
    const entry = tabById(id);
    if (!entry) {
      report(new Error(`未知 tab "${id}"`), "打开 tab");
      return false;
    }
    const spec = tabNodeSpec(id, {
      baseUrl: SERVICE_BASE_URL,
      paper: options.paper,
    });
    if (!spec) {
      report(new Error(`tab "${id}" 的 iframe 地址构造失败`), "打开 tab");
      return false;
    }

    const existing = opened.get(id);
    if (existing) {
      // Already open: retarget the same iframe at the new deep link instead
      // of opening a second one, then bring its host to the front.
      if (existing.iframe.src !== spec.src) {
        existing.iframe.src = spec.src;
      }
      existing.focusHost();
      return true;
    }

    const plan = resolveOpenPlan(OPEN_MECHANISM, {
      zoteroTabsAvailable: zoteroTabsOf(window) !== null,
    });

    if (plan.mechanism === "zotero-tabs") {
      const mounted = mountInZoteroTab(window, entry, spec, () => {
        closeOpened(entry.id, { hostAlreadyClosing: true });
      });
      if (mounted) {
        adopt(entry.id, mounted);
        return true;
      }
      // Never leave a blank tab and never pretend it worked: log the
      // degradation and use the fallback for this call.
      report(
        new Error(`${plan.preferred} 路径未能挂载,改用独立窗口`),
        "打开 tab",
      );
    }

    try {
      adopt(entry.id, mountInDialog(window, entry, spec));
      return true;
    } catch (error) {
      report(error, "打开 tab");
      return false;
    }
  }

  /** Register every resource one mounted tab owns in the cleanup table. */
  function adopt(id: string, mounted: OpenedTab): void {
    opened.set(id, mounted);
    // The iframe node owns its document, so removing it also ends the page,
    // its own listeners and any timer it created.
    ledger.add(`iframe:${id}`, () => {
      mounted.iframe.remove();
    });
    registerDeepLinkBridge(id, mounted);
    // Focus moves into the iframe document once its tab is selected. The
    // exact API shape is M0's; the timer is registered so a closed tab can
    // never fire it later.
    const timer = win.setTimeout(() => {
      try {
        mounted.iframe.focus();
        // Focus belongs in the frame's own document once the tab is active;
        // the exact API shape is M0's, so both calls are guarded.
        mounted.iframe.contentWindow?.focus?.();
      } catch {
        // 已关闭或跨源拒绝: 焦点转移失败不致命。
      }
    }, 0);
    ledger.add(`focus:${id}`, () => {
      win.clearTimeout(timer);
    });
  }

  /**
   * Inject the capture-phase deep-link bridge, once per loaded document:
   * the `load` handler detaches the previous document's listener before
   * injecting into the new one, so a page navigation cannot stack listeners.
   */
  function registerDeepLinkBridge(id: string, mounted: OpenedTab): void {
    if (!bridgePlan(BRIDGE_MODE).capture) {
      // native: install nothing. The iframe's own <a href="zotero:…"> lets
      // the system handle the protocol; that is the accepted degradation.
      return;
    }
    let detach: (() => void) | null = null;
    const onLoad = (): void => {
      detach?.();
      detach = injectCapture(mounted, { reportRefusal: true });
    };
    mounted.iframe.addEventListener("load", onLoad);
    // The initial about:blank document may already be reachable; injecting
    // now is harmless because the first load detaches it again. An
    // unreachable document at this point is expected (the real page has not
    // committed yet), so only the load-time attempt reports.
    detach = injectCapture(mounted, { reportRefusal: false });
    ledger.add(`deep-link:${id}`, () => {
      mounted.iframe.removeEventListener("load", onLoad);
      detach?.();
      detach = null;
    });
  }

  /** Attach the capture listener to one iframe document; null when refused. */
  function injectCapture(
    mounted: OpenedTab,
    options: { reportRefusal: boolean },
  ): (() => void) | null {
    let found: Document | null = null;
    try {
      found = mounted.iframe.contentDocument;
    } catch (error) {
      if (options.reportRefusal) {
        report(error, "深链桥接注入");
      }
      return null;
    }
    if (!found) {
      // Privileged access to a cross-origin frame is M0 item ③. Refused
      // access is not hidden: report it and leave the native anchors in
      // charge, never install a half bridge.
      if (options.reportRefusal) {
        report(
          new Error("iframe contentDocument 不可达,深链退回原生链接"),
          "深链桥接注入",
        );
      }
      return null;
    }
    const doc: Document = found;
    const onClick = (event: Event): void => {
      const anchor = anchorOf(event.target);
      if (!anchor) {
        return;
      }
      const href = anchor.getAttribute("href") ?? "";
      if (!isDeepLink(href)) {
        return;
      }
      if (!canLaunchExternal()) {
        // No privileged launch API: let the anchor navigate natively rather
        // than swallowing the click.
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      launchExternal(href);
    };
    doc.addEventListener("click", onClick, true);
    return () => {
      try {
        doc.removeEventListener("click", onClick, true);
      } catch {
        // 文档已销毁: 没有监听可移除。
      }
    };
  }

  /** Close one opened tab and drop every resource it registered. */
  function closeOpened(
    id: string,
    options: { hostAlreadyClosing?: boolean } = {},
  ): void {
    const target = opened.get(id);
    if (!target) {
      return;
    }
    opened.delete(id);
    ledger.remove(`deep-link:${id}`);
    ledger.remove(`focus:${id}`);
    ledger.remove(`iframe:${id}`);
    if (!options.hostAlreadyClosing) {
      target.closeHost();
    }
  }

  const host: TabHost = {
    open(id, options) {
      return openTab(win, id, options);
    },
    dispose() {
      for (const id of Array.from(opened.keys())) {
        closeOpened(id);
      }
      // Anything left (the lineage listener) is reclaimed here. clear()
      // empties the table before running disposers, so a throwing disposer
      // neither blocks the rest nor leaves a stale entry.
      ledger.clear();
      hosts.delete(win);
    },
  };
  return host;
}

/**
 * Mount one tab through Zotero_Tabs.add. All the non-public shapes live in
 * this one function (M0 item ⑦); returning null means "use the fallback",
 * and no blank tab is left behind on the way out.
 */
function mountInZoteroTab(
  window: Window,
  entry: TabEntry,
  spec: TabNodeSpec,
  onHostClose: () => void,
): OpenedTab | null {
  const tabs = zoteroTabsOf(window);
  if (!tabs) {
    return null;
  }
  let result: unknown;
  try {
    // 待真机定案: 选项名、返回值与容器取法都是非公开机制。
    result = tabs.add({
      type: `${config.addonRef}-${entry.id}`,
      title: t(entry.titleKey),
      select: true,
      onClose: onHostClose,
    });
  } catch (error) {
    report(error, "Zotero_Tabs.add");
    return null;
  }
  const tabID = typeof result === "string" && result.length > 0 ? result : null;
  const container = tabID ? findTabContainer(window, tabs, tabID) : null;
  if (!tabID || !container) {
    if (tabID) {
      closeZoteroTab(tabs, tabID);
    }
    report(new Error("Zotero_Tabs.add 未给出可挂载的容器"), "Zotero_Tabs");
    return null;
  }
  try {
    const iframe = createIframe(window, entry, spec);
    container.append(iframe);
    return {
      iframe,
      focusHost: () => {
        try {
          tabs.select?.(tabID);
        } catch {
          // 已关闭或 API 形态不同: 聚焦失败不致命。
        }
      },
      closeHost: () => {
        closeZoteroTab(tabs, tabID);
      },
    };
  } catch (error) {
    closeZoteroTab(tabs, tabID);
    report(error, "挂载 Zotero tab");
    return null;
  }
}

/**
 * Locate the content container of a tab created through Zotero_Tabs.add.
 * 待真机定案: the id convention is the tab deck's own; the `_tabs` record is
 * the fallback for the same internal mechanism. Returning null makes the
 * caller switch to the dialog instead of throwing.
 */
function findTabContainer(
  window: Window,
  tabs: ZoteroTabsAPI,
  tabID: string,
): HTMLElement | null {
  const byID = window.document.getElementById(`tab-container-${tabID}`);
  if (byID) {
    return byID;
  }
  const records = Array.isArray(tabs._tabs) ? tabs._tabs : [];
  const record = records.find((item) => item && item.id === tabID);
  const container = record?.container as HTMLElement | undefined;
  if (
    container &&
    typeof (container as unknown as Element).append === "function"
  ) {
    return container;
  }
  return null;
}

/** Close one Zotero tab; failures are reported, never thrown into teardown. */
function closeZoteroTab(tabs: ZoteroTabsAPI, tabID: string): void {
  try {
    tabs.close?.(tabID);
  } catch (error) {
    report(error, "关闭 Zotero tab");
  }
}

/**
 * The fallback: a standalone chrome window (`window.openDialog`) that hosts
 * the same iframe. The document is ours, so no Zotero internal is touched;
 * closing the window is the whole cleanup.
 */
function mountInDialog(
  window: Window,
  entry: TabEntry,
  spec: TabNodeSpec,
): OpenedTab {
  const chromeWindow = window as unknown as {
    openDialog?: (url: string, name: string, features: string) => Window | null;
  };
  if (typeof chromeWindow.openDialog !== "function") {
    throw new Error("当前窗口没有 openDialog,独立窗口退路不可用");
  }
  const dialog = chromeWindow.openDialog(
    "about:blank",
    `${config.addonRef}-tab-${entry.id}`,
    "chrome,dialog=no,resizable,width=1100,height=800",
  );
  if (!dialog) {
    throw new Error(`独立窗口 "${entry.id}" 打开失败`);
  }
  const doc = dialog.document;
  if (!doc || !doc.body) {
    try {
      dialog.close();
    } catch {
      // 已经关闭。
    }
    throw new Error(`独立窗口 "${entry.id}" 没有可用文档`);
  }
  doc.title = t(entry.titleKey);
  const style = doc.createElement("style");
  style.textContent =
    "html,body{margin:0;height:100%;}body{display:flex;}" +
    "iframe{flex:1;border:0;width:100%;height:100%;}";
  doc.head?.append(style);
  const iframe = createIframe(dialog, entry, spec);
  doc.body.append(iframe);
  return {
    iframe,
    focusHost: () => {
      try {
        dialog.focus();
      } catch {
        // 已关闭。
      }
    },
    closeHost: () => {
      try {
        dialog.close();
      } catch (error) {
        report(error, "关闭独立窗口");
      }
    },
  };
}

/** The iframe itself. The service page owns everything inside the frame. */
function createIframe(
  owner: Window,
  entry: TabEntry,
  spec: TabNodeSpec,
): HTMLIFrameElement {
  const iframe = owner.document.createElement("iframe");
  iframe.src = spec.src;
  iframe.title = t(entry.titleKey);
  iframe.dataset.littraceTab = entry.id;
  iframe.style.border = "0";
  iframe.style.width = "100%";
  iframe.style.height = "100%";
  iframe.style.display = "block";
  return iframe;
}

/**
 * The clicked anchor, if any. Duck-typed instead of `instanceof Element`
 * because the node comes from the iframe's realm, where the plugin sandbox's
 * `Element` constructor does not match.
 */
function anchorOf(target: EventTarget | null): Element | null {
  const element = target as {
    closest?: (selector: string) => Element | null;
  } | null;
  if (!element || typeof element.closest !== "function") {
    return null;
  }
  return element.closest("a[href]");
}

/** Whether this build exposes the privileged external-link launcher. */
function canLaunchExternal(): boolean {
  const api = Zotero as { launchURL?: unknown };
  return typeof api.launchURL === "function";
}

/** Hand one deep link to Zotero. 待真机定案 (M0 ④): the launchURL shape. */
function launchExternal(href: string): void {
  try {
    Zotero.launchURL(href);
  } catch (error) {
    // The click was already consumed; the accepted recovery is switching
    // BRIDGE_MODE to "native", which is exactly why that switch exists.
    report(error, `打开外部链接 ${href}`);
  }
}
