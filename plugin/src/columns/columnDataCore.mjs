/**
 * Real-data mapping and caching for the two item-tree columns (U1).
 *
 * Deliberately plain ESM with no Zotero, DOM or TypeScript globals, so
 * tests/test_plugin_column.cjs exercises the real response→cell mapping, the
 * cache key/semantics and every failure path with `node --test` and no npm
 * install. src/columns/paperData.ts is the only adapter: it supplies the S4
 * client's `GET` as `fetchPaper`.
 *
 * Facts this module is built on (read from the service on main, U1):
 *   - `GET /api/v2/paper?paper=<id>` (kbapp/web/v2_api.py:53) returns
 *     kbapp/web/library_api.py `paper_view`: `{paper, relations, approvals,
 *     understanding, note_sync, references}`.
 *   - The paper identity is `z:<libraryID>:<zotero key>`
 *     (kbapp/library/sources.py `identity`), and that identity is inserted
 *     into the aliases table on every `put_papers` (kbapp/storage/store.py),
 *     so passing it straight to `?paper=` resolves without a lookup.
 *   - "Established" in the service's own words is `status == 'confirmed' and
 *     not evidence_stale` (kbapp/research/viewmodels.py:35). A confirmed edge
 *     with stale evidence is exactly the 「含缺口」 case: the relation stands
 *     but its evidence must be re-checked (viewmodels.py:67 "证据已过期,
 *     待复核").
 *   - `understanding` is the latest PaperUnderstanding artifact or null, and
 *     its `coverage` is `{total_pages, read_pages, missing_pages, complete}`
 *     (kbapp/research/reading.py:148). `null` therefore means "the service
 *     says there is no reading artifact yet" — a real answer, not a failure.
 *
 * A column's dataProvider is synchronous while every service call is async,
 * so the contract here is: read the cache now, start a background fetch on a
 * miss, and let `onUpdate` tell the caller to repaint. The cell never waits,
 * never throws, and shows nothing until a real answer exists.
 */

import { createDataProvider, isItemRef, itemKeyOf } from "./lineageCore.mjs";

/** The service path both columns read. */
export const PAPER_ENDPOINT = "/api/v2/paper";

/**
 * Cache defaults.
 *
 * `ttlMs` is the freshness window for a successful response: the item list
 * calls dataProvider on every cell of every repaint, so without a cache each
 * scroll would issue a request per row.
 *
 * `failureTtlMs` is deliberately much shorter than `ttlMs` but not zero: a
 * down service must not be hammered once per row per repaint, yet the column
 * must recover on its own within seconds of the service coming back, without
 * asking the user to restart the plugin.
 *
 * `maxEntries` bounds memory for very large libraries. Eviction is oldest
 * insertion first; correctness never depends on an entry still being there.
 */
export const PAPER_CACHE_DEFAULTS = Object.freeze({
  ttlMs: 60000,
  failureTtlMs: 5000,
  maxEntries: 500,
});

/** 「精读」 states; `unknown` is not a state but the absence of an answer. */
export const READING_STATES = Object.freeze(["complete", "partial", "none"]);

/** Glyph/colour table for the 「精读」 column; `none` is still a real answer. */
const READING_STATE_BADGES = Object.freeze({
  complete: Object.freeze({
    state: "complete",
    symbol: "●",
    label: "已精读",
    className: "littrace-reading-complete",
    color: "rgba(46, 125, 50, 0.85)",
  }),
  partial: Object.freeze({
    state: "partial",
    symbol: "◐",
    label: "部分精读",
    className: "littrace-reading-partial",
    color: "rgba(180, 83, 9, 0.85)",
  }),
  none: Object.freeze({
    state: "none",
    symbol: "○",
    label: "未精读",
    className: "littrace-reading-none",
    color: "rgba(107, 114, 128, 0.75)",
  }),
});

/** The `z:<libraryID>:<key>` identity the service resolves, or "" if unusable. */
export function paperKeyOf(libraryID, itemKey) {
  const key = typeof itemKey === "string" ? itemKey.trim() : "";
  if (key.length === 0) {
    return "";
  }
  // Zotero numbers libraries from 1 and the service defaults a missing
  // library_id to 1 (`int(p.get('library_id', 1))`), so an absent or
  // malformed libraryID must mean the user library, not "unknown".
  const library =
    typeof libraryID === "number" && Number.isFinite(libraryID) && libraryID > 0
      ? Math.trunc(libraryID)
      : 1;
  return `z:${library}:${key}`;
}

