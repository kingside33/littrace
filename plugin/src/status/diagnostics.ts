import { serviceClient } from "../service-client";
import { serviceBaseUrlFromPrefs } from "../prefs/serviceOrigin";
import { diagnosticRows, offlineHealth, STATUS_ENDPOINTS } from "./statusCore.mjs";
import type { DiagnosticRow } from "./statusCore.mjs";
import type { HealthVerdict } from "../serviceClientCore.mjs";

/**
 * One diagnostic collection for the status dot and the settings page (U5).
 *
 * The version probe is the same call the dot polls (`ServiceClient.probe()`
 * → `GET /api/version`, S4's request discipline). The two extra reads —
 * `/api/status?detail=false` for the last sync and `/api/models` for Ollama —
 * happen only when the user opened a diagnostic view AND the service answered
 * as compatible. That keeps the toolbar dot at one cheap request per poll and
 * keeps the plugin from asking an incompatible service for data it may not
 * serve.
 *
 * Every extra request is individually tolerated: a failing `/api/models` must
 * not blank the whole popup, because the popup exists precisely to report
 * which checks failed. A failure becomes `null`, and the pure row builder
 * renders "unknown" for that row.
 */

export type DiagnosticSnapshot = {
  health: HealthVerdict;
  baseUrl: string;
  models: unknown;
  status: unknown;
  rows: DiagnosticRow[];
};

/** One probe, no retry: the S4 client owns that decision (ADR 0018). */
export function probeHealth(): Promise<HealthVerdict> {
  return serviceClient().probe();
}

async function optionalGet(path: string): Promise<unknown> {
  try {
    return await serviceClient().get(path);
  } catch {
    return null;
  }
}

/**
 * Collect everything the diagnostic popup shows.
 *
 * `health` is never null: a throwing probe is normalised to the offline
 * verdict, so the caller cannot accidentally render "no row" for a service
 * that could not be reached.
 */
export async function collectDiagnostics(): Promise<DiagnosticSnapshot> {
  const baseUrl = serviceBaseUrlFromPrefs();
  let health: HealthVerdict;
  try {
    health = await probeHealth();
  } catch {
    health = offlineHealth();
  }
  const reachable = health.state === "available";
  const [status, models] = reachable
    ? await Promise.all([
        optionalGet(STATUS_ENDPOINTS.status),
        optionalGet(STATUS_ENDPOINTS.models),
      ])
    : [null, null];
  const rows = diagnosticRows({ health, baseUrl, models, status });
  return { health, baseUrl, models, status, rows };
}
