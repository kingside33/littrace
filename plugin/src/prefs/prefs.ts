import { config } from "../../package.json";
import { t } from "../utils/locale";
import {
  PANE_LABEL_KEY,
  formatBytes,
  normalizeCacheDir,
  prefEntry,
  prefKey,
} from "./prefsCore.mjs";
import { serviceBaseUrlFromPrefs } from "./serviceOrigin";
import { serviceClient } from "../service-client";
import { probeHealth } from "../status/diagnostics";
import { dotView } from "../status/statusCore.mjs";

/**
 * The settings page (U5): preference access, the pane registration and the
 * two dynamic rows the declarative pane cannot compute.
 *
 * Division of labour, unchanged from every other feature:
 *   - prefsCore.mjs owns *what* a preference is (key, type, default, group,
 *     Fluent label) and is covered by tests/test_plugin_prefs.cjs;
 *   - addon/content/preferences.xhtml owns the layout and the declarative
 *     `preference="<full key>"` bindings Zotero performs itself
 *     (preferences.js `_initImportedNodesPostInsert`);
 *   - this file owns the two rows that need a live answer — platform card and
 *     service health — plus reading prefs for the rest of the plugin.
 *
 * Nothing here writes to Zotero; "write back" in this repository always means
 * the Zotero library, which stays read-only.
 */

function report(error: unknown, what: string): void {
  Zotero.logError(
    error instanceof Error
      ? new Error(`littrace 设置页(${what}): ${error.message}`)
      : new Error(`littrace 设置页(${what}): ${String(error)}`),
  );
}

/** The service's cache projection (`GET /api/settings` → `cache`). */
interface CacheInfo {
  /** Effective cache directory the service uses right now. */
  path: string;
  /** `<data>/cache`, the value an empty setting falls back to. */
  default_path: string;
  /** Whether a custom directory is in force. */
  overridden: boolean;
  exists: boolean;
  files: number;
  bytes: number;
}

/** The settings map the service returns; only the fields used here are typed. */
interface SettingsPayload {
  cache_dir?: string;
  cache?: CacheInfo;
}

async function fetchSettings(): Promise<SettingsPayload> {
  return (await serviceClient().get("/api/settings")) as SettingsPayload;
}

async function postSettings(values: {
  cache_dir: string;
}): Promise<SettingsPayload> {
  return (await serviceClient().post(
    "/api/settings",
    values,
  )) as SettingsPayload;
}

/** One pref by bare table key, falling back to the table default. */
export function readPref<T = unknown>(key: string, fallback?: T): T {
  const entry = prefEntry(key);
  if (!entry) {
    throw new Error(`littrace prefs: unknown pref key ${JSON.stringify(key)}`);
  }
  try {
    const value = Zotero.Prefs.get(prefKey(entry.key), true);
    return (value ?? (fallback ?? entry.default)) as T;
  } catch {
    return (fallback ?? entry.default) as T;
  }
}

/** A boolean pref, defaulted from the table. */
export function prefBool(key: string): boolean {
  return Boolean(readPref(key, false));
}

/** The service origin the settings page and the status dot both display. */
export function serviceOrigin(): string {
  return serviceBaseUrlFromPrefs();
}

/**
 * The pane id passed to `PreferencePanes.register`. `openPreferences(paneID)`
 * navigates by this exact id (xpcom/preferencePanes.js keeps the given id), so
 * the offline guide card's 「打开设置页」 button needs the same constant.
 */
export const PANE_ID = "littrace-preferences";

/**
 * Register the pane through Zotero.PreferencePanes and return the disposer
 * for src/registry.ts.
 *
 * `register()` resolves with the pane id; the disposer may run before that
 * promise settles (plugin disabled while the pane is still registering), so
 * the id is unregistered in both orders. Zotero also drops plugin panes on
 * its own shutdown observer, which makes this a second belt rather than the
 * only one.
 */
export function registerPreferencePane(): () => void {
  let paneID: string | null = null;
  let disposed = false;

  const panes = Zotero.PreferencePanes as {
    register?: (options: Record<string, unknown>) => Promise<string>;
    unregister?: (id: string) => void;
  };
  if (!panes || typeof panes.register !== "function") {
    report(new Error("Zotero.PreferencePanes.register 不可用"), "注册");
    return () => {};
  }

  const unregister = (id: string): void => {
    try {
      panes.unregister?.(id);
    } catch (error) {
      report(error, "注销");
    }
  };

  const pending = panes.register({
    pluginID: config.addonID,
    id: PANE_ID,
    // Relative to the plugin root; Zotero resolves it with
    // Zotero.Plugins.resolveURI (preferencePanes.js register()).
    src: "content/preferences.xhtml",
    label: t(PANE_LABEL_KEY),
    image: "content/icons/lineage-20.svg",
  });
  Promise.resolve(pending)
    .then((id) => {
      if (typeof id === "string" && id.length > 0) {
        paneID = id;
        if (disposed) {
          unregister(id);
        }
      }
    })
    .catch((error: unknown) => {
      report(error, "注册");
    });

  return () => {
    disposed = true;
    if (paneID) {
      const id = paneID;
      paneID = null;
      unregister(id);
    }
  };
}

