import { t } from "../utils/locale";
import { serviceClient } from "../service-client";
import { collectDiagnostics } from "../status/diagnostics";
import { probeHealth } from "../status/diagnostics";
import { serviceBaseUrlFromPrefs } from "../prefs/serviceOrigin";
import { dotView, offlineHealth } from "../status/statusCore.mjs";
import { settingsViewFrom } from "./settingsCore.mjs";
import { formatBytes } from "../prefs/prefsCore.mjs";
import type { SettingsView } from "./settingsCore.mjs";
import { renderCloud } from "./settingsPane";
import { renderConnection } from "./connectionPane";

/**
 * One refresh for the whole settings page.
 *
 * Two features landed in the same pane from different directions — the cloud
 * group (P2) and the connection group (P3) — and each brought its own read.
 * Opening the pane therefore issued six requests, of which a pair of
 * `/api/version` calls and a pair of `/api/settings` calls were byte-identical
 * and sent within milliseconds of each other:
 *
 *   prefsPane.refresh         → GET /api/version
 *   loadCloud                 → GET /api/settings
 *   loadConnection            → GET /api/settings   (duplicate)
 *   loadConnection            → collectDiagnostics  → GET /api/version (duplicate)
 *                                                   → GET /api/status
 *                                                   → GET /api/models
 *
 * Beyond the waste, two writers of the same rows could disagree for the moment
 * between their answers. This module owns the one read so the groups cannot
 * drift: one probe, one settings GET, and the diagnostics' two extra reads are
 * gated on the probe having said the service is compatible (which is what
 * status/diagnostics.ts already does).
 *
 * What it deliberately does NOT do: probe the cloud endpoint. `POST
 * /api/settings/test` reaches a metered third party and stays behind the
 * 「测试连接」 button (SECURITY.md: no request without a user action).
 */

const IDS = {
  health: "littrace-prefs-health",
  platform: "littrace-prefs-platform",
  group: "littrace-prefs-cloud",
  cacheCurrent: "littrace-prefs-cache-current",
  cacheDefault: "littrace-prefs-cache-default",
} as const;

/** Dot state → the settings page's own health label key. */
const HEALTH_LABEL_KEYS: Record<string, string> = {
  checking: "prefs-health-checking",
  online: "prefs-health-online",
  outdated: "prefs-health-outdated",
  offline: "prefs-health-offline",
};

function report(error: unknown, what: string): void {
  Zotero.logError(
    error instanceof Error
      ? new Error(`littrace 设置页(${what}): ${error.message}`)
      : new Error(`littrace 设置页(${what}): ${String(error)}`),
  );
}

/** Zotero's platform flag → the support-matrix platform id. */
function platformName(): string {
  if (Zotero.isWin) {
    return "win";
  }
  if (Zotero.isMac) {
    return "mac";
  }
  return "linux";
}

export type PaneSnapshot = {
  health: Awaited<ReturnType<typeof probeHealth>> | null;
  settings: unknown;
  settingsError: unknown;
  rows: unknown;
  baseUrl: string;
};

/**
 * The page's shared reads.
 *
 * Each stage is independently tolerated: a failing settings GET must not blank
 * the version line, and a failing diagnostics collection must not blank the
 * form. The caller gets whatever succeeded plus the errors, and each group
 * renders what it has.
 */
export async function collectPaneSnapshot(): Promise<PaneSnapshot> {
  const baseUrl = serviceBaseUrlFromPrefs();
  let health = null;
  try {
    health = await probeHealth();
  } catch (error) {
    report(error, "健康检查");
  }
  let settings: unknown = null;
  let settingsError: unknown = null;
  try {
    settings = await serviceClient().get("/api/settings");
  } catch (error) {
    settingsError = error;
  }
  let rows: unknown = null;
  // Exactly one `/api/version` per page refresh: the diagnostics are handed the
  // verdict this function already has, including the offline fallback, so a
  // throwing probe cannot trigger a second one inside collectDiagnostics.
  // Only the two extra GETs are gated on compatibility — the rows themselves
  // are always built, because 「版本过旧」 is exactly when that line matters.
  try {
    const snapshot = await collectDiagnostics({ health: health ?? offlineHealth() });
    rows = snapshot.rows;
  } catch (error) {
    report(error, "服务诊断");
  }
  return { health, settings, settingsError, rows, baseUrl };
}

