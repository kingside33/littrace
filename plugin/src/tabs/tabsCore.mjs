/**
 * Pure logic for the four iframe function tabs and the host bridge (U4).
 *
 * Deliberately plain ESM with no Zotero, DOM or TypeScript globals, so
 * tests/test_plugin_tabs.cjs can exercise the real hash routing, the tab
 * registry, the cleanup ledger and the deep-link decision with
 * `node --test`, no npm install and no Zotero process. src/tabs/tabs.ts
 * only turns these values into DOM nodes and Zotero calls.
 *
 * Three boundaries on purpose:
 *
 *   1. **Zero server changes.** Each tab loads the service page as-is; the
 *      hash is the service shell's own initial route. The plugin injects no
 *      page code at all.
 *   2. **The open mechanism is a switch, not a bet.** `Zotero_Tabs.add` is
 *      an internal, undocumented mechanism whose real shape is decided on a
 *      real machine (M0 item ⑦). The dialog fallback therefore ships in the
 *      same change and the selection is a pure function with one constant
 *      as its switch point.
 *   3. **Register is record, clear is empty.** iframe nodes, listeners and
 *      timers all go into one cleanup table; teardown drops the table first
 *      and then runs the disposers, so a throwing disposer can neither leave
 *      stale entries behind nor stop the rest.
 */

/**
 * Tab registry: hash → title key → content builder.
 *
 * Frozen plain data on purpose: the table is the contract (order, routes,
 * titles) and must be assertable without a DOM. `hash` is the route name the
 * service shell understands (`#search|#graph|#research|#approvals`), and
 * `builder` names the constructor table in src/tabs/tabs.ts; both are
 * checked against TAB_BUILDERS below by tests.
 *
 * Order is the tab order, and follows the issue's enumeration: 证据检索,
 * 研究谱系, 研究调度台, 全文审批.
 */
export const TAB_REGISTRY = Object.freeze([
  Object.freeze({
    id: "search",
    hash: "search",
    titleKey: "tab-title-search",
    builder: "iframe",
  }),
  Object.freeze({
    id: "graph",
    hash: "graph",
    titleKey: "tab-title-graph",
    builder: "iframe",
  }),
  Object.freeze({
    id: "research",
    hash: "research",
    titleKey: "tab-title-research",
    builder: "iframe",
  }),
  Object.freeze({
    id: "approvals",
    hash: "approvals",
    titleKey: "tab-title-approvals",
    builder: "iframe",
  }),
]);

/** The four tab ids, in registry order. Derived, never a second list. */
export const TAB_IDS = Object.freeze(TAB_REGISTRY.map((tab) => tab.id));

/** Content builders src/tabs/tabs.ts can dispatch to. */
export const TAB_BUILDERS = Object.freeze(["iframe"]);

/** One registry entry by id, or null for an unknown id. */
export function tabById(id) {
  if (typeof id !== "string") {
    return null;
  }
  return TAB_REGISTRY.find((tab) => tab.id === id) ?? null;
}

/**
 * Resolve `location.hash` (with or without the leading `#`) to a tab id.
 * Anything that is not one of the four routes — e.g. the service's own
 * `#settings` or `#chains` pages — resolves to null: this bridge only maps
 * the four function tabs.
 */
