/**
 * Pure logic for the item / tools menus and the offline guide card (U6).
 *
 * Deliberately plain ESM with no DOM and no Zotero globals, so
 * tests/test_plugin_menus.cjs runs the real menu contract and the real
 * greying rules with `node --test`, no npm install and no Zotero process.
 * src/menus/menus.ts only turns these values into `Zotero.MenuManager`
 * registrations and DOM nodes.
 *
 * ── One table, no second list ───────────────────────────────────────────
 *
 * `MENU_ENTRIES` is the contract: every menu item's id, Fluent label key, the
 * gate that greys it out and the command it dispatches. The renderer derives
 * the `MenuManager` tree from it and the tests assert it directly, so a menu
 * item that exists in only one of the two places cannot happen. The table is
 * also the place the planned-but-blocked 「打开离线图谱」 entry lives: it
 * carries `registered: false` and a reason key, and the tests assert it never
 * reaches `Zotero.MenuManager` (an entry that would 404 in the system browser
 * is a half feature, and this repository does not ship half features).
 *
 * ── Greying is two gates, not one ──────────────────────────────────────
 *
 *   service gate  the S4 health probe said `available`. `unsupported` (the
 *                 service answers but speaks a version this plugin cannot use)
 *                 greys too, with its own reason — letting it through would
 *                 hand the user a click that can only fail. A probe still in
 *                 flight greys as 「正在检查」, because availability is never
 *                 claimed without positive evidence.
 *   selection    「查证据」/「加入对比集」 need at least one selected item that
 *                 can be expressed as a paper id, the single-item commands
 *                 need exactly one, and 「与对比集对比」 needs 2–4 papers in
 *                 the comparison set — the same bound the service enforces
 *                 (`kbapp/workflows/agents.py`: kind=compare requires 2–4).
 *
 * Order of reasons: the service gate is reported first. When the service is
 * down, "offline" is the fact the user must act on; the selection is not.
 *
 * ── Paper identity ─────────────────────────────────────────────────────
 *
 * A selected Zotero item becomes the service's paper id `z:<libraryID>:<key>`
 * (`kbapp/library/sources.py: identity()`), the same rule the 「谱系」column's
 * locate payload follows (ADR 0028). A missing or non-positive `libraryID` is
 * refused instead of defaulting to 0: `z:0:KEY` names no paper, and guessing
 * would open a different paper for group libraries.
 */

import { dotStateOf } from "../status/statusCore.mjs";

/** The two `Zotero.MenuManager` targets this feature registers. */
export const MENU_TARGETS = Object.freeze({
  ITEM: "main/library/item",
  TOOLS: "main/menubar/tools",
});

/**
 * One `menuID` per target. `MenuManager.unregisterMenu()` takes this id, so it
 * is the handle the registry's disposer needs.
 */
export const MENU_IDS = Object.freeze({
  item: "littrace-item-menu",
  tools: "littrace-tools-menu",
});

/**
 * What the menu layer knows about the service, in menu terms.
 *
 * Derived from S4's health verdict through `dotStateOf()`, so the three menu
 * states and the three dot colours cannot drift apart: green is the only
 * state that enables an entry, yellow (unsupported) and grey (offline) both
 * grey it out.
 */
export const SERVICE_STATES = Object.freeze({
  CHECKING: "checking",
  ONLINE: "online",
  INCOMPATIBLE: "incompatible",
  OFFLINE: "offline",
});

/**
 * S4 health verdict → menu service state.
 *
 * `null`/`undefined` means "no probe has settled yet" and maps to CHECKING;
 * anything else is classified by `dotStateOf()`, where an unrecognised
 * verdict is already offline (never green without positive evidence).
 */
export function serviceStateOf(health, options = {}) {
  if (options.pending === true) {
    return SERVICE_STATES.CHECKING;
  }
  if (health === null || health === undefined) {
    return SERVICE_STATES.CHECKING;
  }
  switch (dotStateOf(health)) {
    case "online":
      return SERVICE_STATES.ONLINE;
    case "outdated":
      return SERVICE_STATES.INCOMPATIBLE;
    default:
      return SERVICE_STATES.OFFLINE;
  }
}

