/**
 * Window-scoped registration bookkeeping.
 *
 * Deliberately plain ESM with no TypeScript, DOM or Zotero globals, so
 * tests/test_plugin_registry.cjs can exercise the real logic with
 * `node --test` and no toolchain or npm install. src/registry.ts wraps this
 * with the typed plugin-facing API.
 *
 * Scoping rules:
 *   - window registrations live in one map per window, so the same id may be
 *     registered once per window (registerAll runs once per open window);
 *   - a duplicate id is an error only inside the same scope;
 *   - disposers run in reverse registration order, and one throwing disposer
 *     must not stop the rest.
 */

/**
 * @param {(error: unknown, id: string) => void} [onError]
 */
export function createRegistry(onError) {
  const report = typeof onError === "function" ? onError : () => {};
  /** @type {Map<object, Map<string, () => void>>} */
  const windows = new Map();
  /** @type {Map<string, () => void>} */
  const globals = new Map();

  function add(store, kind, id, dispose, scope) {
    if (typeof id !== "string" || id.length === 0) {
      throw new Error(`littrace: ${kind} registration id must be a non-empty string`);
    }
    if (typeof dispose !== "function") {
      throw new Error(
        `littrace: ${kind} registration "${id}" must provide a disposer`,
      );
    }
    if (store.has(id)) {
      throw new Error(
        `littrace: duplicate ${kind} registration id "${id}"${scope}`,
      );
    }
    store.set(id, dispose);
  }

  function disposeStore(store) {
    // Reverse insertion order: later registrations may depend on earlier ones.
    const entries = Array.from(store.entries()).reverse();
    store.clear();
    for (const [id, dispose] of entries) {
      try {
        dispose();
      } catch (error) {
        report(error, id);
      }
    }
  }

  function unregisterWindow(win) {
    const store = windows.get(win);
    if (!store) {
      return;
    }
    windows.delete(win);
    disposeStore(store);
  }

  return {
    /**
     * Register `id` for the given main window. Registering the same id for a
     * different window is expected, not a conflict.
     */
    register(win, id, dispose) {
      let store = windows.get(win);
      if (!store) {
        store = new Map();
        windows.set(win, store);
      }
      add(store, "window", id, dispose, " for this window");
    },

    /** Register something window-independent (notifier, deep-link bridge). */
    registerGlobal(id, dispose) {
      add(globals, "global", id, dispose, "");
    },

    /**
     * Mark a window as being registered. Returns false when the window was
     * already claimed, which makes registerAll idempotent per window even if
     * the same window is loaded through two startup paths.
     */
    claimWindow(win) {
      if (windows.has(win)) {
        return false;
      }
      windows.set(win, new Map());
      return true;
    },

    /** Dispose only this window's registrations. */
    unregisterWindow,

    /** Dispose the window-independent registrations. */
    unregisterGlobals() {
      disposeStore(globals);
    },

    /** Dispose every window's registrations, then the globals (shutdown). */
    unregisterEverything() {
      for (const win of Array.from(windows.keys())) {
        unregisterWindow(win);
      }
      disposeStore(globals);
    },
  };
}
