/**
 * Pure logic for the 「谱系」 item-tree column (A2 原型).
 *
 * Deliberately plain ESM with no Zotero, DOM or TypeScript globals, so
 * tests/test_plugin_column.cjs can exercise the real badge mapping, the
 * dataProvider text contract and the click-event contract with
 * `node --test` and no npm install. src/columns/lineage.ts wraps this with
 * the Zotero-facing registration and with the DOM renderer.
 *
 * Facts this module is built on (Zotero 10 main, read for A2):
 *   - `Zotero.ItemTreeManager.registerColumn` (pluginAPI/itemTreeManager.js)
 *     returns the *namespaced* dataKey (`${pluginID}-${dataKey}`), and
 *     `unregisterColumn` only accepts that returned value — the raw dataKey
 *     is not in `_optionsCache`. See src/columns/lineage.ts.
 *   - Unknown option keys make `registerColumn` return false
 *     (pluginAPI/pluginAPIBase.mjs `_validateObject`: "Unknown option ..."),
 *     so the option object may only carry documented keys.
 *   - Sorting and export read the plain-text `dataProvider` result
 *     (itemTreeRow.js `getField` -> `getCustomCellData`), and Zotero compares
 *     column values as strings (`itemTree.jsx` `_compareField` ->
 *     `compareString`), including for the built-in numeric columns such as
 *     `numNotes`. The text below is therefore readable and sortable by that
 *     same string semantics.
 *   - A custom column without `defaultIn` starts hidden and is enabled from
 *     the column picker (`itemTree.jsx` `_getColumns`: "Initial hidden
 *     value"), which is the default-off behaviour this column wants.
 */

/** The three badge states the UI contract defines. `empty` renders nothing. */
export const LINEAGE_STATES = Object.freeze(["verified", "pending", "gap"]);

/** Name of the click event reserved for the future 谱系 tab (U4). */
export const LINEAGE_LOCATE_EVENT = "littrace:lineage-locate";

/** Raw dataKey; Zotero namespaces it with the plugin ID on registration. */
export const LINEAGE_COLUMN_DATA_KEY = "lineage";

/**
 * Badge descriptors for the three states. `symbol` is the visual glyph,
 * `label` the short Chinese state name used in titles.
 */
const STATE_BADGES = Object.freeze({
  verified: Object.freeze({
    state: "verified",
    symbol: "●",
    label: "已确认",
    className: "littrace-lineage-verified",
    color: "rgba(46, 125, 50, 0.85)",
  }),
  pending: Object.freeze({
    state: "pending",
    symbol: "○",
    label: "待核",
    className: "littrace-lineage-pending",
    color: "rgba(107, 114, 128, 0.85)",
  }),
  gap: Object.freeze({
    state: "gap",
    symbol: "⚠",
    label: "含缺口",
    className: "littrace-lineage-gap",
    color: "rgba(180, 83, 9, 0.85)",
  }),
});

/**
 * Deterministic fake fixtures (A2 uses fake data on purpose). The hash below
 * picks one by item key, so the same item always shows the same badge while
 * a library shows all four outcomes (verified / pending / gap / empty).
 */
const FAKE_FIXTURES = Object.freeze([
  Object.freeze({ confirmed: 5, pending: 0, gaps: 0 }),
  Object.freeze({ confirmed: 2, pending: 2, gaps: 0 }),
  Object.freeze({ confirmed: 1, pending: 0, gaps: 1 }),
  Object.freeze({ confirmed: 0, pending: 0, gaps: 0 }),
  Object.freeze({ confirmed: 3, pending: 1, gaps: 0 }),
]);

/** Copy out a plain counts object; every counter is a non-negative integer. */
function zeroCounts() {
  return { confirmed: 0, pending: 0, gaps: 0 };
}

/** Coerce one counter: anything that is not a finite positive number is 0. */
export function toCount(value) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number) || number <= 0) {
    return 0;
  }
  return Math.floor(number);
}

/**
 * Defensive normalization of a lineage record. Never throws, for any input:
 * the dataProvider contract is "always a string", so a malformed record must
 * degrade to the empty state rather than break the item list.
 */