/** Why an entry is greyed out, as Fluent keys. */
export const GATE_REASON_KEYS = Object.freeze({
  checking: "menu-gate-checking",
  offline: "menu-gate-offline",
  incompatible: "menu-gate-incompatible",
  selection: "menu-gate-selection",
  single: "menu-gate-selection-single",
  "compare-size": "menu-gate-compare-size",
  "compare-full": "menu-gate-compare-full",
});

/** The comparison set's bounds; the service rejects anything else. */
export const COMPARE_MIN = 2;
export const COMPARE_MAX = 4;

/**
 * One menu entry of the contract. Fields:
 *   id             stable entry id (unique across the whole table)
 *   group          "item" (条目右键子菜单) or "tools" (Tools 菜单)
 *   labelKey       bare Fluent key; the build's namespace is added by ftlId()
 *   command        dispatcher id, resolved in src/menus/menus.ts
 *   menuType       MenuManager menu type ("menuitem" here; separators avoided)
 *   needsService   greyed unless the S4 probe said `available`
 *   selection      "none" | "any" (>=1 usable target) | "single" (exactly 1)
 *   compare        "none" | "add" (needs room) | "run" (needs 2–4) | "clear"
 *   visible        "always" | "offline" (offline/incompatible only) | "compare"
 *   registered     false = defined but never registered (blocked entry)
 *   blockedReasonKey  Fluent key explaining why a blocked entry is not shipped
 */
export const MENU_ENTRIES = Object.freeze([
  Object.freeze({
    id: "item-evidence",
    group: "item",
    labelKey: "menu-item-evidence",
    command: "evidence",
    menuType: "menuitem",
    needsService: true,
    selection: "any",
    compare: "none",
    visible: "always",
    registered: true,
  }),
  Object.freeze({
    id: "item-compare-add",
    group: "item",
    labelKey: "menu-item-compare-add",
    command: "compare-add",
    menuType: "menuitem",
    needsService: true,
    selection: "any",
    compare: "add",
    visible: "always",
    registered: true,
  }),
  Object.freeze({
    id: "item-compare-run",
    group: "item",
    labelKey: "menu-item-compare-run",
    command: "compare-run",
    menuType: "menuitem",
    needsService: true,
    selection: "none",
    compare: "run",
    visible: "always",
    registered: true,
  }),
  // Clearing the set is local bookkeeping: it works while the service is
  // offline, and it only appears once there is something to clear. Without it
  // a full set could never be emptied, which is exactly the 「半功能状态」
  // this repository forbids.
  Object.freeze({
    id: "item-compare-clear",
    group: "item",
    labelKey: "menu-item-compare-clear",
    command: "compare-clear",
    menuType: "menuitem",
    needsService: false,
    selection: "none",
    compare: "clear",
    visible: "compare",
    registered: true,
  }),
  Object.freeze({
    id: "item-locate",
    group: "item",
    labelKey: "menu-item-locate",
    command: "locate",
    menuType: "menuitem",
    needsService: true,
    selection: "single",
    compare: "none",
    visible: "always",
    registered: true,
  }),
  Object.freeze({
    id: "item-parse",
    group: "item",
    labelKey: "menu-item-parse",
    command: "parse",
    menuType: "menuitem",
    needsService: true,
    selection: "single",
    compare: "none",
    visible: "always",
    registered: true,
  }),
  // The one always-clickable entry while offline. It is the guide card's door:
  // disabled entries cannot be clicked, so the explanation needs a neighbour
  // that stays enabled (same rule as U5's 「服务诊断」).
  Object.freeze({
    id: "item-guide",
    group: "item",
    labelKey: "menu-item-guide",
    command: "guide",
    menuType: "menuitem",
    needsService: false,
    selection: "none",
    compare: "none",
    visible: "offline",
    registered: true,
  }),
  Object.freeze({
    id: "tools-open-graph",
    group: "tools",
    labelKey: "menu-item-open-graph",
    command: "open-graph",
    menuType: "menuitem",
    needsService: true,
    selection: "none",
    compare: "none",
    visible: "always",
    registered: true,
  }),
  // Planned by the UI design, blocked on the service: littrace's offline
  // graph is `exports/evolution.html`, and no route serves it today (the web
  // app serves only `assets/`). The plugin cannot open a file whose data
  // directory it is never told. Kept here so the contract is complete and the
  // tests assert it is NOT registered — a always-greyed menu item or a 404 in
  // the system browser are both defects.
  Object.freeze({
    id: "tools-open-offline-graph",
    group: "tools",
    labelKey: "menu-item-open-offline-graph",
    command: "open-offline-graph",
    menuType: "menuitem",
    needsService: true,
    selection: "none",
    compare: "none",
    visible: "always",
    registered: false,
    blockedReasonKey: "menu-blocked-offline-graph",
  }),
  Object.freeze({
    id: "tools-diagnostics",
    group: "tools",
    labelKey: "menu-item-diagnostics",
    command: "diagnostics",
    menuType: "menuitem",
    needsService: false,
    selection: "none",
    compare: "none",
    visible: "always",
    registered: true,
  }),
  Object.freeze({
    id: "tools-guide",
    group: "tools",
    labelKey: "menu-item-guide",
    command: "guide",
    menuType: "menuitem",
    needsService: false,
    selection: "none",
    compare: "none",
    visible: "offline",
    registered: true,
  }),
]);