/** Zotero's platform flag → the support-matrix platform id. */
export function platformName(): string {
  if (Zotero.isWin) {
    return "win";
  }
  if (Zotero.isMac) {
    return "mac";
  }
  return "linux";
}

/** Dot state → the settings page's own health label key. */
const HEALTH_LABEL_KEYS: Record<string, string> = {
  checking: "prefs-health-checking",
  online: "prefs-health-online",
  outdated: "prefs-health-outdated",
  offline: "prefs-health-offline",
};

/**
 * The pane's two live rows, called from the fragment's inline handlers
 * (Zotero's own preferences panes use the same `onload` pattern —
 * preferences_general.xhtml). Both are no-ops when the element is absent, so
 * a Zotero build that renames ids degrades to a missing row, never a throw
 * inside the preferences window.
 */
export const prefsPane = {
  onPaneLoad(doc: Document): void {
    // The platform card is markup that is present but hidden; this is the one
    // line that decides whether it shows. docs/support-matrix.json is the
    // authority for the Windows-only M1 scope.
    const card = doc.getElementById("littrace-prefs-platform");
    if (card) {
      card.hidden = platformName() === "win";
    }
    // The refresh control gets its listener here rather than as an inline
    // handler: html:button dispatches click on HTML, and this keeps the only
    // executable wiring in TypeScript where it is reviewable.
    const refresh = doc.getElementById("littrace-prefs-refresh");
    if (refresh) {
      refresh.addEventListener("click", () => prefsPane.refresh(doc));
    }
    // The cache row: read the current server-side cache on load, and forward a
    // typed directory when the user applies it (issue #61).
    const saveCache = doc.getElementById("littrace-prefs-cache-save");
    if (saveCache) {
      saveCache.addEventListener("click", () => prefsPane.saveCacheDir(doc));
    }
    void prefsPane.refresh(doc);
    void prefsPane.refreshCache(doc);
  },

  /**
   * Show where the service's cache actually is, and how big it is.
   *
   * The pane never guesses: this is a GET of the same settings endpoint the
   * service page uses, so an offline service shows "unavailable" instead of a
   * stale path.
   */
  refreshCache(doc: Document): void {
    const row = doc.getElementById("littrace-prefs-cache-current");
    if (!row) {
      return;
    }
    row.setAttribute("data-l10n-id", "littrace-prefs-cache-checking");
    void (async () => {
      try {
        const settings = await fetchSettings();
        const cache = settings.cache as CacheInfo | undefined;
        if (!row.isConnected) {
          return;
        }
        if (!cache) {
          row.textContent = t("prefs-cache-unavailable");
          return;
        }
        row.textContent = `${cache.path}(${t("prefs-cache-count", {
          args: { files: String(cache.files), size: formatBytes(cache.bytes) },
        })})`;
        const hint = doc.getElementById("littrace-prefs-cache-default");
        if (hint) {
          hint.textContent = t("prefs-cache-default", {
            args: { path: cache.default_path },
          });
        }
      } catch (error) {
        if (row.isConnected) {
          row.textContent = t("prefs-cache-unavailable");
        }
        report(error, "读取缓存信息");
      }
    })();
  },

  /**
   * Validate what the plugin can validate, then let the service decide.
   *
   * Emptiness and absoluteness are client-side facts; existence and
   * writability are properties of the machine running the service, so the
   * answer the user sees is the service's, not a guess here. A refused path
   * leaves the stored pref as typed so the user can fix it.
   */
  saveCacheDir(doc: Document): void {
    const input = doc.getElementById(
      "littrace-prefs-cache-dir",
    ) as HTMLInputElement | null;
    const result = doc.getElementById("littrace-prefs-cache-result");
    if (!input) {
      return;
    }
    const normalized = normalizeCacheDir(input.value);
    if (!normalized.ok) {
      if (result) {
        result.hidden = false;
        result.textContent = t("prefs-cache-error", {
          args: { reason: t(String(normalized.reason)) },
        });
      }
      return;
    }
    input.value = normalized.value;
    void (async () => {
      try {
        const saved = await postSettings({ cache_dir: normalized.value });
        const cache = saved.cache as CacheInfo | undefined;
        if (result && cache) {
          result.hidden = false;
          result.textContent = t("prefs-cache-saved", {
            args: {
              path: cache.path,
              files: String(cache.files),
              size: formatBytes(cache.bytes),
            },
          });
        }
        prefsPane.refreshCache(doc);
      } catch (error) {
        if (result) {
          result.hidden = false;
          result.textContent = t("prefs-cache-error", {
            args: { reason: String((error as Error)?.message ?? error) },
          });
        }
        report(error, "保存缓存目录");
      }
    })();
  },

  refresh(doc: Document): void {
    const row = doc.getElementById("littrace-prefs-health");
    if (!row) {
      return;
    }
    row.textContent = t(HEALTH_LABEL_KEYS.checking);
    void (async () => {
      let state = "offline";
      try {
        const health = await probeHealth();
        state = dotView(health).state;
      } catch (error) {
        report(error, "健康检查");
      }
      if (!row.isConnected) {
        return;
      }
      row.textContent = t(HEALTH_LABEL_KEYS[state] ?? HEALTH_LABEL_KEYS.offline);
    })();
  },
};
