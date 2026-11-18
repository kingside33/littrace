import { config } from "../package.json";
import { t } from "./utils/locale";
import {
  acceptResponse,
  cacheKeyFor,
  canParse,
  createPanelCache,
  createRenderGeneration,
  createSectionRegistration,
  idleState,
  paperIdFor,
  planRender,
  sectionRows,
  settleRender,
  snapshotPath,
  workbenchUrl,
} from "./itemSectionsCore.mjs";
import type {
  PanelCache,
  PanelState,
  RenderGeneration,
  SectionID,
  SectionRegistration,
  SectionRow,
} from "./itemSectionsCore.mjs";

/**
 * The three item-pane sections (U2): 证据 / 谱系关系 / 精读.
 *
 * Division of labour, unchanged from the other features in this plugin:
 *
 *   - itemSectionsCore.mjs decides *what* a section shows: the cache key, the
 *     loading → ready/empty/error/offline state machine, the stale-answer
 *     guard, the rows. tests/test_plugin_sections.cjs runs that logic.
 *   - this file only maps a decided state onto DOM nodes, and supplies the
 *     one thing the tests cannot: a real request through the S4 client.
 *
 * Lifecycle follows the documented `Zotero.ItemPaneManager.registerSection`
 * hooks (itemPaneManager.js:485-497): `onRender` is synchronous and creates
 * the skeleton, `onAsyncRender` fills it, `onDestroy` releases the mount.
 * Registration goes through src/registry.ts so disable/uninstall reclaims the
 * sections through the paneID Zotero returned.
 */

const pluginID: string = config.addonID;

/**
 * Icons ship inside the XPI and are referenced through the `littrace` chrome
 * namespace registered by addon/bootstrap.js. 20x20 for the sidenav and 16x16
 * for the header, both sized by Zotero's own CSS, both `context-fill` so they
 * follow the host theme instead of hardcoding a colour.
 */
const ICON_BASE = "chrome://littrace/content/icons";

/** Class prefix for everything this feature injects, so cleanup is exact. */
const ROOT_CLASS = "littrace-section";

interface SectionMount {
  doc: Document;
  body: HTMLElement;
  cacheKey: string;
  state: PanelState;
  generation: RenderGeneration;
  disposed: boolean;
}

/**
 * One cache for all three sections: the snapshot endpoint answers all three
 * at once, so switching an item must cost one request, not three. The loader
 * is created lazily against the plugin's shared S4 client (see `createSections`).
 */
let cache: PanelCache | null = null;

function panelCache(): PanelCache {
  if (!cache) {
    cache = createPanelCache({
      load: async (cacheKey: string) => addon.service.get(snapshotPath(cacheKey)),
      limit: 64,
    });
  }
  return cache;
}

/** Drop the cache on shutdown: a disabled plugin must leave no snapshot behind. */
export function resetSectionCache(): void {
  cache?.clear();
  cache = null;
}

function element<K extends keyof HTMLElementTagNameMap>(
  doc: Document,
  tag: K,
  className?: string,
): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  if (className) {
    node.className = className;
  }
  return node;
}

/* ── State rendering ─────────────────────────────────────────────────── */

function renderSkeleton(doc: Document): HTMLElement {
  const box = element(doc, "div", `${ROOT_CLASS}-skeleton`);
  box.setAttribute("aria-busy", "true");
  for (const width of ["72%", "54%", "63%"]) {
    const line = element(doc, "div", `${ROOT_CLASS}-line`);
    line.style.width = width;
    box.append(line);
  }
  return box;
}