/** Absolute request path for one paper, or "" when the item has no key. */
export function paperPath(libraryID, itemKey) {
  const paper = paperKeyOf(libraryID, itemKey);
  return paper.length === 0
    ? ""
    : `${PAPER_ENDPOINT}?paper=${encodeURIComponent(paper)}`;
}

/**
 * `relations` → the three counters the 「谱系」 badge shows.
 *
 * - confirmed: established (confirmed, not stale)
 * - gaps:      confirmed but evidence is stale — the relation stands, its
 *              evidence needs re-checking (「含缺口」)
 * - pending:   everything else that is not `rejected`. Unknown statuses are
 *              counted as pending on purpose: dropping a relation the service
 *              did mention would silently under-report, while showing it as
 *              pending matches "not yet established".
 *
 * `rejected` is the only status ignored: the service stores review outcomes
 * that way and never shows them (`status!='rejected'` throughout graph.py).
 */
export function lineageCountsFromRelations(relations) {
  const counts = { confirmed: 0, pending: 0, gaps: 0 };
  if (!Array.isArray(relations)) {
    return counts;
  }
  for (const relation of relations) {
    // Only plain objects are relations. An array or a primitive inside the
    // list is schema drift, not "a relation whose status we did not
    // recognise", so it must not be counted as pending.
    if (!relation || typeof relation !== "object" || Array.isArray(relation)) {
      continue;
    }
    if (relation.status === "rejected") {
      continue;
    }
    if (relation.status !== "confirmed") {
      counts.pending += 1;
      continue;
    }
    if (relation.evidence_stale === true) {
      counts.gaps += 1;
    } else {
      counts.confirmed += 1;
    }
  }
  return counts;
}

/** Non-negative integer from a count-ish field; anything else is 0. */
function pageCount(value) {
  if (Array.isArray(value)) {
    return value.length;
  }
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number) || number <= 0) {
    return 0;
  }
  return Math.floor(number);
}

/**
 * `understanding` → `{state, readPages, totalPages}`.
 *
 * Returns null when the response carries no usable coverage: `understanding`
 * is absent/not an object (a schema we do not understand), or it is an object
 * without a `coverage` object (the endpoint would have to add that field).
 * Null means "no answer", which the column renders blank — never 未精读 and
 * never a made-up page count.
 *
 * `understanding === null` is different: the service explicitly answered that
 * this paper has no reading artifact yet, so it is `none` (未精读).
 *
 * Stale artifacts need no separate branch: `paper_view` rewrites a stale
 * understanding with `verification_status: 'stale'` and
 * `coverage.complete = False` (kbapp/web/library_api.py) whenever the PDF,
 * model or prompt changed, so a stale reading can never be reported as
 * 已精读. It lands in `partial` by construction — the honest rendering.
 */
export function readingFromUnderstanding(understanding) {
  if (understanding === null) {
    return { state: "none", readPages: 0, totalPages: 0 };
  }
  if (!understanding || typeof understanding !== "object") {
    return null;
  }
  const coverage = understanding.coverage;
  if (!coverage || typeof coverage !== "object" || Array.isArray(coverage)) {
    return null;
  }
  const readPages = pageCount(coverage.read_pages);
  const totalPages = pageCount(coverage.total_pages);
  if (coverage.complete === true && totalPages > 0) {
    return { state: "complete", readPages, totalPages };
  }
  if (readPages > 0 || totalPages > 0 || coverage.complete === true) {
    return { state: "partial", readPages, totalPages };
  }
  return { state: "none", readPages: 0, totalPages: 0 };
}

/**
 * dataProvider text for 「精读」: the only data Zotero sorts and exports.
 *
 *   complete  `已精读 · 12/12 页`
 *   partial   `部分精读 · 7/12 页`
 *   none      `未精读`
 *
 * Unknown (null) is `""`: an empty cell. The format is fixed so it parses
 * back — renderCell rebuilds the badge from this text, exactly as the
 * 「谱系」 column does, so the cell and the sort key cannot disagree.
 */