/** Paint everything one snapshot can paint, and nothing it cannot. */
export function renderPane(doc: Document, snapshot: PaneSnapshot): void {
  const platformCard = doc.getElementById(IDS.platform);
  if (platformCard) {
    platformCard.hidden = platformName() === "win";
  }
  const healthRow = doc.getElementById(IDS.health);
  if (healthRow && snapshot.health) {
    const state = dotView(snapshot.health).state;
    healthRow.textContent = t(HEALTH_LABEL_KEYS[state] ?? HEALTH_LABEL_KEYS.offline);
  }
  if (snapshot.settings !== null) {
    const view: SettingsView = settingsViewFrom(snapshot.settings);
    renderCloud(doc, view, snapshot.settingsError);
  } else {
    renderCloud(doc, null, snapshot.settingsError);
  }
  renderConnection(doc, snapshot.settings, snapshot.rows);
  renderCache(doc, snapshot.settings);
}

/** The service's cache projection, as much of it as the pane shows. */
type CacheInfo = {
  path: string;
  default_path: string;
  files: number;
  bytes: number;
};

/**
 * The cache row, painted from the same snapshot as everything else.
 *
 * The cache group's own save button stays in prefsPane (it owns a POST and its
 * own result line), but its read must not be a second `GET /api/settings`:
 * that is exactly the duplication this module exists to remove, and a cache
 * path from a different moment than the form beside it is a worse answer than
 * one row arriving a little later.
 */
function renderCache(doc: Document, settings: unknown): void {
  const row = doc.getElementById(IDS.cacheCurrent);
  if (!row) {
    return;
  }
  const cache =
    settings && typeof settings === "object" && !Array.isArray(settings)
      ? ((settings as { cache?: unknown }).cache as CacheInfo | undefined)
      : undefined;
  if (!cache || typeof cache.path !== "string") {
    row.textContent = t("prefs-cache-unavailable");
    return;
  }
  row.textContent = `${cache.path}(${t("prefs-cache-count", {
    args: { files: String(cache.files), size: formatBytes(cache.bytes) },
  })})`;
  const hint = doc.getElementById(IDS.cacheDefault);
  if (hint) {
    hint.textContent = t("prefs-cache-default", { args: { path: cache.default_path } });
  }
}

/** Per-window generation; a newer refresh invalidates older answers. */
const generations = new WeakMap<Document, number>();

function nextGeneration(doc: Document): number {
  const value = (generations.get(doc) ?? 0) + 1;
  generations.set(doc, value);
  return value;
}

function stillCurrent(doc: Document, generation: number): boolean {
  return generations.get(doc) === generation && doc.documentElement.isConnected;
}

/**
 * Read once and paint every row.
 *
 * Never throws into the preferences window: a service that is offline must
 * still produce a readable page, which is the whole point of the health row.
 */
export async function refreshPane(doc: Document): Promise<void> {
  const generation = nextGeneration(doc);
  const healthRow = doc.getElementById(IDS.health);
  if (healthRow) {
    healthRow.textContent = t(HEALTH_LABEL_KEYS.checking);
  }
  let snapshot: PaneSnapshot;
  try {
    snapshot = await collectPaneSnapshot();
  } catch (error) {
    report(error, "刷新设置页");
    return;
  }
  if (!stillCurrent(doc, generation)) {
    return;
  }
  renderPane(doc, snapshot);
}

/**
 * Run one refresh after a write, then repaint.
 *
 * A save changes settings but not the service version, yet repainting the whole
 * page from one read is cheaper than tracking which rows a given write touched
 * — and it is the only way the two groups cannot show values from different
 * moments.
 */
export async function refreshAfterWrite(doc: Document): Promise<void> {
  await refreshPane(doc);
}

/**
 * Wire the page's one re-entry listener and run the first read.
 *
 * `preferences.js` guards pane loading with a `loaded` flag and signals
 * re-entry with a `showing` event, so `onPaneLoad` may run only once per
 * preferences window. The cheap defence is worth it — one local read — and it
 * matters more now that both groups render from the same snapshot: a stale one
 * would show a form and a health row from different moments.
 *
 * Edits in progress are safe: the generation counter makes a later user action
 * win, and a refresh only ever overwrites values the service already holds.
 */
export function mountPane(doc: Document): void {
  const root = doc.getElementById(IDS.group);
  const paneRoot = root ? root.parentElement : null;
  if (paneRoot && typeof paneRoot.addEventListener === "function") {
    paneRoot.addEventListener("showing", () => {
      void refreshPane(doc);
    });
  }
  void refreshPane(doc);
}