function renderNote(
  doc: Document,
  state: PanelState,
  onAction: (actionKey: string) => void,
): HTMLElement {
  const box = element(doc, "div", `${ROOT_CLASS}-note`);
  // The message key is the contract; a missing FTL entry falls back to the
  // raw key (see utils/locale.ts) so a forgotten string is visible, not blank.
  const text = element(doc, "p", `${ROOT_CLASS}-text`);
  text.textContent = state.messageKey ? t(state.messageKey) : "";
  box.append(text);

  if (state.status === "offline") {
    // The guide card: what the user has to do, not just that it failed.
    const guide = element(doc, "p", `${ROOT_CLASS}-hint`);
    guide.textContent = t("section-offline-guide");
    box.append(guide);
    box.dataset.state = "offline";
  }

  if (state.actionKey) {
    const action = element(doc, "button", `${ROOT_CLASS}-action`);
    action.type = "button";
    action.textContent = t(state.actionKey);
    action.addEventListener("click", () => onAction(state.actionKey as string));
    box.append(action);
  }
  return box;
}

function renderError(
  doc: Document,
  state: PanelState,
  onRetry: () => void,
): HTMLElement {
  // One line of error text plus the retry button, per the design's section
  // error state: the message is the service's own words where it has any.
  const box = element(doc, "div", `${ROOT_CLASS}-error`);
  box.dataset.state = "error";
  const text = element(doc, "p", `${ROOT_CLASS}-text`);
  text.textContent = `${t("section-error")}：${state.error ?? ""}`;
  box.append(text);
  const retry = element(doc, "button", `${ROOT_CLASS}-retry`);
  retry.type = "button";
  retry.textContent = t("section-retry");
  retry.addEventListener("click", onRetry);
  box.append(retry);
  return box;
}

const DIRECTION_KEYS: Record<string, string> = {
  previous: "section-direction-previous",
  following: "section-direction-following",
  unknown: "section-direction-unknown",
};

function renderRow(
  doc: Document,
  sectionID: SectionID,
  row: SectionRow,
): HTMLElement {
  const line = element(doc, "div", `${ROOT_CLASS}-row`);
  line.dataset.rowKind = row.kind;

  if (row.kind === "count" || row.kind === "page-class") {
    const label = element(doc, "span", `${ROOT_CLASS}-label`);
    label.textContent = t(row.labelKey);
    const value = element(doc, "span", `${ROOT_CLASS}-value`);
    value.textContent = row.value;
    line.append(label, value);
    return line;
  }

  if (row.kind === "excerpt") {
    if (row.page > 0) {
      const page = element(doc, "span", `${ROOT_CLASS}-page`);
      page.textContent = `p.${row.page}`;
      line.append(page);
    }
    const text = element(doc, "span", `${ROOT_CLASS}-excerpt`);
    text.textContent = row.text;
    line.append(text);
    return line;
  }

  if (row.kind === "relation") {
    const badge = element(doc, "span", `${ROOT_CLASS}-badge`);
    badge.textContent = row.label;
    // Grade is data, not decoration: "题目线索，待核" must not look verified.
    badge.dataset.grade = row.grade;
    line.append(badge);

    const peer = element(doc, "span", `${ROOT_CLASS}-peer`);
    peer.textContent = row.peerYear
      ? `${row.peerTitle}（${row.peerYear}）`
      : row.peerTitle;
    line.append(peer);

    const direction = element(doc, "span", `${ROOT_CLASS}-direction`);
    direction.textContent = t(DIRECTION_KEYS[row.direction] ?? DIRECTION_KEYS.unknown);
    line.append(direction);

    if (row.gradeLabel) {
      line.title = row.note ? `${row.gradeLabel} · ${row.note}` : row.gradeLabel;
    }
    return line;
  }

  if (row.kind === "coverage") {
    const label = element(doc, "span", `${ROOT_CLASS}-label`);
    label.textContent = t(`section-reading-state-${row.state}`);
    const value = element(doc, "span", `${ROOT_CLASS}-value`);
    value.textContent = `${row.readPages}/${row.totalPages}`;
    line.append(label, value);
    return line;
  }

  // gap
  const gap = element(doc, "span", `${ROOT_CLASS}-gap`);
  gap.textContent = row.text;
  line.append(gap);
  // `sectionID` is unused for the gap row but keeps the renderer's signature
  // honest: every row is rendered for a named section.
  line.dataset.section = sectionID;
  return line;
}