/**
 * The two registrations. `shape: "submenu"` nests the entries under one
 * 「littrace」 submenu (the item context menu is a grouped target, where
 * top-level separators are rejected by MenuManager); `shape: "flat"` puts the
 * tools entries directly into the Tools menu.
 */
export const MENU_GROUPS = Object.freeze([
  Object.freeze({
    id: "item",
    target: MENU_TARGETS.ITEM,
    menuID: MENU_IDS.item,
    labelKey: "menu-item-root",
    shape: "submenu",
  }),
  Object.freeze({
    id: "tools",
    target: MENU_TARGETS.TOOLS,
    menuID: MENU_IDS.tools,
    labelKey: null,
    shape: "flat",
  }),
]);

/** Every entry that is actually registered, in table order. */
export function registeredEntries(groupId = null) {
  return MENU_ENTRIES.filter(
    (entry) => entry.registered !== false && (groupId === null || entry.group === groupId),
  );
}

/** Non-empty string? Used by the identity helpers below. */
function textOf(value) {
  return typeof value === "string" ? value.trim() : "";
}

/** A positive integer library id, or null. Never 0, never NaN. */
function libraryIdOf(value) {
  const number =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim()
        ? Number(value.trim())
        : NaN;
  return Number.isInteger(number) && number > 0 ? number : null;
}

/**
 * One selected Zotero item → the paper target, or null when the item cannot
 * be expressed as `z:<libraryID>:<key>`.
 *
 * An attachment or note is represented by its parent item: the parent key is
 * inherited from the child, and so is the library (a parent in a different
 * library is not expressible through this API, which the service's own
 * `identity()` also assumes).
 */
export function targetOf(item) {
  if (!item || typeof item !== "object") {
    return null;
  }
  const parentKey = textOf(item.parentKey);
  const key = parentKey || textOf(item.key);
  const libraryID = libraryIdOf(item.libraryID);
  if (!key || libraryID === null) {
    return null;
  }
  return Object.freeze({ libraryID, key });
}

/** The comparison set's dedupe key: one paper per library. */
export function targetKeyOf(target) {
  return `${target.libraryID}:${target.key}`;
}

/** The service's paper id for one target (`kbapp/library/sources.py`). */
export function paperIdOf(target) {
  return `z:${target.libraryID}:${target.key}`;
}

/**
 * Normalise a Zotero selection into deduped, addressable paper targets.
 *
 * `skipped` counts the selected items that cannot become a paper id (session
 * items without a library, notes without a key, …). The caller logs it: a
 * silently dropped selection is the "click does nothing" state.
 */
