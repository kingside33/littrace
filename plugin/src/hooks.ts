import { config } from "../package.json";
import { initLocale } from "./utils/locale";
import { registerAll, unregisterAll } from "./registry";

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

async function onMainWindowUnload(_win: Window): Promise<void> {
  unregisterAll();
}

function onShutdown(): void {
  unregisterAll();
  addon.data.alive = false;
  delete Zotero[config.addonInstance];
}

export default { onStartup, onShutdown, onMainWindowLoad, onMainWindowUnload };