function renderSnapshot(
  doc: Document,
  sectionID: SectionID,
  state: PanelState,
): HTMLElement {
  const box = element(doc, "div", `${ROOT_CLASS}-snapshot`);
  box.dataset.state = "ready";

  for (const row of sectionRows(sectionID, state.snapshot)) {
    box.append(renderRow(doc, sectionID, row));
  }

  if (sectionID === "evidence" && state.snapshot?.evidence?.indexed) {
    const open = element(doc, "button", `${ROOT_CLASS}-action`);
    open.type = "button";
    open.textContent = t("section-open-search");
    open.addEventListener("click", () => openSearch(state));
    box.append(open);
  }

  if (sectionID === "lineage") {
    const counts = element(doc, "p", `${ROOT_CLASS}-hint`);
    const lineage = state.snapshot?.lineage ?? {};
    counts.textContent = `${t("section-lineage-pending")}：${lineage.pending ?? 0} · ${t("section-lineage-citations")}：${lineage.citations ?? 0}`;
    box.append(counts);
  }

  if (sectionID === "reading" && canParse(state.snapshot)) {
    // Its own class, so the 证据 section's "open search" and this button can
    // never be confused by a querySelector.
    const parse = element(doc, "button", `${ROOT_CLASS}-parse`);
    parse.type = "button";
    parse.textContent = t("section-parse");
    parse.addEventListener("click", () => {
      void requestParse(doc, state, parse);
    });
    box.append(parse);
  }

  return box;
}

/* ── Navigation ──────────────────────────────────────────────────────── */

/**
 * Entry points into the service UI. Both open the local workbench with the
 * paper preselected. The URL shape is decided by `workbenchUrl()`: shell.js:59
 * reads the paper from `location.search` and the page from the hash, so the
 * query must come *before* the hash (`/?paper=…#graph`) — a `?paper=` inside
 * the hash is not parsed and the page falls through to 研究调度台.
 *
 * The paper id is the *resolved* one from the snapshot, not the plugin's
 * lookup key: `/api/paper` resolves through `store.resolve()`, which does not
 * map `z:<lib>:<key>` for a paper whose stored id is a legacy `doi:` form.
 *
 * The tab host is U4's job; until it exists this opens the default browser,
 * which is honest — the section itself is complete and never pretends to have
 * in-app navigation it does not have.
 */
function openSearch(state: PanelState): void {
  openWorkbench("search", state);
}

function openLineage(state: PanelState): void {
  openWorkbench("graph", state);
}

function openWorkbench(page: string, state: PanelState): void {
  try {
    Zotero.launchURL(workbenchUrl(addon.service.baseUrl, page, paperIdFor(state)));
  } catch (error) {
    Zotero.logError(
      error instanceof Error
        ? new Error(`littrace: 打开工作台失败：${error.message}`)
        : (error as Error),
    );
  }
}

/**
 * Submit a parse job through the S4 client, which owns the session token and
 * the request discipline. The button reports the outcome in place: a queued
 * job and a failure must not look the same.
 *
 * The submitted id is the *resolved* paper id from the snapshot, never the
 * plugin's lookup key: `/api/documents/process` (document_api.py:127-136) does
 * `papers().get(store.resolve(paper_id))`, and `store.resolve` does not map
 * `z:<lib>:<key>` for a paper stored under a legacy `doi:` id — it returns the
 * key unchanged, `papers.get(...)` is None, and the endpoint answers 400 with
 * 「请选择库内文献」. The snapshot's `paper_id` is exactly that resolved id.
 */