export function readingCellText(reading) {
  if (!reading || typeof reading !== "object") {
    return "";
  }
  const state = READING_STATES.includes(reading.state) ? reading.state : null;
  if (!state) {
    return "";
  }
  const label = READING_STATE_BADGES[state].label;
  if (state === "none") {
    return label;
  }
  const readPages = pageCount(reading.readPages);
  const totalPages = pageCount(reading.totalPages);
  return `${label} · ${readPages}/${totalPages} 页`;
}

/** Inverse of readingCellText; null for anything that is not that format. */
export function parseReadingCellText(text) {
  if (typeof text !== "string") {
    return null;
  }
  const match = /^(已精读|部分精读|未精读)(?: · (\d{1,6})\/(\d{1,6}) 页)?$/.exec(text);
  if (!match) {
    return null;
  }
  const state =
    match[1] === "已精读" ? "complete" : match[1] === "部分精读" ? "partial" : "none";
  if (state === "none") {
    return match[2] === undefined
      ? { state: "none", readPages: 0, totalPages: 0 }
      : null;
  }
  if (match[2] === undefined) {
    return null;
  }
  return { state, readPages: Number(match[2]), totalPages: Number(match[3]) };
}

/** Badge descriptor for a reading text; null for an empty/unparsable cell. */
export function readingBadgeForText(text) {
  const reading = parseReadingCellText(text);
  if (!reading) {
    return null;
  }
  const badge = READING_STATE_BADGES[reading.state];
  if (!badge) {
    return null;
  }
  const title =
    reading.state === "none"
      ? "尚无精读产物"
      : `${badge.label} · 已读 ${reading.readPages}/${reading.totalPages} 页`;
  return { ...badge, title, reading };
}

/**
 * A bounded, TTL'd cache of one paper response plus in-flight dedupe.
 *
 * The item list asks for the same paper many times per repaint (every
 * column, every cell) and both columns ask for it, so a miss must produce
 * exactly one request. `track` holds that promise so a second caller in the
 * same tick joins it instead of starting a duplicate.
 *
 * `now` is injectable so tests assert expiry without sleeping.
 */
export function createPaperCache(options = {}) {
  const ttlMs = Number.isFinite(options.ttlMs)
    ? Math.max(0, options.ttlMs)
    : PAPER_CACHE_DEFAULTS.ttlMs;
  const failureTtlMs = Number.isFinite(options.failureTtlMs)
    ? Math.max(0, options.failureTtlMs)
    : PAPER_CACHE_DEFAULTS.failureTtlMs;
  const maxEntries = Number.isFinite(options.maxEntries)
    ? Math.max(1, Math.trunc(options.maxEntries))
    : PAPER_CACHE_DEFAULTS.maxEntries;
  const now = typeof options.now === "function" ? options.now : () => Date.now();
  const entries = new Map();
  const inflight = new Map();

  function evictIfNeeded() {
    while (entries.size > maxEntries) {
      const oldest = entries.keys().next();
      if (oldest.done) {
        return;
      }
      entries.delete(oldest.value);
    }
  }

  return {
    /** Fresh entry (`{ok, data}`) or undefined when missing/expired. */
    read(key) {
      if (typeof key !== "string" || key.length === 0) {
        return undefined;
      }
      const entry = entries.get(key);
      if (!entry) {
        return undefined;
      }
      if (now() >= entry.expires) {
        entries.delete(key);
        return undefined;
      }
      return { ok: entry.ok, data: entry.data };
    },
    /** Record a successful response. */
    store(key, data) {
      if (typeof key !== "string" || key.length === 0) {
        return;
      }
      entries.delete(key);
      entries.set(key, { ok: true, data, expires: now() + ttlMs });
      evictIfNeeded();
    },
    /** Record a failure with the short TTL; the data stays "no answer". */
    storeFailure(key) {
      if (typeof key !== "string" || key.length === 0) {
        return;
      }
      entries.delete(key);
      entries.set(key, { ok: false, data: null, expires: now() + failureTtlMs });
      evictIfNeeded();
    },
    /** The in-flight promise for `key`, if any. */
    inFlight(key) {
      return inflight.get(key);
    },
    /** Track one in-flight request, clearing the slot when it settles. */
    track(key, promise) {
      inflight.set(key, promise);
      const clear = () => {
        if (inflight.get(key) === promise) {
          inflight.delete(key);
        }
      };
      Promise.resolve(promise).then(clear, clear);
      return promise;
    },
    invalidate(key) {
      entries.delete(key);
    },
    clear() {
      entries.clear();
      inflight.clear();
    },
    get size() {
      return entries.size;
    },
  };
}