export function normalizeCounts(record) {
  if (!record || typeof record !== "object") {
    return zeroCounts();
  }
  return {
    confirmed: toCount(record.confirmed),
    pending: toCount(record.pending),
    gaps: toCount(record.gaps),
  };
}

/**
 * The three-state mapping. Precedence is fixed and matters: any gap wins,
 * then any pending, then any confirmed; a record with no relation at all is
 * `empty` and renders nothing.
 */
export function badgeState(counts) {
  const { confirmed, pending, gaps } = normalizeCounts(counts);
  if (gaps > 0) {
    return "gap";
  }
  if (pending > 0) {
    return "pending";
  }
  if (confirmed > 0) {
    return "verified";
  }
  return "empty";
}

/** Descriptor for `verified` / `pending` / `gap`; null for `empty`. */
export function stateBadge(state) {
  const badge = STATE_BADGES[state];
  return badge ? { ...badge } : null;
}

/** Badge descriptor for a counts record (null when there is nothing to show). */
export function badgeForCounts(counts) {
  return stateBadge(badgeState(counts));
}

/**
 * The relation count rendered next to the badge: always the number of
 * *confirmed* relations (issue #22 需求 3 「三态徽标 + 已确认关系数」).
 *
 * Deliberately independent of the badge state. A pending item shows how many
 * relations are already confirmed (`○ 3`), not how many are pending — the
 * state is carried by the glyph and the full breakdown by the title, so the
 * number has one meaning in every row.
 */
export function relationCount(counts) {
  return normalizeCounts(counts).confirmed;
}

/**
 * Tooltip / aria text. Zero counters are omitted; an empty record has no
 * badge and therefore no title.
 */
export function badgeTitle(counts) {
  const { confirmed, pending, gaps } = normalizeCounts(counts);
  const parts = [];
  if (confirmed > 0) {
    parts.push(`已确认 ${confirmed} 条`);
  }
  if (pending > 0) {
    parts.push(`待核 ${pending} 条`);
  }
  if (gaps > 0) {
    parts.push(`缺口 ${gaps} 条`);
  }
  return parts.join(" · ");
}

/**
 * dataProvider text: the only data Zotero sorts and exports.
 *
 * Format is fixed so tests can parse it back:
 *   `${confirmed} 已确认 · ${pending} 待核 · ${gaps} 缺口`
 * Empty state (no relations, malformed record, unknown item) is `""` — an
 * empty cell, never a throw and never a placeholder that pollutes sorting.
 */
export function cellText(counts) {
  const { confirmed, pending, gaps } = normalizeCounts(counts);
  if (confirmed + pending + gaps === 0) {
    return "";
  }
  return `${confirmed} 已确认 · ${pending} 待核 · ${gaps} 缺口`;
}

/** Inverse of cellText; null for anything that is not that exact format. */
export function parseCellText(text) {
  if (typeof text !== "string") {
    return null;
  }
  const match = /^(\d{1,6}) 已确认 · (\d{1,6}) 待核 · (\d{1,6}) 缺口$/.exec(text);
  if (!match) {
    return null;
  }
  return {
    confirmed: Number(match[1]),
    pending: Number(match[2]),
    gaps: Number(match[3]),
  };
}

/**
 * Everything renderCell needs, derived from the dataProvider text alone.
 * Returns null for an empty cell (so renderCell returns a blank element).
 */
export function badgeForText(text) {
  const counts = parseCellText(text);
  if (!counts) {
    return null;
  }
  const badge = badgeForCounts(counts);
  if (!badge) {
    return null;
  }
  return { ...badge, counts, title: badgeTitle(counts) };
}

/**
 * Build the real dataProvider from a record resolver. The resolver is what
 * maps a Zotero item to a record (fake data in A2). Every failure path —
 * missing item, resolver throw, malformed record — yields `""`, because a
 * throwing dataProvider would make Zotero log and fall back to `null`
 * (pluginAPIBase.mjs wraps option functions and returns
 * `fallbackReturn || null`; for this column's `fallbackReturn: ""` that
 * expression is `null`, not `""`). The column must guard on its own.
 */