export function tabIdForHash(hash) {
  if (typeof hash !== "string") {
    return null;
  }
  const name = hash.trim().replace(/^#/, "").split(/[?&]/)[0];
  if (!name) {
    return null;
  }
  return TAB_REGISTRY.find((tab) => tab.hash === name)?.id ?? null;
}

/** The hash a tab opens at (`#graph`), or null for an unknown id. */
export function hashForTab(id) {
  const tab = tabById(id);
  return tab ? `#${tab.hash}` : null;
}

/**
 * Build the iframe src for one tab.
 *
 * `?paper=` is a QUERY parameter, never part of the hash: the service shell
 * reads `location.search` for `paper` and `location.hash` for the page, so
 * the only shape that carries both a page and a deep link is
 * `/?paper=KEY#graph`.
 *
 * Returns null when the id is unknown or the base URL is empty; a missing
 * base URL means the caller has no configured service address and must not
 * silently open `undefined/#graph`.
 */
export function tabUrl(id, options = {}) {
  const tab = tabById(id);
  if (!tab) {
    return null;
  }
  const base =
    typeof options.baseUrl === "string"
      ? options.baseUrl.trim().replace(/\/+$/, "")
      : "";
  if (!base) {
    return null;
  }
  const params = new URLSearchParams();
  const paper = typeof options.paper === "string" ? options.paper.trim() : "";
  if (paper) {
    params.set("paper", paper);
  }
  const query = params.toString();
  return `${base}/${query ? `?${query}` : ""}#${tab.hash}`;
}

/**
 * The DOM-independent description of a tab's content. `src/tabs/tabs.ts`
 * creates the node; tests assert the destination without a DOM.
 */
export function tabNodeSpec(id, options = {}) {
  const tab = tabById(id);
  const src = tabUrl(id, options);
  if (!tab || !src) {
    return null;
  }
  if (tab.builder === "iframe") {
    return Object.freeze({
      kind: "iframe",
      tabId: tab.id,
      src,
      titleKey: tab.titleKey,
    });
  }
  throw new Error(`littrace: 未知 tab 构造器 ${String(tab.builder)}`);
}

/**
 * One cleanup table per window: every resource the tab host owns is
 * registered here with the function that releases it.
 *
 * Semantics (all covered by tests/test_plugin_tabs.cjs):
 *   - `add(id, dispose)` records immediately; duplicate ids are an error,
 *     so one resource cannot silently overwrite another's disposal;
 *   - `remove(id)` releases one entry and drops it from the table;
 *   - `clear()` drops the whole table FIRST, then runs every disposer in
 *     reverse registration order — "清理即清空" holds even when a disposer
 *     throws, and one failure cannot stop the rest;
 *   - errors go to `onError`, never rethrown: teardown must run to the end.
 */
export function createCleanupLedger(onError) {
  const report = typeof onError === "function" ? onError : () => {};
  /** @type {Map<string, () => void>} */
  const entries = new Map();

  function add(id, dispose) {
    if (typeof id !== "string" || id.length === 0) {
      throw new Error("littrace: 清理表条目标识必须是非空字符串");
    }
    if (typeof dispose !== "function") {
      throw new Error(`littrace: 清理表条目 "${id}" 必须提供清理函数`);
    }
    if (entries.has(id)) {
      throw new Error(`littrace: 清理表条目重复 "${id}"`);
    }
    entries.set(id, dispose);
    return id;
  }

  function remove(id) {
    const dispose = entries.get(id);
    if (!dispose) {
      return false;
    }
    entries.delete(id);
    try {
      dispose();
    } catch (error) {
      report(error, id);
    }
    return true;
  }

  function clear() {
    const pending = Array.from(entries.entries()).reverse();
    entries.clear();
    for (const [id, dispose] of pending) {
      try {
        dispose();
      } catch (error) {
        report(error, id);
      }
    }
    return pending.length;
  }

  return {
    add,
    remove,
    clear,
    has(id) {
      return entries.has(id);
    },
    get size() {
      return entries.size;
    },
    ids() {
      return Array.from(entries.keys());
    },
  };
}

/** Protocol prefixes the host bridge intercepts; every other link is left alone. */
export const DEEP_LINK_PREFIXES = Object.freeze(["zotero:", "obsidian:"]);

/**
 * Whether a clicked href is a deep link the host must hand to Zotero instead
 * of letting the iframe navigate. Matching is case-insensitive because the
 * scheme is case-insensitive in URLs.
 */
export function isDeepLink(href) {
  if (typeof href !== "string") {
    return false;
  }
  const value = href.trim().toLowerCase();
  return DEEP_LINK_PREFIXES.some((prefix) => value.startsWith(prefix));
}

/** The two bridge paths: capture in the host, or leave the native anchor alone. */
export const BRIDGE_MODES = Object.freeze(["capture", "native"]);

/**
 * Default: capture. If the privileged host turns out to have no access to
 * the cross-origin iframe document (M0 item ③), switching this to "native"
 * is the accepted degradation: the anchors navigate, the system handles the
 * protocol, and the plugin installs no listener at all.
 */
export const DEFAULT_BRIDGE_MODE = "capture";

/** Resolve the bridge switch into an explicit plan; unknown values throw. */
export function bridgePlan(mode) {
  if (!BRIDGE_MODES.includes(mode)) {
    throw new Error(`littrace: 未知深链桥接模式 ${String(mode)}`);
  }
  return Object.freeze({ mode, capture: mode === "capture" });
}

/** How a function tab is opened. */
export const OPEN_MECHANISMS = Object.freeze(["zotero-tabs", "dialog"]);

/**
 * Default: the internal tab deck. `Zotero_Tabs.add` is not a public API
 * (M0 item ⑦); the dialog fallback is the documented escape hatch, and the
 * selection below is the only place the choice is made.
 */
export const DEFAULT_OPEN_MECHANISM = "zotero-tabs";

/**
 * Decide how to open a tab.
 *
 * - preferred "zotero-tabs" + available → the tab deck;
 * - preferred "zotero-tabs" + unavailable → the dialog, with `fallback: true`
 *   and a reason the caller logs (a fallback must be visible, never silent);
 * - preferred "dialog" → the dialog, `fallback: false` (that is the switch,
 *   not a degradation);
 * - unknown preferred → throw: a typo must not silently pick an engine.
 */
export function resolveOpenPlan(preferred, options = {}) {
  if (!OPEN_MECHANISMS.includes(preferred)) {
    throw new Error(`littrace: 未知 tab 打开机制 ${String(preferred)}`);
  }
  if (preferred === "zotero-tabs" && options.zoteroTabsAvailable !== true) {
    return Object.freeze({
      mechanism: "dialog",
      preferred,
      fallback: true,
      reason: "Zotero_Tabs 不可用",
    });
  }
  return Object.freeze({
    mechanism: preferred,
    preferred,
    fallback: false,
    reason: "",
  });
}

/**
 * Whether one open tab can be reused for a new target, or its host is gone
 * and must be reopened.
 *
 * The signals are raw and independent so the decision is testable without a
 * DOM:
 *   - `hostClosed`      the hosting tab or window announced that it closed;
 *   - `iframeConnected` the iframe node is no longer in its document.
 *
 * Either one true means the recorded entry is stale: reusing it would retarget
 * a dead node and report success while nothing is on screen. An UNKNOWN
 * signal (undefined) counts as alive — a false "reopen" spawns duplicate
 * windows, which is the worse failure.
 */
export function reuseVerdict(input = {}) {
  if (!input || typeof input !== "object") {
    return "reuse";
  }
  if (input.hostClosed === true) {
    return "reopen";
  }
  if (input.iframeConnected === false) {
    return "reopen";
  }
  return "reuse";
}

/**
 * Why a locate payload could not be turned into a tab target, or null when
 * it can. Kept separate from locateRequestOf so the caller can log the
 * refusal: silently ignoring a click is exactly the "点了没反应" state this
 * repository treats as a defect.
 */
export function locateRefusalOf(detail) {
  if (locateRequestOf(detail)) {
    return null;
  }
  if (!detail || typeof detail !== "object") {
    return "定位事件缺少载荷";
  }
  const itemKey =
    typeof detail.itemKey === "string" ? detail.itemKey.trim() : "";
  if (!itemKey) {
    return "定位事件缺少条目 key";
  }
  return `定位事件缺少可用的 libraryID(条目 ${itemKey}),无法拼出文献标识`;
}

/**
 * Turn a `littrace:lineage-locate` payload (A2 column) into a tab target.
 *
 * The payload's `itemKey` is a **Zotero item key** (e.g. `ABCD1234`), not a
 * littrace paper id. The service resolves `?paper=` through
 * `Store.resolve` (kbapp/storage/store.py:155-167), which follows the aliases
 * table (paper id / citekey / openalex_id / doi) and otherwise returns the
 * raw string; a bare Zotero key is in no alias, so `paper_view` raises
 * 「文献不存在」 (kbapp/web/library_api.py:9-16) and `graph_page` resolves
 * nothing. littrace's paper id for a Zotero item is
 * `z:<libraryID>:<zoteroKey>` (kbapp/library/sources.py:34-40), so that is
 * what this builds, and nothing else is accepted.
 *
 * A payload without a usable `libraryID` is refused rather than guessed: the
 * service defaults the missing library to 1, but guessing would silently
 * open a different paper for group libraries, and opening an unfiltered graph
 * would look as if the locate had worked. The caller reports the refusal;
 * see locateRefusalOf().
 */
export function locateRequestOf(detail) {
  if (!detail || typeof detail !== "object") {
    return null;
  }
  const itemKey =
    typeof detail.itemKey === "string" ? detail.itemKey.trim() : "";
  if (!itemKey) {
    return null;
  }
  // A2 sends libraryID as a number or null; a numeric string is accepted,
  // anything non-integral or negative is not a library id.
  const rawLibrary = detail.libraryID;
  const libraryID =
    typeof rawLibrary === "number"
      ? rawLibrary
      : typeof rawLibrary === "string" && rawLibrary.trim()
        ? Number(rawLibrary.trim())
        : NaN;
  if (!Number.isInteger(libraryID) || libraryID < 0) {
    return null;
  }
  return Object.freeze({
    tab: "graph",
    paper: `z:${libraryID}:${itemKey}`,
  });
}

/**
 * The tab id from whatever `Zotero_Tabs.add` returned.
 *
 * 真机定案 (Zotero 10.0.8, `chrome/content/zotero/tabs.js`): `add()` returns
 * `{ id, container }`, not a bare id string. The string form is still
 * accepted so one shape change cannot break the primary path silently.
 * Returns null when neither shape carries a usable id.
 */
export function readTabId(result) {
  if (typeof result === "string") {
    return result.length > 0 ? result : null;
  }
  if (result === null || typeof result !== "object") {
    return null;
  }
  const id = result.id;
  return typeof id === "string" && id.length > 0 ? id : null;
}

/**
 * Whether a `Zotero_Tabs.add` result already carries the deck container.
 *
 * 真机定案: Zotero 10 creates the XUL `<tab-content>` itself, appends it to
 * the deck and hands it back, so the caller does not have to find it by id.
 * Duck-typed on `append` because the node comes from Zotero's realm, where
 * the plugin sandbox's `Element` constructor does not match.
 */
export function hasResultContainer(result) {
  if (result === null || typeof result !== "object") {
    return false;
  }
  const container = result.container;
  return (
    container !== null &&
    typeof container === "object" &&
    typeof container.append === "function"
  );
}

/**
 * The `data` payload a tab record needs.
 *
 * 真机定案: `Zotero_Tabs.add` stores `data` verbatim on the tab record, and
 * Zotero's own tab renderer dereferences it (`tab.data.icon`). Omitting it
 * leaves `tab.data === undefined` and that renderer throws. Whatever the
 * caller does or does not know, the record must therefore always get an
 * object. Caller-supplied fields win; the result is a fresh plain object.
 */
export function tabDataPayload(extra) {
  const base = {};
  if (extra !== null && typeof extra === "object" && !Array.isArray(extra)) {
    for (const [key, value] of Object.entries(extra)) {
      base[key] = value;
    }
  }
  return base;
}