/**
 * Build the two synchronous dataProviders over one shared cache.
 *
 * `fetchPaper(paper, {libraryID, itemKey})` must resolve with the parsed
 * `/api/v2/paper` body and reject on any failure; the rejection is swallowed
 * here (that is the point — a list render must never see it) and recorded as
 * a short-lived failure so the service is not asked again immediately.
 *
 * `onUpdate` is called after a successful store so the caller can repaint the
 * item trees; it is never called from a failure path, because nothing visible
 * changed.
 */
export function createPaperDataProviders(options = {}) {
  const fetchPaper = options.fetchPaper;
  if (typeof fetchPaper !== "function") {
    throw new TypeError(
      "littrace column data: fetchPaper must be a function",
    );
  }
  const cache = options.cache || createPaperCache(options.cacheOptions || {});
  const onUpdate = typeof options.onUpdate === "function" ? options.onUpdate : null;

  /** Schedule one request for `key` unless a fresh value or request exists. */
  function ensure(key, libraryID, itemKey) {
    if (cache.read(key) || cache.inFlight(key)) {
      return;
    }
    let request;
    try {
      request = Promise.resolve(fetchPaper(key, { libraryID, itemKey }));
    } catch (error) {
      request = Promise.reject(error);
    }
    cache.track(
      key,
      request.then(
        (data) => {
          cache.store(key, data);
          if (onUpdate) {
            try {
              onUpdate(key);
            } catch {
              // A repaint hook must not turn a good response into a failure.
            }
          }
          return data;
        },
        () => {
          cache.storeFailure(key);
          return null;
        },
      ),
    );
  }

  /** `{key, libraryID, itemKey}` for a real item row, or null. */
  function identityOf(item) {
    if (!isItemRef(item)) {
      return null;
    }
    const itemKey = itemKeyOf(item);
    if (itemKey.length === 0) {
      return null;
    }
    const libraryID =
      typeof item.libraryID === "number" && Number.isFinite(item.libraryID)
        ? Math.trunc(item.libraryID)
        : 1;
    return { key: paperKeyOf(libraryID, itemKey), libraryID, itemKey };
  }

  function entryFor(item) {
    const identity = identityOf(item);
    if (!identity) {
      return null;
    }
    const entry = cache.read(identity.key);
    if (!entry) {
      ensure(identity.key, identity.libraryID, identity.itemKey);
      return null;
    }
    if (!entry.ok) {
      return null;
    }
    return { identity, data: entry.data };
  }

  // The lineage provider reuses lineageCore's guard, so the "never throw,
  // always a string" contract is implemented once.
  const lineageProvider = createDataProvider((item) => {
    const found = entryFor(item);
    if (!found) {
      return null;
    }
    const relations = found.data && typeof found.data === "object"
      ? found.data.relations
      : null;
    return lineageCountsFromRelations(relations);
  });

  function readingProvider(item) {
    try {
      const found = entryFor(item);
      if (!found) {
        return "";
      }
      const understanding = found.data && typeof found.data === "object"
        ? found.data.understanding
        : undefined;
      return readingCellText(readingFromUnderstanding(understanding));
    } catch {
      return "";
    }
  }

  return {
    cache,
    /** Paper identity for an item, exposed for the click payload. */
    identityOf,
    /** 「谱系」 text: counts from `relations`, "" while unknown or failed. */
    lineageProvider,
    /** 「精读」 text: state from `understanding.coverage`, "" while unknown. */
    readingProvider,
    /** Drop every cached answer (plugin shutdown, explicit refresh). */
    invalidateAll() {
      cache.clear();
    },
  };
}
