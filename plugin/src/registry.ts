import { createRegistry } from "./registryCore.mjs";
import { registerReaderEvents } from "./readerPanel";

/**
 * The single registration entry of the plugin.
 *
 * Every feature that registers something with Zotero (item columns, item
 * sections, reader panels, menus, notifiers, deep-link bridges) must do two
 * things here and nowhere else:
 *   1. perform the registration inside `registerAll`, and
 *   2. hand a disposer to `register(win, id, dispose)` so it can be reclaimed.
 *
 * Scoping: Zotero can have several main windows open at once, and startup
 * runs `registerAll` once per existing window. Registrations are therefore
 * keyed by (window, id): the same id may be registered once per window, and
 * a duplicate id is an error only within one window. Closing a window
 * disposes only that window's entries; shutdown disposes everything.
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
  // Future features hook in here; each keeps its own
  // `register(win, id, dispose)`. Reader events are NOT registered here: see
  // registerWindowIndependent() below.
}

/**
 * Register every window-independent feature. Called once from startup, NOT
 * once per window: `Zotero.Reader.registerEventListener` is global, so
 * registering it per main window would inject the selection buttons twice
 * and the second `unregisterEventListener` would remove the first handler's
 * twin. Reclaimed on shutdown only, per the (window, id) / global split in
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