export function targetsOf(items) {
  const list = Array.isArray(items) ? items : [];
  const targets = [];
  const seen = new Set();
  let skipped = 0;
  for (const item of list) {
    const target = targetOf(item);
    if (!target) {
      skipped += 1;
      continue;
    }
    const key = targetKeyOf(target);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    targets.push(target);
  }
  return Object.freeze({ targets: Object.freeze(targets), skipped });
}

/** The empty comparison set. */
export const EMPTY_COMPARE_SET = Object.freeze([]);

/**
 * Add targets to the comparison set.
 *
 * Rules, all covered by tests:
 *   - dedupe by `libraryID:key`; a target already in the set is reported as
 *     `duplicate` and not added twice;
 *   - the set never exceeds COMPARE_MAX; the overflow is reported as `full`
 *     instead of silently evicting an earlier choice (dropping what the user
 *     picked is worse than saying "full");
 *   - order is insertion order, so the service's compare run receives the
 *     papers in the order the user collected them.
 *
 * Returns a frozen snapshot: `{ entries, added, duplicate, full, size }`.
 */
export function compareSetAdd(entries, targets) {
  const current = (Array.isArray(entries) ? entries : [])
    .map(targetOf)
    .filter(Boolean);
  const seen = new Set(current.map(targetKeyOf));
  let added = 0;
  let duplicate = 0;
  let full = 0;
  for (const raw of Array.isArray(targets) ? targets : []) {
    const target = targetOf(raw);
    if (!target) {
      continue;
    }
    const key = targetKeyOf(target);
    if (seen.has(key)) {
      duplicate += 1;
      continue;
    }
    if (current.length >= COMPARE_MAX) {
      full += 1;
      continue;
    }
    seen.add(key);
    current.push(target);
    added += 1;
  }
  return Object.freeze({
    entries: Object.freeze(current),
    added,
    duplicate,
    full,
    size: current.length,
  });
}

/** Remove one target by `libraryID:key`. */
export function compareSetRemove(entries, key) {
  const list = (Array.isArray(entries) ? entries : []).map(targetOf).filter(Boolean);
  const remaining = list.filter((target) => targetKeyOf(target) !== key);
  return Object.freeze({
    entries: Object.freeze(remaining),
    removed: remaining.length !== list.length,
  });
}

/** The comparison set as service paper ids, in insertion order. */
export function compareSetPaperIds(entries) {
  return Object.freeze(
    (Array.isArray(entries) ? entries : []).map(targetOf).filter(Boolean).map(paperIdOf),
  );
}

/**
 * Ready to compare? 2–4 papers — the service's own bound for
 * `kind: "compare"` (`kbapp/workflows/agents.py`: 「比较需要2至4篇论文」).
 */
export function compareReadiness(entries) {
  const size = Array.isArray(entries) ? entries.length : 0;
  if (size < COMPARE_MIN || size > COMPARE_MAX) {
    return Object.freeze({ size, ready: false, reasonKey: GATE_REASON_KEYS["compare-size"] });
  }
  return Object.freeze({ size, ready: true, reasonKey: null });
}

/**
 * The compare-run submission for already-resolved paper ids, or null when the
 * resolved set is not in 2–4.
 *
 * `paper_ids` must be ids the service can resolve: `z:<libraryID>:<key>` only
 * names a paper when the stored id is that exact string, and a paper stored
 * under a legacy `doi:` id is not reachable through it
 * (`kbapp/storage/store.py`: the aliases table holds id / citekey /
 * openalex_id / doi, and `store.resolve()` otherwise returns the input
 * unchanged). The edge therefore resolves every target through
 * `GET /api/v2/paper/sections` first and submits the resolved `paper_id`s.
 */
export function compareRunBody(paperIds) {
  const list = Array.isArray(paperIds) ? paperIds : [];
  const ids = [];
  const seen = new Set();
  for (const raw of list) {
    const id = typeof raw === "string" ? raw.trim() : "";
    if (!id || seen.has(id)) {
      continue;
    }
    seen.add(id);
    ids.push(id);
  }
  if (ids.length < COMPARE_MIN || ids.length > COMPARE_MAX) {
    return null;
  }
  return Object.freeze({
    path: "/api/agent-runs",
    body: Object.freeze({ kind: "compare", paper_ids: Object.freeze(ids) }),
  });
}

