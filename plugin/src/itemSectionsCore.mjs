/**
 * Pure logic for the three item-pane sections (U2): the cache key, the
 * skeleton → filled state machine, the four states and the stale-response
 * guard.
 *
 * Plain ESM with no DOM and no Zotero globals, so tests/test_plugin_sections.cjs
 * runs the real thing under node. The DOM half lives in itemSections.ts and
 * only maps what this module decided onto nodes.
 *
 * Why the state machine is here and not in the renderer: a section can be
 * re-rendered for an item whose request is still in flight, and the answer
 * can arrive after the user moved on. "Which key owns the DOM right now" must
 * be a value the tests can assert, not a closure hidden in a render function.
 */

/** The three sections, in the order they appear in the pane. */
export const SECTIONS = Object.freeze(["evidence", "lineage", "reading"]);

/** Panel states. `loading` and `empty` are not errors; only `error` offers a retry. */
export const PANEL_STATES = Object.freeze({
  LOADING: "loading",
  READY: "ready",
  EMPTY: "empty",
  ERROR: "error",
  OFFLINE: "offline",
});

/**
 * Canonical identity of a Zotero item for caching.
 *
 * Mirrors `identity()` in kbapp/library/sources.py: the service names a
 * library paper `z:<library_id>:<zotero_key>`, so the plugin builds exactly
 * that string. Anything that cannot produce a key (no key, a collection row,
 * a trashed search) yields "" — the sections then render nothing rather than
 * asking the service about an item that is not a library paper.
 */
export function cacheKeyFor(item, defaults = {}) {
  if (!item || typeof item !== "object") {
    return "";
  }
  // A collection or saved search also carries `.key`; only items are papers.
  if (item._objectType !== undefined && item._objectType !== "item") {
    return "";
  }
  const key = typeof item.key === "string" ? item.key.trim() : "";
  if (!key) {
    return "";
  }
  const rawLibrary = item.libraryID ?? defaults.libraryID ?? 1;
  const libraryID = Number.isFinite(Number(rawLibrary)) ? Math.trunc(Number(rawLibrary)) : 1;
  return `z:${libraryID}:${key}`;
}

/** URL for the aggregate snapshot. The key is already an opaque identity. */
export function snapshotPath(cacheKey) {
  const key = typeof cacheKey === "string" ? cacheKey.trim() : "";
  if (!key) {
    throw new TypeError("littrace item sections: snapshotPath needs a cache key");
  }
  return `/api/v2/paper/sections?paper=${encodeURIComponent(key)}`;
}

/**
 * Empty state with the "go index it" lead-in stated as data, so the renderer
 * never has to guess whether an empty section is a success or a failure.
 */
function emptyState(cacheKey, messageKey, actionKey) {
  return {
    cacheKey,
    status: PANEL_STATES.EMPTY,
    messageKey,
    actionKey,
    error: null,
    retryable: false,
    snapshot: null,
  };
}

/** The loading state: a skeleton is already on screen, a request is in flight. */
export function loadingState(cacheKey) {
  return {
    cacheKey,
    status: PANEL_STATES.LOADING,
    messageKey: null,
    actionKey: null,
    error: null,
    retryable: false,
    snapshot: null,
  };
}

/** No item key at all (collection row, non-library item): render nothing. */
export function idleState() {
  return {
    cacheKey: "",
    status: PANEL_STATES.EMPTY,
    messageKey: null,
    actionKey: null,
    error: null,
    retryable: false,
    snapshot: null,
  };
}

/**
 * The service could not be reached. This is deliberately NOT the error state:
 * the design contract says an offline panel shows a guide card ("需要本地服务"),
 * never a half-working section and never a bare retry loop.
 */
export function offlineState(cacheKey) {
  return {
    cacheKey,
    status: PANEL_STATES.OFFLINE,
    messageKey: "section-offline",
    actionKey: null,
    error: null,
    retryable: false,
    snapshot: null,
  };
}

/**
 * One-line error plus a retry button, per the design's error state.
 * `message` is the service's or client's own text; an empty message still
 * yields a visible line, never a blank panel.
 */
export function errorState(cacheKey, error) {
  const message = String((error && (error.message || error.detail)) || error || "").trim();
  return {
    cacheKey,
    status: PANEL_STATES.ERROR,
    messageKey: "section-error",
    actionKey: null,
    error: message || "未知错误",
    retryable: true,
    snapshot: null,
  };
}

