import { config } from "../package.json";
import { initLocale } from "./utils/locale";
import {
  registerAll,
  registerWindowIndependent,
  unregisterAll,
  unregisterWindow,
} from "./registry";
import { resetServiceClient } from "./service-client";
import { resetPaperData } from "./columns/paperData";

/**
 * Lifecycle dispatcher. bootstrap.js forwards the Zotero bootstrap hooks
 * here; real registration work happens in src/registry.ts, which is the
 * only place features may hook in.
 */

async function onStartup() {
  await Promise.all([
    Zotero.initializationPromise,
    Zotero.unlockPromise,
    Zotero.uiReadyPromise,
  ]);

  initLocale();

  // Window-independent registrations (notifiers, deep-link bridges, reader
  // events, the 「谱系」item-tree column) run exactly once. Registering them
  // per main window would double every selection-popup button (Zotero.Reader's
  // listeners are global) and the item-tree column (its registry is
  // process-wide).
  registerWindowIndependent();

  // Run the window-level registration for windows that already exist, so a
  // plugin loaded after startup registers on every open main window.
  await Promise.all(
    Zotero.getMainWindows().map((win: Window) => onMainWindowLoad(win)),
  );

  // Mark initialized as true to confirm plugin loading status outside of the
  // plugin (e.g. scaffold serve/test).
  addon.data.initialized = true;
}

async function onMainWindowLoad(win: Window): Promise<void> {
  await registerAll(win);
}

async function onMainWindowUnload(win: Window): Promise<void> {
  // Only this window's registrations: other main windows stay functional.
  unregisterWindow(win);
}

function onShutdown(): void {
  // Disable/uninstall: reclaim every window plus the window-independent ones.
  unregisterAll();
  // Drop the per-paper column cache (and its pending repaint) so a disabled
  // plugin holds no library data in memory.
  resetPaperData();
  // Drop the shared service client, and with it the cached session token.
  resetServiceClient();
  addon.data.alive = false;
  delete Zotero[config.addonInstance];
}

export default { onStartup, onShutdown, onMainWindowLoad, onMainWindowUnload };
