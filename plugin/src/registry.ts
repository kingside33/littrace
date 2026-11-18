/**
 * The single registration entry of the plugin.
 *
 * Every feature that registers something with Zotero (item columns, item
 * sections, reader panels, menus, notifiers, deep-link bridges) must do two
 * things here and nowhere else:
 *   1. perform the registration inside `registerAll`, and
 *   2. hand a disposer to `register` so it can be reclaimed.
 *
 * `unregisterAll` disposes in reverse order and is wired to
 * onMainWindowUnload / shutdown in src/hooks.ts, so disabling or uninstalling
 * the plugin reclaims every window-level registration in one pass.
 */

export type Disposer = () => void;

interface Registration {
  id: string;
  dispose: Disposer;
}

const registrations: Array<Registration> = [];

/**
 * Record a feature's disposer. Throws on a duplicate id so two features
 * cannot silently fight over one slot.
 */
export function register(id: string, dispose: Disposer): void {
  if (registrations.some((entry) => entry.id === id)) {
    throw new Error(`littrace: duplicate registration id: ${id}`);
  }
  registrations.push({ id, dispose });
}

/**
 * Register every feature. Called once per main-window load; feature code
 * that is window-independent must guard its own re-entry and key its
 * disposers off the window it was handed.
 */
export async function registerAll(_win: Window): Promise<void> {
  // Future features hook in here; each keeps its own `register(id, dispose)`.
}

/**
 * Dispose every live registration in reverse order. Safe to call twice.
 */
export function unregisterAll(): void {
  while (registrations.length > 0) {
    const entry = registrations.pop() as Registration;
    try {
      entry.dispose();
    } catch (error) {
      Zotero.logError(error);
    }
  }
}