/**
 * The compare-run submission for a comparison set in the plugin's own
 * `z:<libraryID>:<key>` form, or null when the set is not in 2–4.
 */
export function compareRunRequest(entries) {
  return compareRunBody(compareSetPaperIds(entries));
}

/**
 * Split per-target resolution results into the ids the service can reach and
 * the targets it cannot.
 *
 * `pairs` is one `{ paperId, resolvedId }` per target, in set order;
 * `resolvedId` is the service's `paper_id` (from `/api/v2/paper/sections`) or
 * null/empty when the paper is not in the library. The caller must not submit
 * a partially resolved run: a `z:` id the service cannot resolve makes
 * `create_run()` answer 400 for the whole run, which would hide the actually
 * usable papers.
 */
export function resolutionOutcome(pairs) {
  const list = Array.isArray(pairs) ? pairs : [];
  const resolved = [];
  const missing = [];
  for (const pair of list) {
    const paperId = typeof pair?.paperId === "string" ? pair.paperId : "";
    const resolvedId =
      typeof pair?.resolvedId === "string" ? pair.resolvedId.trim() : "";
    if (!paperId) {
      continue;
    }
    if (resolvedId) {
      resolved.push(resolvedId);
    } else {
      missing.push(paperId);
    }
  }
  return Object.freeze({
    resolved: Object.freeze(resolved),
    missing: Object.freeze(missing),
  });
}

/** The empty selection shape used when a target passes no items. */
export const EMPTY_SELECTION = Object.freeze({ count: 0 });

/** Accept a count, a target list or a `{count}` object. */
export function normalizeSelection(value) {
  if (Array.isArray(value)) {
    return Object.freeze({ count: value.length });
  }
  if (typeof value === "number" && Number.isInteger(value) && value >= 0) {
    return Object.freeze({ count: value });
  }
  if (
    value &&
    typeof value === "object" &&
    Number.isInteger(value.count) &&
    value.count >= 0
  ) {
    return Object.freeze({ count: value.count });
  }
  return EMPTY_SELECTION;
}

/**
 * The menu context every rule reads.
 *
 * `health` is the S4 verdict (null before the first probe), `selection` the
 * number of addressable targets, `compareSize` the comparison set's size.
 * A pre-computed `service` string wins over `health`, which lets the edge
 * keep one cached classification instead of re-deriving it per entry.
 */
export function menuContext(input = {}) {
  const given = input && typeof input === "object" ? input : {};
  const known = Object.values(SERVICE_STATES);
  const service =
    typeof given.service === "string" && known.includes(given.service)
      ? given.service
      : serviceStateOf(given.health, { pending: given.pending === true });
  const compareSize =
    Number.isInteger(given.compareSize) && given.compareSize >= 0 ? given.compareSize : 0;
  return Object.freeze({
    service,
    selection: normalizeSelection(given.selection),
    compareSize,
  });
}

/**
 * Accept either an already-normalised context (anything `menuContext()`
 * returned) or a raw input, so the exported predicates cannot be called with
 * a half-built context and silently read `undefined` as "not offline".
 */
function asContext(context) {
  if (
    context &&
    typeof context === "object" &&
    context.selection &&
    typeof context.selection === "object"
  ) {
    return context;
  }
  return menuContext(context);
}

/** Whether an entry shows in this context. */
export function visibleOf(entry, context) {
  const ctx = asContext(context);
  if (entry.visible === "offline") {
    return (
      ctx.service === SERVICE_STATES.OFFLINE ||
      ctx.service === SERVICE_STATES.INCOMPATIBLE
    );
  }
  if (entry.visible === "compare") {
    return ctx.compareSize > 0;
  }
  return true;
}

/**
 * Why an entry is greyed out, or null when it is enabled.
 *
 * Service first: with the service down, that is the fact to act on. Then the
 * selection gate, then the comparison set's size.
 */
