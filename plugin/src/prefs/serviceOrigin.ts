import {
  DEFAULT_SERVICE_PORT,
  normalizePort,
  prefEntry,
  prefKey,
  serviceBaseUrl,
} from "./prefsCore.mjs";

/**
 * The service origin derived from prefs.
 *
 * A separate module, and deliberately a leaf: src/prefs/prefs.ts (the pane),
 * src/service-client.ts (the S4 adapter) and src/tabs/tabs.ts (the iframe
 * base URL) all need the origin, and routing it through any one of them would
 * make that module part of a cycle. It imports prefsCore only — plain data and
 * pure functions — and reads Zotero.Prefs at call time.
 *
 * The host is fixed at 127.0.0.1. It is not a preference: the local service
 * binds loopback (kbapp/core/config.py), so a configurable host would either
 * be ignored or turn the plugin into a client for an arbitrary server. Only
 * the port is a user decision.
 */

/** The configured port, validated, falling back to the service default. */
export function servicePort(): number {
  const entry = prefEntry("connection.port");
  if (!entry) {
    return DEFAULT_SERVICE_PORT;
  }
  let raw: unknown;
  try {
    raw = Zotero.Prefs.get(prefKey(entry.key), true);
  } catch {
    // A pref read before the pref system is up must not break the client.
    return DEFAULT_SERVICE_PORT;
  }
  return normalizePort(raw ?? entry.default);
}

/** The service origin: `http://127.0.0.1:<port>` from prefsCore's single rule. */
export function serviceBaseUrlFromPrefs(): string {
  return serviceBaseUrl(servicePort());
}