/**
 * Classify a client failure into the state it deserves.
 *
 * `ServiceClientError.kind` is the S4 contract, and it throws for a non-2xx
 * answer instead of returning the status, so both facts are read here:
 *
 *   - kind "offline"          → the service is not running: the guide card
 *   - status 404              → the item is not in the littrace library: an
 *                               empty state, not an error
 *   - anything else           → a one-line error with a retry
 *
 * Anything without a kind is an error, never "offline": claiming the service
 * is down would hide a real bug behind an actionable-looking card.
 */
export function stateForFailure(cacheKey, error) {
  const status = Number(error && error.status);
  if (error && error.kind === "offline") {
    return offlineState(cacheKey);
  }
  if (status === 404) {
    return emptyState(cacheKey, "section-unindexed", "section-open-search");
  }
  return errorState(cacheKey, error);
}

/** A 404 from the snapshot endpoint means "not in the library" — an empty state. */
export function stateForStatus(cacheKey, status) {
  if (status === 404) {
    return emptyState(cacheKey, "section-unindexed", "section-open-search");
  }
  return errorState(cacheKey, `服务返回 ${status}`);
}
/**
 * Whether a section's own payload is empty. Each section decides what "no
 * content" means; the renderer only asks this one question.
 *
 * 精读 is the exception worth stating: a library paper that could still be
 * parsed is NOT empty even with zero documents, because the section's whole
 * job in that case is to offer 「解析此文献」.
 */
export function isEmptyPayload(sectionID, snapshot) {
  if (!snapshot || typeof snapshot !== "object") {
    return true;
  }
  if (sectionID === "evidence") {
    return !snapshot.evidence || !snapshot.evidence.indexed;
  }
  if (sectionID === "lineage") {
    const lineage = snapshot.lineage || {};
    return !(lineage.verified || lineage.pending || lineage.citations);
  }
  if (sectionID === "reading") {
    const reading = snapshot.reading || {};
    return !reading.documents && !reading.total_pages && reading.can_parse !== true;
  }
  return true;
}

/** The empty message per section; the actions differ because the fixes differ. */
const EMPTY_COPY = Object.freeze({
  evidence: { messageKey: "section-unindexed", actionKey: "section-open-search" },
  lineage: { messageKey: "section-no-relations", actionKey: "section-open-lineage" },
  reading: { messageKey: "section-unparsed", actionKey: null },
});

/**
 * Build the ready (or per-section empty) state from a snapshot.
 *
 * A 200 answer can still be empty for one section while the other two have
 * content, so emptiness is decided per section, not per snapshot.
 */
export function readyState(sectionID, cacheKey, snapshot) {
  if (isEmptyPayload(sectionID, snapshot)) {
    const copy = EMPTY_COPY[sectionID] || EMPTY_COPY.evidence;
    return emptyState(cacheKey, copy.messageKey, copy.actionKey);
  }
  return {
    cacheKey,
    status: PANEL_STATES.READY,
    messageKey: null,
    actionKey: null,
    error: null,
    retryable: false,
    snapshot,
  };
}

/**
 * The staleness rule that keeps a switched item from showing another item's
 * data: a response may only paint when its cache key is still the one the
 * section is bound to. Returns the state to paint, or null to discard.
 *
 * This is the single guard the renderer and the async fill must both call, so
 * there is exactly one place where "discard the late answer" is decided.
 */
export function acceptResponse(state, cacheKey, nextState) {
  if (!state || state.cacheKey !== cacheKey) {
    return null;
  }
  if (!nextState || nextState.cacheKey !== cacheKey) {
    return null;
  }
  return nextState;
}

/**
 * Map one section's snapshot to its list of rows, already reduced to what the
 * DOM needs. Keeping this out of the renderer means the numbers the tests see
 * are the numbers the pane shows.
 */