export function gateReasonOf(entry, context) {
  const ctx = asContext(context);
  if (entry.needsService && ctx.service !== SERVICE_STATES.ONLINE) {
    return GATE_REASON_KEYS[ctx.service] ?? GATE_REASON_KEYS.checking;
  }
  if (entry.selection === "any" && ctx.selection.count < 1) {
    return GATE_REASON_KEYS.selection;
  }
  if (entry.selection === "single" && ctx.selection.count !== 1) {
    return GATE_REASON_KEYS.single;
  }
  if (entry.compare === "add" && ctx.compareSize >= COMPARE_MAX) {
    return GATE_REASON_KEYS["compare-full"];
  }
  if (
    entry.compare === "run" &&
    (ctx.compareSize < COMPARE_MIN || ctx.compareSize > COMPARE_MAX)
  ) {
    return GATE_REASON_KEYS["compare-size"];
  }
  // "clear" needs no reason: it is hidden when there is nothing to clear.
  return null;
}

/** One entry's rendering state. */
export function entryStateOf(entry, context) {
  const ctx = menuContext(context);
  const reasonKey = gateReasonOf(entry, ctx);
  return Object.freeze({
    id: entry.id,
    group: entry.group,
    labelKey: entry.labelKey,
    command: entry.command,
    menuType: entry.menuType,
    visible: visibleOf(entry, ctx),
    enabled: reasonKey === null,
    reasonKey,
  });
}

/** Every registered entry of one group, with its state. */
export function menuStates(groupId, context = {}) {
  const ctx = menuContext(context);
  return Object.freeze(
    registeredEntries(groupId).map((entry) => entryStateOf(entry, ctx)),
  );
}

/**
 * The static skeleton src/menus/menus.ts turns into `registerMenu()` options.
 * Tests assert it directly: no empty `menus` array, no blocked entry, no
 * separator at the top level of a grouped target.
 */
export function menuSpecs() {
  return Object.freeze(
    MENU_GROUPS.map((group) =>
      Object.freeze({
        id: group.id,
        target: group.target,
        menuID: group.menuID,
        labelKey: group.labelKey,
        shape: group.shape,
        entries: Object.freeze(
          registeredEntries(group.id).map((entry) =>
            Object.freeze({
              id: entry.id,
              labelKey: entry.labelKey,
              command: entry.command,
              menuType: entry.menuType,
            }),
          ),
        ),
      }),
    ),
  );
}

/**
 * The offline guide card's contract.
 *
 * The card is the answer to 「服务离线时入口为什么是灰的」. It must carry the
 * installation budget (体积与时长) the first-run experience promises, and it
 * must state that no entry ever degrades into a half feature. Every string is
 * a Fluent key; tests assert each one exists in both bundles.
 */
export const GUIDE_CARD = Object.freeze({
  titleKey: "menu-guide-title",
  bodyKeys: Object.freeze([
    "menu-guide-body-offline",
    "menu-guide-body-start",
    "menu-guide-budget-size",
    "menu-guide-budget-time",
    "menu-guide-body-no-half",
  ]),
  incompatibleKey: "menu-guide-body-incompatible",
  actions: Object.freeze([
    Object.freeze({ id: "settings", labelKey: "menu-guide-open-settings" }),
    Object.freeze({ id: "recheck", labelKey: "menu-guide-recheck" }),
    Object.freeze({ id: "close", labelKey: "menu-guide-close" }),
  ]),
});

/** The recheck button's three lines, as Fluent keys. */
export const GUIDE_STATUS_KEYS = Object.freeze({
  checking: "menu-guide-status-checking",
  online: "menu-guide-status-online",
  incompatible: "menu-guide-status-incompatible",
  offline: "menu-guide-status-offline",
});

/**
 * The guide card for one service state. The reason line differs between
 * 「服务未运行」 and 「服务版本不兼容」, because the user's next step differs:
 * start it, versus update it.
 */
