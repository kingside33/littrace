import type {
  HealthVerdict,
  PollOptions,
  Poller,
  Requirements,
  ServiceClient,
  Transport,
  TransportResponse,
} from "./serviceClientCore.mjs";
import {
  createServiceClient,
  ServiceClientError,
} from "./serviceClientCore.mjs";

/**
 * The Zotero adapter of the unified service client (S4).
 *
 * All the logic lives in serviceClientCore.mjs: request construction, the
 * token cache, the tri-state health verdict and the polling state machine.
 * This file supplies exactly one thing — a `Zotero.HTTP.request` transport —
 * plus the service base URL. Nothing below makes a decision the tests cannot
 * see.
 *
 * The transport contract is `(method, url, options) => {status, text}` and it
 * resolves for every HTTP status. Zotero.HTTP.request rejects on non-2xx
 * (`UnexpectedStatusException`) and on a missing response
 * (`TimeoutException`, `BrowserOfflineException`, `CancelledException`), so
 * both paths are translated here: non-2xx becomes a resolved response, no
 * response becomes `status: 0`. Converting at the edge keeps the core's
 * classification pure.
 */

/**
 * The local service origin. The port mirrors kbapp/core/config.py's default
 * `port` (8765) — the plugin speaks to the same process the service starts.
 * A per-user override is deliberately not a pref yet: the service manager
 * (U5/U7) is where a non-default port becomes a supported configuration, and
 * adding a pref before it exists would be a setting nobody can set.
 */
export const SERVICE_BASE_URL = "http://127.0.0.1:8765";

function textOf(xhr: any): string {
  if (!xhr) {
    return "";
  }
  // The default `responseType` ("text") is kept on purpose: with
  // responseType "json" Firefox throws InvalidStateError on `responseText`,
  // and every response here goes through the core's single JSON parser
  // anyway.
  if (typeof xhr.responseText === "string") {
    return xhr.responseText;
  }
  if (typeof xhr.response === "string") {
    return xhr.response;
  }
  if (xhr.response !== undefined && xhr.response !== null) {
    try {
      return JSON.stringify(xhr.response);
    } catch {
      return "";
    }
  }
  return "";
}

function statusOf(xhr: any): number {
  const status = Number(xhr && xhr.status);
  return Number.isFinite(status) ? status : 0;
}

/**
 * `Zotero.HTTP.request` as the core's transport. Never rejects for an HTTP
 * status: a non-2xx answer is a response the core classifies, and only "no
 * response at all" becomes `status: 0`.
 */
export const zoteroTransport: Transport = async (
  method: string,
  url: string,
  options: Record<string, unknown>,
): Promise<TransportResponse> => {
  try {
    const xhr = await Zotero.HTTP.request(method, url, options);
    return { status: statusOf(xhr), text: textOf(xhr) };
  } catch (error) {
    const status = statusOf((error as any)?.xmlhttp);
    if (status !== 0) {
      // The service answered, just not with 2xx. That is a response, and the
      // core decides what it means (403 → re-auth, 5xx → present the error).
      return { status, text: textOf((error as any)?.xmlhttp) };
    }
    // No HTTP response at all: offline, timeout, cancelled, or a Zotero
    // security check. `status: 0` is the core's "unreachable".
    return { status: 0, text: "", error };
  }
};

export function createClient(
  options: { baseUrl?: string; requirements?: Requirements } = {},
): ServiceClient {
  return createServiceClient({
    baseUrl: options.baseUrl || SERVICE_BASE_URL,
    request: zoteroTransport,
    requirements: options.requirements,
  });
}

/**
 * The plugin-wide client. One instance means one token cache: every feature
 * that submits or polls shares the session token instead of each fetching
 * its own (kbapp/web/service.py issues exactly one token per service start).
 */
let shared: ServiceClient | null = null;

export function serviceClient(): ServiceClient {
  if (!shared) {
    shared = createClient();
  }
  return shared;
}

/** Drop the shared client, and with it the cached token (plugin shutdown). */
export function resetServiceClient(): void {
  shared = null;
}

export { createServiceClient, ServiceClientError };
export type {
  HealthVerdict,
  PollOptions,
  Poller,
  Requirements,
  ServiceClient,
  Transport,
  TransportResponse,
};