export function sectionRows(sectionID, snapshot) {
  if (!snapshot || typeof snapshot !== "object") {
    return [];
  }
  if (sectionID === "evidence") {
    const evidence = snapshot.evidence || {};
    const counts = evidence.counts || {};
    const rows = Object.keys(counts)
      .filter((kind) => counts[kind] > 0)
      .map((kind) => ({
        kind: "count",
        labelKey: `evidence-kind-${kind}`,
        value: String(counts[kind]),
      }));
    const excerpts = Array.isArray(evidence.excerpts) ? evidence.excerpts : [];
    for (const excerpt of excerpts) {
      rows.push({
        kind: "excerpt",
        page: Number.isFinite(Number(excerpt.page)) ? Math.trunc(Number(excerpt.page)) : 0,
        text: String(excerpt.excerpt || ""),
      });
    }
    return rows;
  }
  if (sectionID === "lineage") {
    const lineage = snapshot.lineage || {};
    const items = Array.isArray(lineage.items) ? lineage.items : [];
    return items.map((item) => ({
      kind: "relation",
      id: String(item.id || ""),
      label: String(item.label || ""),
      peerTitle: String(item.peer_title || ""),
      peerYear: Number.isFinite(Number(item.peer_year)) ? Math.trunc(Number(item.peer_year)) : null,
      direction: ["previous", "following", "unknown"].includes(item.direction)
        ? item.direction
        : "unknown",
      grade: String(item.grade || "title"),
      gradeLabel: String(item.grade_label || ""),
      note: String(item.explanation || ""),
    }));
  }
  if (sectionID === "reading") {
    const reading = snapshot.reading || {};
    const rows = [
      {
        kind: "coverage",
        readPages: Number(reading.read_pages) || 0,
        totalPages: Number(reading.total_pages) || 0,
        state: String(reading.state || "unparsed"),
      },
    ];
    const classification = reading.classification || {};
    for (const name of Object.keys(classification)) {
      if (!classification[name]) {
        continue;
      }
      rows.push({
        kind: "page-class",
        labelKey: `reading-class-${name}`,
        value: String(classification[name]),
      });
    }
    const gaps = Array.isArray(reading.gaps) ? reading.gaps : [];
    for (const gap of gaps) {
      rows.push({ kind: "gap", text: String(gap || "") });
    }
    return rows;
  }
  return [];
}

/**
 * Whether the parse button may be offered: a library paper with a document,
 * or one that could still be parsed. Never shown when there is nothing to parse.
 */
export function canParse(snapshot) {
  const reading = (snapshot && snapshot.reading) || {};
  return reading.can_parse === true;
}

/**
 * Registration options, minus the hook functions the renderer supplies.
 *
 * `pluginID` is mandatory: Zotero namespaces the paneID with it and reclaims
 * the section on disable/uninstall. Icons are 16x16 (header) and 20x20
 * (sidenav) per itemPaneManager.js:458-459.
 *
 * The l10n IDs carry the plugin namespace prefix. Zotero consolidates every
 * plugin's FTL files into one L10nFileSource (zp plugins.js), and the
 * scaffold's build rewrites each message key to `<addonRef>-<key>`; a
 * `data-l10n-id` is looked up verbatim in that shared source, so passing the
 * bare key would render the raw ID in the header. `t()` adds the same prefix
 * on the code side (utils/locale.ts), which keeps one naming rule.
 */
export function sectionOptions(pluginID, kind, iconBase, namespace) {
  const base = String(iconBase || "").replace(/\/+$/, "");
  const ns = String(namespace || "").trim();
  const sections = {
    evidence: { id: "evidence", headerKey: "section-evidence", sidenavKey: "section-evidence-tooltip" },
    lineage: { id: "lineage", headerKey: "section-lineage", sidenavKey: "section-lineage-tooltip" },
    reading: { id: "reading", headerKey: "section-reading", sidenavKey: "section-reading-tooltip" },
  };
  const entry = sections[kind];
  if (!entry) {
    throw new TypeError(`littrace item sections: unknown section "${kind}"`);
  }
  const l10nID = (key) => (ns && !key.startsWith(`${ns}-`) ? `${ns}-${key}` : key);
  return {
    paneID: `item-section-${entry.id}`,
    pluginID: String(pluginID || ""),
    header: {
      l10nID: l10nID(entry.headerKey),
      icon: `${base}/${entry.id}-16.svg`,
    },
    sidenav: {
      l10nID: l10nID(entry.sidenavKey),
      icon: `${base}/${entry.id}-20.svg`,
    },
  };
}

/**
 * `createSectionRegistration` drives `registerSection` through a fake manager
 * in tests and the real one at runtime. Zotero returns the namespaced paneID
 * or false; unregister takes that *returned* value. A rejection is reported
 * (dataKey null), never thrown: one bad section must not abort startup.
 */
export function createSectionRegistration(
  manager,
  pluginID,
  kind,
  hooks = {},
  iconBase = "",
  namespace = "",
) {
  let option;
  try {
    option = Object.assign(sectionOptions(pluginID, kind, iconBase, namespace), hooks);
  } catch {
    return { dataKey: null, option: null, dispose: () => false };
  }
  let dataKey = null;
  try {
    const registered = manager.registerSection(option);
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
        return manager.unregisterSection(dataKey) !== false;
      } catch {
        return false;
      }
    },
  };
}

/**
 * Shared snapshot cache, keyed by the Zotero item identity.
 *
 * One instance per plugin lifetime (see itemSections.ts) so the three sections
 * read one snapshot per item instead of three requests. Three rules make the
 * laziness observable:
 *
 *   1. A cache hit performs **no** request — that is the whole point of
 *      caching by item key.
 *   2. Concurrent requests for the same key share one in-flight promise, so
 *      re-entering a section mid-flight cannot double the traffic.
 *   3. A rejected request caches nothing, so retry really retries.
 *
 * `limit` bounds the map (an LRU by insertion order): scrolling through a
 * 5000-item library must not pin 5000 snapshots.
 */