async function requestParse(
  doc: Document,
  state: PanelState,
  button: HTMLButtonElement,
): Promise<void> {
  const paperId = paperIdFor(state);
  if (!paperId) {
    return;
  }
  button.disabled = true;
  const previous = button.textContent;
  button.textContent = t("section-parse-submitting");
  try {
    const result = await addon.service.submitTask("/api/documents/process", {
      paper_id: paperId,
    });
    // `queued` names a task; `started`/`running` are the service's idless
    // outcomes; `unknown` is not a success (S4 ADR 0020).
    button.textContent =
      result.outcome === "unknown" ? t("section-parse-unknown") : t("section-parse-queued");
    // Coverage is now out of date: drop the snapshot so the next render refetches.
    panelCache().invalidate(state.cacheKey);
  } catch (error) {
    button.textContent = previous ?? t("section-parse");
    button.disabled = false;
    button.after(
      renderError(
        doc,
        {
          cacheKey: state.cacheKey,
          status: "error",
          messageKey: "section-error",
          actionKey: null,
          error: (error as Error)?.message ?? String(error),
          retryable: true,
          snapshot: null,
        },
        () => {
          void requestParse(doc, state, button);
        },
      ),
    );
    report(error, "parse request");
    return;
  }
  button.disabled = false;
}

function report(error: unknown, context: string): void {
  const message = error instanceof Error ? error.message : String(error);
  Zotero.logError(new Error(`littrace: item section ${context} failed: ${message}`));
}

/* ── Mount lifecycle ─────────────────────────────────────────────────── */

function renderState(
  mount: SectionMount,
  sectionID: SectionID,
  state: PanelState,
): number {
  const { doc, body, generation } = mount;
  mount.state = state;
  // Every paint takes a new generation. An answer that arrives after a newer
  // paint is therefore recognisable and can be dropped (see paintSection).
  const token = generation.begin();
  // Replace children rather than diff: the section is a handful of rows, and
  // one code path for every state removes the "stale row left behind" class.
  body.replaceChildren();

  if (!state.cacheKey) {
    // Nothing to show and nothing to ask: a collection row is not a paper.
    return token;
  }

  switch (state.status) {
    case "loading":
      body.append(renderSkeleton(doc));
      return token;
    case "empty":
    case "offline":
      body.append(renderNote(doc, state, (actionKey) => {
        if (actionKey === "section-open-lineage") {
          openLineage(state);
        } else {
          openSearch(state);
        }
      }));
      return token;
    case "error":
      body.append(
        renderError(doc, state, () => {
          if (!mount.generation.isCurrent(token)) {
            return;
          }
          void paintSection(mount, sectionID, { force: true });
        }),
      );
      return token;
    default:
      body.append(renderSnapshot(doc, sectionID, state));
      return token;
  }
}

/**
 * Paint one section for its current item, synchronously from the cache and
 * asynchronously on a miss.
 *
 * `force` is the retry path: it skips the cached value so "重试" actually
 * re-queries instead of redrawing the same failure.
 */
async function paintSection(
  mount: SectionMount,
  sectionID: SectionID,
  options: { force?: boolean } = {},
): Promise<void> {
  if (mount.disposed) {
    return;
  }
  const owner = mount.cacheKey;
  if (!owner) {
    return;
  }
  if (options.force) {
    panelCache().invalidate(owner);
  }
  const cached = options.force ? null : panelCache().peek(owner);
  const plan = planRender(sectionID, owner, cached);
  const token = renderState(mount, sectionID, plan.state);
  if (!plan.request) {
    return;
  }

  let outcome: { snapshot?: any; error?: unknown };
  try {
    outcome = { snapshot: await panelCache().fetch(owner) };
  } catch (error) {
    outcome = { error };
    if ((error as { kind?: string } | null)?.kind !== "offline") {
      report(error, sectionID);
    }
  }
  // Three guards, all needed. The mount may be gone (disabled while in
  // flight), re-bound to another item (the user switched selection), or
  // re-painted by a newer render of the same item (retry). Painting after any
  // of those would put one item's rows under another item's header — the
  // exact "串数据" failure this feature has to rule out.
  if (mount.disposed || mount.cacheKey !== owner) {
    return;
  }
  if (!mount.generation.isCurrent(token)) {
    return;
  }
  const next = settleRender(sectionID, owner, outcome);
  const accepted = acceptResponse(mount.state, owner, next);
  if (!accepted) {
    return;
  }
  renderState(mount, sectionID, accepted);
}