export function createDataProvider(resolveRecord) {
  return (item, dataKey) => {
    try {
      return cellText(resolveRecord(item, dataKey));
    } catch {
      return "";
    }
  };
}

/** Item key of a Zotero item, or "" for rows that are not items. */
export function itemKeyOf(item) {
  if (!item || typeof item !== "object") {
    return "";
  }
  const key = item.key;
  return typeof key === "string" ? key.trim() : "";
}

/**
 * The row ref behind `index`.
 *
 * `this` first: itemTree.jsx calls `column.renderCell.apply(this, ...)` with
 * the ItemTree as `this`, and honouring it costs nothing. But Zotero wraps
 * every registered option function in an arrow function before storing it
 * (pluginAPIBase.mjs `_validateObject`, identical on 8.0/9.0/10.0), so the
 * wrapper — not our function — receives that `this`, and `apply` cannot pass
 * it through: inside a real Zotero our `this` is undefined.
 *
 * The document handed to renderCell is the node that does reach us, and its
 * window owns the item tree. In Zotero 10 every library view's tree shares
 * the id "main" (libraryView.js `initItemsTree`: "Every library view's tree
 * shares the \"main\" id, which keys ... the plugin columns enabled for it"),
 * and `ZoteroPane.itemsView` proxies to the *active* view
 * (zoteroPane.js:191-193). That is exact for the tree on screen; with
 * several library tabs open it can name the same row index of the active
 * tab instead. This is prototype-grade targeting for the reserved click
 * hook — before U1 binds real data the payload should be derived from the
 * row itself. Null for header/spacer rows and for any unexpected context.
 */
export function itemRefOf(context, index, doc) {
  try {
    if (context && typeof context.getRow === "function") {
      const row = context.getRow(index);
      if (row && typeof row === "object" && row.ref) {
        return row.ref;
      }
    }
    const view = doc && typeof doc === "object" ? doc.defaultView : null;
    const trees = [
      view?.ZoteroPane?.itemsView,
      view?.ZoteroPane_Local?.itemsView,
      view?.Zotero?.getActiveZoteroPane?.()?.itemsView,
      view?.Zotero?.getMainWindow?.()?.ZoteroPane?.itemsView,
    ];
    for (const tree of trees) {
      if (!tree || typeof tree.getRow !== "function") {
        continue;
      }
      const row = tree.getRow(index);
      if (row && typeof row === "object") {
        return row.ref ?? null;
      }
    }
    return null;
  } catch {
    return null;
  }
}