export function createPanelCache({ load, limit = 64, now = () => Date.now() } = {}) {
  if (typeof load !== "function") {
    throw new TypeError("littrace item sections: createPanelCache needs a load function");
  }
  const entries = new Map();
  const inflight = new Map();
  const max = Number.isFinite(Number(limit)) && Number(limit) > 0 ? Math.trunc(Number(limit)) : 64;
  let hits = 0;
  let misses = 0;
  let requests = 0;

  function touch(cacheKey) {
    const entry = entries.get(cacheKey);
    if (!entry) {
      return null;
    }
    // Re-insert to move it to the young end of the Map's insertion order.
    entries.delete(cacheKey);
    entries.set(cacheKey, entry);
    return entry.value;
  }

  function put(cacheKey, value) {
    const key = String(cacheKey || "");
    if (!key) {
      return value;
    }
    entries.delete(key);
    entries.set(key, { value, at: now() });
    while (entries.size > max) {
      const oldest = entries.keys().next().value;
      entries.delete(oldest);
    }
    return value;
  }

  return {
    get(cacheKey) {
      const key = String(cacheKey || "");
      if (!key) {
        return null;
      }
      const value = touch(key);
      if (value === null) {
        misses += 1;
      } else {
        hits += 1;
      }
      return value;
    },
    peek(cacheKey) {
      const entry = entries.get(String(cacheKey || ""));
      return entry ? entry.value : null;
    },
    put,
    /**
     * Fetch one key. Resolves with the snapshot; rejects with the loader's
     * error and caches nothing in that case.
     */
    fetch(cacheKey) {
      const key = String(cacheKey || "");
      if (!key) {
        return Promise.reject(new TypeError("littrace item sections: fetch needs a cache key"));
      }
      const cached = touch(key);
      if (cached !== null) {
        hits += 1;
        return Promise.resolve(cached);
      }
      const pending = inflight.get(key);
      if (pending) {
        return pending;
      }
      requests += 1;
      const promise = Promise.resolve()
        .then(() => load(key))
        .then((value) => {
          put(key, value);
          return value;
        })
        .finally(() => {
          inflight.delete(key);
        });
      inflight.set(key, promise);
      return promise;
    },
    invalidate(cacheKey) {
      return entries.delete(String(cacheKey || ""));
    },
    size() {
      return entries.size;
    },
    inflightSize() {
      return inflight.size;
    },
    stats() {
      return { hits, misses, requests, size: entries.size };
    },
    clear() {
      entries.clear();
      inflight.clear();
    },
  };
}

/**
 * What a section should do on render, decided without touching the DOM.
 *
 * - no cache key  → idle (render nothing; a collection row is not a paper)
 * - cached        → ready/empty from the cache, **no request** (lazy load)
 * - not cached    → loading skeleton plus a request to start
 *
 * The renderer must paint `state` synchronously and only then await
 * `request`, which is the official "skeleton first, fill later" shape
 * (itemPaneManager.js onRender / onAsyncRender).
 */
export function planRender(sectionID, cacheKey, cached) {
  if (!cacheKey) {
    return { state: idleState(), request: null };
  }
  if (cached) {
    return { state: readyState(sectionID, cacheKey, cached), request: null };
  }
  return { state: loadingState(cacheKey), request: true };
}

/**
 * Turn one fetch outcome into the state to paint, for `sectionID`.
 *
 * `outcome` is `{snapshot}` on success or `{error}` for a thrown client
 * failure. The S4 client rejects non-2xx answers (ServiceClientError carries
 * `status`), so there is no separate status branch. The caller still has to
 * run the result through `acceptResponse` before painting.
 */
export function settleRender(sectionID, cacheKey, outcome) {
  if (!cacheKey) {
    return idleState();
  }
  const result = outcome || {};
  if (result.error) {
    return stateForFailure(cacheKey, result.error);
  }
  return readyState(sectionID, cacheKey, result.snapshot);
}

/**
 * Render generation of one section mount.
 *
 * `acceptResponse` already refuses an answer whose key differs from the
 * mounted one. The generation adds the second half of the rule: a retry (or a
 * re-render of the same item) supersedes earlier in-flight answers, so two
 * responses for one key cannot paint in the order they happen to arrive.
 */
export function createRenderGeneration() {
  let current = 0;
  return {
    begin() {
      current += 1;
      return current;
    },
    isCurrent(token) {
      return token === current;
    },
    current() {
      return current;
    },
  };
}