export function guideView(context = {}) {
  const ctx = menuContext(context);
  const bodyKeys = Object.freeze([
    ctx.service === SERVICE_STATES.INCOMPATIBLE
      ? GUIDE_CARD.incompatibleKey
      : GUIDE_CARD.bodyKeys[0],
    ...GUIDE_CARD.bodyKeys.slice(1),
  ]);
  return Object.freeze({
    service: ctx.service,
    titleKey: GUIDE_CARD.titleKey,
    bodyKeys,
    actions: GUIDE_CARD.actions,
  });
}

/** User-visible feedback lines the edge shows through a progress window. */
export const FEEDBACK_KEYS = Object.freeze({
  headline: "menu-feedback-headline",
  compareAdded: "menu-feedback-compare-added",
  compareDuplicate: "menu-feedback-compare-duplicate",
  compareFull: "menu-feedback-compare-full",
  compareCleared: "menu-feedback-compare-cleared",
  compareSubmitted: "menu-feedback-compare-submitted",
  compareUnknown: "menu-feedback-compare-unknown",
  compareUnindexed: "menu-feedback-compare-unindexed",
  compareError: "menu-feedback-compare-error",
  parseSubmitted: "menu-feedback-parse-submitted",
  parseUnknown: "menu-feedback-parse-unknown",
  parseNotIndexed: "menu-feedback-parse-not-indexed",
  parseError: "menu-feedback-parse-error",
  diagnosticsUnavailable: "menu-feedback-diagnostics-unavailable",
  tabUnavailable: "menu-feedback-tab-unavailable",
  selectFirst: "menu-feedback-select-first",
});

/** Which feedback line one 「加入对比集」 result deserves. */
export function compareAddFeedback(result) {
  if (!result || result.added === 0) {
    return result && result.duplicate > 0
      ? FEEDBACK_KEYS.compareDuplicate
      : FEEDBACK_KEYS.compareFull;
  }
  return FEEDBACK_KEYS.compareAdded;
}

/**
 * Every Fluent key this feature renders. tests/test_plugin_menus.cjs holds
 * this list against both .ftl bundles, so a forgotten key fails in CI instead
 * of rendering as a raw id on a real machine.
 */
export function menuFluentKeys() {
  const keys = new Set();
  for (const group of MENU_GROUPS) {
    if (group.labelKey) {
      keys.add(group.labelKey);
    }
  }
  for (const entry of MENU_ENTRIES) {
    keys.add(entry.labelKey);
    if (entry.blockedReasonKey) {
      keys.add(entry.blockedReasonKey);
    }
  }
  for (const key of Object.values(GATE_REASON_KEYS)) {
    keys.add(key);
  }
  keys.add(GUIDE_CARD.titleKey);
  for (const key of GUIDE_CARD.bodyKeys) {
    keys.add(key);
  }
  keys.add(GUIDE_CARD.incompatibleKey);
  for (const action of GUIDE_CARD.actions) {
    keys.add(action.labelKey);
  }
  for (const key of Object.values(GUIDE_STATUS_KEYS)) {
    keys.add(key);
  }
  for (const key of Object.values(FEEDBACK_KEYS)) {
    keys.add(key);
  }
  return Object.freeze([...keys].sort());
}

/**
 * How long a settled health verdict may be reused.
 *
 * Menus open on user action, so probing on every open would be a request per
 * right-click. Five seconds is short enough that a service started a moment
 * ago shows up on the next open, and long enough that opening the submenu,
 * the tools menu and the guide card costs one probe.
 */
export const PROBE_TTL_MS = 5000;

/**
 * The last settled health verdict, with a TTL.
 *
 * Deliberately not a timer: the status dot already polls, and a second poller
 * would double the request budget for no new information. The menu probes on
 * demand (on showing) and reuses the answer for PROBE_TTL_MS.
 */
export function createVerdictCache(options = {}) {
  const ttl = Number.isFinite(options.ttl) ? options.ttl : PROBE_TTL_MS;
  let value = null;
  let at = null;
  return {
    read() {
      return value;
    },
    stale(now = Date.now()) {
      return at === null || now - at >= ttl;
    },
    write(health, now = Date.now()) {
      value = health ?? null;
      at = now;
      return value;
    },
    clear() {
      value = null;
      at = null;
    },
  };
}