/** FNV-1a, stable across sessions: same key -> same fixture. */
export function stableHash(text) {
  const source = typeof text === "string" ? text : "";
  let hash = 0x811c9dc5;
  for (let index = 0; index < source.length; index += 1) {
    hash ^= source.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

/** Fake lineage record for an item key; deterministic, empty for no key. */
export function fakeLineageFor(key) {
  const source = typeof key === "string" ? key : "";
  if (source.length === 0) {
    return zeroCounts();
  }
  return { ...FAKE_FIXTURES[stableHash(source) % FAKE_FIXTURES.length] };
}

/** The dataProvider actually registered on the column in A2. */
export const fakeLineageDataProvider = createDataProvider((item) =>
  fakeLineageFor(itemKeyOf(item)),
);

/**
 * Option object handed to `Zotero.ItemTreeManager.registerColumn`, minus the
 * two functions the renderer supplies.
 *
 * `defaultIn` is deliberately absent: with no `defaultIn`, Zotero starts the
 * column hidden and the user enables it from the column picker, which is the
 * "默认关闭" requirement. `zoteroPersist` keeps the user's width / hidden /
 * sort choices across restarts (virtualized-table.jsx `_getPersistedSettings`
 * always persists ordinal/hidden/sortDirection and honours this list too).
 */
export function lineageColumnOption(pluginID) {
  return {
    dataKey: LINEAGE_COLUMN_DATA_KEY,
    label: "谱系",
    pluginID: typeof pluginID === "string" ? pluginID : "",
    enabledTreeIDs: ["main"],
    showInColumnPicker: true,
    flex: 1,
    minWidth: 48,
    zoteroPersist: ["width", "hidden", "sortDirection"],
  };
}

/**
 * Registration mechanics, extracted so tests can drive them with a fake
 * manager: exactly one registerColumn call, disposal through the *returned*
 * key (Zotero namespaces it with the plugin ID), and a second dispose that
 * is a no-op.
 *
 * A failed registration (`false` or a throw) yields `dataKey: null` and a
 * no-op disposer — Zotero logs the validation error itself, and the caller
 * decides whether to surface it. Never throws.
 */
export function createColumnRegistration(manager, pluginID, hooks = {}) {
  const option = Object.assign(lineageColumnOption(pluginID), hooks);
  let dataKey = null;
  try {
    const registered = manager.registerColumn(option);
    dataKey = typeof registered === "string" && registered.length > 0 ? registered : null;
  } catch {
    dataKey = null;
  }
  let disposed = false;
  return {
    dataKey,
    option,
    dispose() {
      if (disposed || !dataKey) {
        disposed = true;
        return false;
      }
      disposed = true;
      try {
        return manager.unregisterColumn(dataKey) !== false;
      } catch {
        return false;
      }
    },
  };
}

/**
 * Click payload contract for the future 谱系 tab (U4). The column only
 * dispatches this event; it does not know how a tab opens.
 *
 * Returns null when the payload could not be routed anywhere meaningful
 * (no item key or no badge state), in which case the caller dispatches
 * nothing — a locate request without a paper cannot be honoured.
 */
export function createLocatePayload({ itemKey, libraryID, state } = {}) {
  const key = typeof itemKey === "string" ? itemKey.trim() : "";
  if (key.length === 0) {
    return null;
  }
  if (!LINEAGE_STATES.includes(state)) {
    return null;
  }
  const library =
    typeof libraryID === "number" && Number.isFinite(libraryID)
      ? Math.trunc(libraryID)
      : null;
  return { source: "lineage-column", itemKey: key, libraryID: library, state };
}

/**
 * Dispatch the locate event on `target` (the item tree's document).
 * Returns whether an event was dispatched. Never throws: a click must not
 * break the item list even if event creation is unavailable.
 *
 * The `CustomEvent` constructor is resolved from the target's window first
 * (the plugin sandbox is not guaranteed to expose DOM classes as globals),
 * then from the sandbox global; older Gecko is handled through createEvent.
 */
export function dispatchLineageLocate(target, payload) {
  if (!payload || !target || typeof target.dispatchEvent !== "function") {
    return false;
  }
  try {
    const view = target.defaultView ?? target.ownerDocument?.defaultView ?? null;
    const EventCtor = view?.CustomEvent ?? globalThis.CustomEvent;
    let event;
    if (typeof EventCtor === "function") {
      event = new EventCtor(LINEAGE_LOCATE_EVENT, {
        detail: payload,
        bubbles: true,
        cancelable: true,
      });
    } else {
      event =
        typeof target.createEvent === "function"
          ? target.createEvent("CustomEvent")
          : null;
      if (!event || typeof event.initCustomEvent !== "function") {
        return false;
      }
      event.initCustomEvent(LINEAGE_LOCATE_EVENT, true, true, payload);
    }
    target.dispatchEvent(event);
    return true;
  } catch {
    return false;
  }
}

/**
 * Subscribe to locate requests. U4 (谱系 tab) calls this; returns an
 * unsubscribe function. Invalid arguments yield a no-op unsubscribe rather
 * than an exception.
 */
export function listenLineageLocate(target, handler) {
  if (
    !target ||
    typeof target.addEventListener !== "function" ||
    typeof handler !== "function"
  ) {
    return () => {};
  }
  const listener = (event) => {
    handler(event?.detail ?? null);
  };
  target.addEventListener(LINEAGE_LOCATE_EVENT, listener);
  return () => {
    try {
      target.removeEventListener(LINEAGE_LOCATE_EVENT, listener);
    } catch {
      // Window already torn down: nothing left to unsubscribe from.
    }
  };
}