/* ── Registration ────────────────────────────────────────────────────── */

const mounts = new Map<string, SectionMount>();

function mountFor(
  sectionID: SectionID,
  doc: Document,
  body: HTMLElement,
): SectionMount {
  const mount: SectionMount = {
    doc,
    body,
    cacheKey: "",
    state: idleState(),
    generation: createRenderGeneration(),
    disposed: false,
  };
  mounts.set(sectionID, mount);
  return mount;
}

/**
 * Rebind a mount to the current item, dropping the previous item's paint.
 *
 * `onItemChange` must not render (itemPaneManager.js:477-483: "Update data ...
 * Don't render/refresh UI"), so this only rebinds; `onRender` — which Zotero
 * calls right after — does the painting. Dropping the rows here also means a
 * switched item can never inherit the previous one's DOM, even for one frame.
 */
function bindItem(mount: SectionMount, item?: any): void {
  mount.cacheKey = cacheKeyFor(item);
  mount.state = idleState();
  mount.body.replaceChildren();
}

function registerOne(sectionID: SectionID): SectionRegistration {
  return createSectionRegistration(
    Zotero.ItemPaneManager,
    pluginID,
    sectionID,
    {
      onInit({ doc, body }: { doc: Document; body: HTMLElement }) {
        mountFor(sectionID, doc, body);
      },
      onDestroy() {
        const mount = mounts.get(sectionID);
        if (mount) {
          mount.disposed = true;
          mounts.delete(sectionID);
        }
      },
      onItemChange({ item, setEnabled }: { item?: any; setEnabled: (yes: boolean) => void }) {
        const mount = mounts.get(sectionID);
        if (!mount) {
          return;
        }
        // Collections / saved searches and non-library rows have no snapshot:
        // hide the section instead of asking the service about a non-paper.
        setEnabled(Boolean(cacheKeyFor(item)));
        bindItem(mount, item);
      },
      // The one place a section paints. Synchronous skeleton first (official
      // JSDoc: "Cannot be async"), then the async fill from the same call, so
      // no second hook is needed and there is no path that paints twice.
      onRender({ doc, body, item }: { doc: Document; body: HTMLElement; item?: any }) {
        let mount = mounts.get(sectionID);
        if (!mount || mount.doc !== doc || mount.body !== body) {
          mount = mountFor(sectionID, doc, body);
        }
        const key = cacheKeyFor(item);
        if (key === mount.cacheKey && mount.state.status !== "empty") {
          // Same item, already painted or painting: Zotero re-renders on
          // scroll and focus, and a duplicate must not restart the request.
          return;
        }
        mount.cacheKey = key;
        // paintSection paints synchronously from the cache and only then
        // awaits a miss: skeleton first, content after.
        void paintSection(mount, sectionID);
      },
    },
    ICON_BASE,
    config.addonRef,
  );
}

/**
 * Register the three sections. Called once from src/registry.ts
 * (registerWindowIndependent): the pane manager keeps one process-wide
 * registry, and a second registration would be a duplicate-id failure.
 *
 * A rejected section is logged by the caller, never thrown: one broken
 * section must not cost the other two.
 */
export function createItemSections(): SectionRegistration[] {
  return [registerOne("evidence"), registerOne("lineage"), registerOne("reading")];
}

/** Release every mount (shutdown): the oracle-cleared pane must hold no nodes. */
export function disposeItemSections(): void {
  for (const mount of mounts.values()) {
    mount.disposed = true;
  }
  mounts.clear();
  resetSectionCache();
}
