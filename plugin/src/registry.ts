import { createRegistry } from "./registryCore.mjs";
import { registerReaderEvents } from "./readerPanel";
import { createLineageColumn } from "./columns/lineage";
import { createTabHost } from "./tabs/tabs";
import { createItemSections, disposeItemSections } from "./itemSections";

/**
 * The single registration entry of the plugin.
 *
 * Every feature that registers something with Zotero (item columns, item
 * sections, reader panels, menus, notifiers, deep-link bridges) must do two
 * things here and nowhere else:
 *   1. perform the registration inside `registerAll` (window-level) or
 *      `registerWindowIndependent` (window-independent), and
 *   2. hand a disposer to `register(win, id, dispose)` /
 *      `registerGlobal(id, dispose)` so it can be reclaimed.
 *
 * Scoping: Zotero can have several main windows open at once, and startup
 * runs `registerAll` once per existing window. Registrations are therefore
 * keyed by (window, id): the same id may be registered once per window, and
 * a duplicate id is an error only within one window. Closing a window
 * disposes only that window's entries; shutdown disposes everything.
 *
 * A *process-wide* registry with no window dimension (item-tree columns,
 * which Zotero stores once and every tree reads, and the reader events) goes
 * into `registerWindowIndependent`: it runs once per startup, and only
 * shutdown reclaims it.
 *
 * The bookkeeping itself lives in registryCore.mjs (plain ESM, no Zotero
 * globals) so tests/test_plugin_registry.cjs can exercise it directly.
 */

export type Disposer = () => void;

/** Guards registerWindowIndependent against a second startup path. */
let globalsRegistered = false;

const registry = createRegistry((error: unknown, id: string) => {
  Zotero.logError(
    error instanceof Error
      ? new Error(`littrace: registration "${id}" disposer failed: ${error.message}`)
      : (error as Error),
  );
});

/**
 * Register `id` for one main window. Registering the same id for a different
 * window is expected; registering it twice for the same window throws.
 */
export function register(win: Window, id: string, dispose: Disposer): void {
  registry.register(win, id, dispose);
}

/**
 * Register something window-independent (a notifier or deep-link bridge).
 * Reclaimed on shutdown, not when an individual window closes.
 */
export function registerGlobal(id: string, dispose: Disposer): void {
  registry.registerGlobal(id, dispose);
}

/**
 * Register every window-level feature. Called once per main-window load;
 * the window is claimed first so the same window arriving through two
 * startup paths is not registered twice.
 */
export async function registerAll(win: Window): Promise<void> {
  if (!registry.claimWindow(win)) {
    return;
  }
  // Four iframe function tabs plus the host bridge (U4). Window-scoped: the
  // iframes live in this window's tab deck, the lineage-locate listener
  // listens on this window, and closing the window reclaims every node,
  // listener and timer through the tab host's cleanup table.
  const tabHost = createTabHost(win);
  register(win, "iframe-tabs", () => {
    tabHost.dispose();
  });
  // Future features hook in here; each keeps its own
  // `register(win, id, dispose)`. Reader events and the item-tree column are
  // NOT registered here: see registerWindowIndependent() below.
}

/**
 * Register every window-independent feature. Called once from startup, NOT
 * once per window: `Zotero.Reader.registerEventListener` is global, so
 * registering it per main window would inject the selection buttons twice
 * and the second `unregisterEventListener` would remove the first handler's
 * twin; likewise `Zotero.ItemTreeManager.registerColumn` keeps one
 * process-wide column registry that every library tree reads. Reclaimed on
 * shutdown only, per the (window, id) / global split in
 * docs/adr/0009-registry-keyed-by-window.md.
 */
export function registerWindowIndependent(): void {
  if (globalsRegistered) {
    return;
  }
  globalsRegistered = true;
  // Reader events: the evidence-Q&A panel and the two selection buttons.
  // The disposer unregisters both handlers and closes any panel it opened,
  // so a disabled plugin leaves no listener or DOM behind in an open reader.
  registerGlobal("reader-panel", registerReaderEvents());
  // 「谱系」item-tree column (A2). Registered only when Zotero accepted the
  // option; a rejection is already logged and must not abort startup.
  const lineage = createLineageColumn();
  if (lineage.dataKey) {
    registerGlobal("item-column:lineage", () => {
      lineage.dispose();
    });
  }
  // 条目面板三 section(U2): 证据 / 谱系关系 / 精读. Process-wide like the
  // column and the reader events: ItemPaneManager keeps one section registry
  // that every item pane reads, so registering per window would duplicate
  // every section (ADR 0009). Sections Zotero rejected are skipped and logged
  // (createSectionRegistration never throws); one bad section must not cost
  // the other two.
  for (const section of createItemSections()) {
    if (!section.dataKey) {
      const paneID = (section.option as { paneID?: string } | null)?.paneID ?? "?";
      Zotero.logError(
        new Error(
          `littrace: 条目面板 section「${paneID}」注册失败(Zotero 拒绝了 section 选项),该 section 暂不可用。`,
        ),
      );
      continue;
    }
    registerGlobal(`item-section:${section.dataKey}`, () => {
      section.dispose();
    });
  }
  // The mounts and the snapshot cache outlive individual sections; release
  // them once, after the registrations above.
  registerGlobal("item-sections:mounts", () => {
    disposeItemSections();
  });
}

/**
 * Dispose only this window's registrations, in reverse order. Called when
 * that one window closes, so other open windows keep their registrations.
 */
export function unregisterWindow(win: Window): void {
  registry.unregisterWindow(win);
}

/**
 * Dispose every registration for every window plus the globals. Only for
 * shutdown: disabling or uninstalling the plugin must reclaim everything.
 */
export function unregisterAll(): void {
  registry.unregisterEverything();
  // Allow a later startup (disable → enable in the same session) to register
  // the window-independent features again.
  globalsRegistered = false;
}
