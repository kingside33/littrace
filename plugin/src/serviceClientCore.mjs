/**
 * Unified client for the local littrace service (127.0.0.1:8765).
 *
 * Deliberately plain ESM with no TypeScript and no Zotero globals, so
 * tests/test_plugin_service_client.cjs can exercise the real logic with
 * `node --test` and no toolchain or npm install. src/service-client.ts is the
 * only adapter: it supplies `Zotero.HTTP.request` as the transport.
 *
 * One client, one request discipline. Everything that talks to the service
 * (session token, health probe, task submit/poll) goes through this module,
 * so the three rules cannot drift between features:
 *
 *   1. every request carries `errorDelayMax: 0`, and `timeout: 10000` — see
 *      the DecisionRecord in the S4 pull request. Zotero.HTTP.request would
 *      otherwise retry 429/5xx for up to 7500 ms and wait 30000 ms before
 *      timing out (chrome/content/zotero/xpcom/http.js, `_errorDelayMax` and
 *      the `timeout = 30000` default), and a local deterministic error is not
 *      worth a retry budget that hangs the polling promise;
 *   2. non-safe requests (anything but GET/HEAD/OPTIONS) carry the session
 *      token in `x-kb-token`, because the service rejects them otherwise
 *      (kbapp/web/service.py's local_guard);
 *   3. failures throw. Nothing is swallowed and nothing is retried here: the
 *      caller presents the error plus an explicit retry action.
 *
 * Transport contract (implemented by src/service-client.ts):
 *   request(method, url, options) -> Promise<{ status, text, error? }>
 *   - resolves for every HTTP status, including non-2xx, with the response
 *     body text;
 *   - status 0 means no HTTP response at all (offline, timeout, cancelled).
 * Keeping non-2xx out of the rejection path is what lets the classification
 * below stay pure and fully testable.
 */

/** The service's state vocabulary, mirrored from kbapp/workflows/states.py:22-23. */
export const TERMINAL_STATUSES = Object.freeze([
  "complete",
  "partial",
  "blocked",
  "failed",
  "interrupted",
]);

/** Safe methods the service allows without `x-kb-token` (kbapp/web/service.py). */
export const SAFE_METHODS = Object.freeze(["GET", "HEAD", "OPTIONS"]);

/**
 * Submit responses that are accepted without an id to poll.
 *
 * `POST /api/jobs` is the case: `kbapp/web/system_api.py:62-68` forwards to
 * `JobCoordinator.start_update`, which answers `{'state': 'started'}`
 * (kbapp/workflows/jobs.py:68) or `{'state': 'running', 'message': ...}`
 * when a task is already running (jobs.py:42/46/48/53). Neither carries an
 * id. Treating that as a failure would report "submit failed" for a submit
 * that succeeded, which is why these are outcomes, not errors.
 */
export const IDLESS_SUBMIT_STATES = Object.freeze(["started", "running"]);

/**
 * The request discipline, in one place. Every request builder copies these
 * two numbers; tests assert them on every recorded request.
 */
export const REQUEST_DISCIPLINE = Object.freeze({
  errorDelayMax: 0,
  timeout: 10000,
});

export const HEALTH_STATES = Object.freeze({
  AVAILABLE: "available",
  UNSUPPORTED: "unsupported",
  OFFLINE: "offline",
});

/** Health probe contract: the service contract this plugin build speaks. */
export const DEFAULT_REQUIREMENTS = Object.freeze({
  apiVersion: "v2",
  minServiceVersion: "0.1.0",
  maxServiceVersion: null,
});

/**
 * One error type for every failure the client can produce, so a caller can
 * branch on `kind` instead of parsing messages.
 *
 * kind:
 *   - offline  no HTTP response (service down, timeout, cancelled XHR)
 *   - http     the service answered with a non-2xx status
 *   - session  a non-safe request was rejected with 403; the cached token was
 *              dropped, the next request fetches a new one
 *   - parse    a 2xx response body was not JSON
 *   - contract a 2xx response did not satisfy the client contract (no task id)
 */
export class ServiceClientError extends Error {
  constructor(message, options = {}) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    this.name = "ServiceClientError";
    this.kind = options.kind || "http";
    this.status = options.status || 0;
    this.detail = options.detail || null;
    this.retryable = options.retryable !== false;
  }
}

/**
 * The base URL plus absolute path. Rejecting a relative path keeps a typo
 * from silently hitting a path relative to the privileged document
 * (`chrome://…`), which is exactly the class of bug the iframe route avoids.
 */
export function joinUrl(baseUrl, path) {
  if (typeof baseUrl !== "string" || baseUrl.length === 0) {
    throw new TypeError("littrace service client: baseUrl must be a non-empty string");
  }
  if (typeof path !== "string" || !path.startsWith("/")) {
    throw new TypeError(`littrace service client: path must be absolute, got ${JSON.stringify(path)}`);
  }
  return `${baseUrl.replace(/\/+$/, "")}${path}`;
}

/**
 * Build `{ url, options }` for one request.
 *
 * @param {string} method
 * @param {string} path absolute, e.g. "/api/session"
 * @param {{ baseUrl: string, token?: string | null, body?: unknown }} context
 * @returns {{ url: string, options: Record<string, unknown> }}
 */
export function buildRequest(method, path, context = {}) {
  const verb = String(method || "GET").toUpperCase();
  const { baseUrl, token = null, body } = context;
  const headers = {};
  if (!SAFE_METHODS.includes(verb)) {
    if (typeof token !== "string" || token.length === 0) {
      // Fail here rather than let the service answer 403: an unauthenticated
      // write is a programming error, not a runtime condition.
      throw new ServiceClientError(
        `littrace service client: ${verb} ${path} needs a session token`,
        { kind: "session", retryable: false },
      );
    }
    headers["x-kb-token"] = token;
  }
  const options = {
    ...REQUEST_DISCIPLINE,
    headers,
  };
  if (body !== undefined) {
    options.body = JSON.stringify(body);
    // Zotero.HTTP.request defaults a body without Content-Type to
    // application/x-www-form-urlencoded (xpcom/http.js), which FastAPI's
    // `payload: dict` route would fail to parse. Be explicit.
    headers["Content-Type"] = "application/json";
  }
  return { url: joinUrl(baseUrl, path), options };
}

/**
 * Parse a dotted numeric version ("1.2.3", optional -/+ suffix).
 * Returns null for anything else, so a malformed field can be reported
 * instead of compared.
 */
export function parseVersion(text) {
  if (typeof text !== "string") {
    return null;
  }
  const match = /^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(text.trim());
  if (!match) {
    return null;
  }
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
  };
}

/**
 * Numeric comparison of two versions (strings or parseVersion results).
 * Returns -1 / 0 / 1, or null when either side is unparsable.
 */
export function compareVersions(left, right) {
  const a = typeof left === "string" ? parseVersion(left) : left;
  const b = typeof right === "string" ? parseVersion(right) : right;
  if (!a || !b) {
    return null;
  }
  for (const part of ["major", "minor", "patch"]) {
    if (a[part] !== b[part]) {
      return a[part] < b[part] ? -1 : 1;
    }
  }
  return 0;
}

function health(state, reason, extra = {}) {
  return { state, reason, ok: state === HEALTH_STATES.AVAILABLE, ...extra };
}

/**
 * GET /api/version tri-state, as a pure function of the raw response.
 *
 * available    reachable, api_version matches and service_version is within
 *              [minServiceVersion, maxServiceVersion]
 * unsupported  reachable, but the answer does not satisfy the contract:
 *              wrong api_version, malformed body, or a version outside the
 *              range. The UI disables the entry and explains why.
 * offline      no usable answer: no HTTP response, or a non-2xx status
 *              (another app may be holding the port).
 *
 * @param {{ status?: number, text?: string }} response
 * @param {{ apiVersion?: string, minServiceVersion?: string | null, maxServiceVersion?: string | null }} requirements
 */
export function assessHealth(response, requirements = {}) {
  const apiVersion = requirements.apiVersion ?? DEFAULT_REQUIREMENTS.apiVersion;
  const minServiceVersion =
    requirements.minServiceVersion ?? DEFAULT_REQUIREMENTS.minServiceVersion;
  const maxServiceVersion =
    requirements.maxServiceVersion ?? DEFAULT_REQUIREMENTS.maxServiceVersion;
  const status = Number(response && response.status) || 0;
  if (status === 0) {
    return health(HEALTH_STATES.OFFLINE, "unreachable", { status });
  }
  if (status < 200 || status >= 300) {
    return health(HEALTH_STATES.OFFLINE, `http-${status}`, { status });
  }
  let data;
  try {
    data = JSON.parse(String((response && response.text) || ""));
  } catch {
    return health(HEALTH_STATES.UNSUPPORTED, "malformed-response", { status });
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return health(HEALTH_STATES.UNSUPPORTED, "malformed-response", { status });
  }
  if (typeof data.api_version !== "string" || data.api_version !== apiVersion) {
    return health(HEALTH_STATES.UNSUPPORTED, "api-version-mismatch", {
      status,
      apiVersion: typeof data.api_version === "string" ? data.api_version : null,
      expectedApiVersion: apiVersion,
    });
  }
  const parsed = parseVersion(data.service_version);
  if (!parsed) {
    return health(HEALTH_STATES.UNSUPPORTED, "malformed-version", {
      status,
      serviceVersion:
        typeof data.service_version === "string" ? data.service_version : null,
      apiVersion,
    });
  }
  if (minServiceVersion !== null) {
    const floor = parseVersion(minServiceVersion);
    if (!floor) {
      // Misconfiguration must be loud, not a silently disabled feature.
      throw new TypeError(
        `littrace service client: minServiceVersion is not a version: ${JSON.stringify(minServiceVersion)}`,
      );
    }
    if (compareVersions(parsed, floor) < 0) {
      return health(HEALTH_STATES.UNSUPPORTED, "version-too-old", {
        status,
        serviceVersion: data.service_version,
        apiVersion,
        minServiceVersion,
      });
    }
  }
  if (maxServiceVersion !== null) {
    const ceiling = parseVersion(maxServiceVersion);
    if (!ceiling) {
      throw new TypeError(
        `littrace service client: maxServiceVersion is not a version: ${JSON.stringify(maxServiceVersion)}`,
      );
    }
    if (compareVersions(parsed, ceiling) > 0) {
      return health(HEALTH_STATES.UNSUPPORTED, "version-too-new", {
        status,
        serviceVersion: data.service_version,
        apiVersion,
        maxServiceVersion,
      });
    }
  }
  return health(HEALTH_STATES.AVAILABLE, null, {
    status,
    serviceVersion: data.service_version,
    apiVersion,
  });
}

/**
 * Classify a POST submit response.
 *
 * Returns one of:
 *   { outcome: "queued",  taskId }                            poll it
 *   { outcome: "started", taskId: null, state, message }      accepted
 *   { outcome: "running", taskId: null, state, message }      already running
 *   { outcome: "unknown", taskId: null, state }               2xx, no outcome
 *
 * "unknown" is the only branch that is not a success: the service answered
 * 2xx without saying what happened, so the caller must not claim success,
 * but nothing may be retried blindly either — the task may exist anyway.
 */
export function classifySubmit(data) {
  const record =
    data && typeof data === "object" && !Array.isArray(data) ? data : {};
  const id = record.id ?? record.task_id;
  if (typeof id === "string" && id.length > 0) {
    return { outcome: "queued", taskId: id };
  }
  const state = typeof record.state === "string" ? record.state : null;
  const message = typeof record.message === "string" ? record.message : null;
  if (state !== null && IDLESS_SUBMIT_STATES.includes(state)) {
    return { outcome: state, taskId: null, state, message };
  }
  return { outcome: "unknown", taskId: null, state };
}

/**
 * Default wait for the poller: a cancellable setTimeout. Timer-based on
 * purpose — Firefox's privileged sandbox has no need for anything else, and
 * injecting `wait` in tests removes real time from the loop.
 */
export function timerWait(ms) {
  let cancelFn = () => {};
  const promise = new Promise((resolve) => {
    let finished = false;
    const finish = () => {
      if (finished) {
        return;
      }
      finished = true;
      resolve();
    };
    const handle = setTimeout(finish, ms);
    cancelFn = () => {
      clearTimeout(handle);
      finish();
    };
  });
  return { promise, cancel: () => cancelFn() };
}

/**
 * Poll one GET task endpoint until a terminal state.
 *
 * Deliberately not an interval timer: each tick is one sequential request, so
 * a slow service cannot stack overlapping polls. One failed request rejects
 * the whole poll (no retry), and `cancel()` stops the loop before the next
 * request and resolves `{ state: "cancelled" }` — a cancellation is a user
 * action, not an error to present.
 *
 * @param {{
 *   poll: () => Promise<any>,
 *   intervalMs?: number,
 *   wait?: (ms: number) => { promise: Promise<void>, cancel: () => void },
 *   isTerminal?: (snapshot: any) => boolean,
 *   onUpdate?: (snapshot: any, info: { polls: number }) => void,
 * }} options
 * @returns {{ done: Promise<any>, cancel: () => void, isCancelled: () => boolean }}
 */
export function startPolling(options = {}) {
  const poll = options.poll;
  if (typeof poll !== "function") {
    throw new TypeError("littrace service client: poll must be a function");
  }
  const intervalMs = options.intervalMs ?? 1000;
  if (!Number.isFinite(intervalMs) || intervalMs < 0) {
    throw new TypeError(`littrace service client: invalid intervalMs ${intervalMs}`);
  }
  const wait = options.wait || timerWait;
  const isTerminal =
    options.isTerminal ||
    ((snapshot) => Boolean(snapshot) && TERMINAL_STATUSES.includes(snapshot.status));
  const onUpdate = options.onUpdate;
  let cancelled = false;
  let settled = false;
  let polls = 0;
  let pendingWait = null;
  let settle = null;
  let fail = null;

  function finish(value) {
    if (settled) {
      return;
    }
    settled = true;
    settle(value);
  }

  const done = new Promise((resolve, reject) => {
    settle = resolve;
    fail = reject;
    (async () => {
      try {
        while (!cancelled) {
          // Each tick is one sequential request: a slow service can never
          // stack overlapping polls.
          const snapshot = await poll();
          polls += 1;
          if (cancelled) {
            break;
          }
          if (onUpdate) {
            onUpdate(snapshot, { polls });
          }
          if (isTerminal(snapshot)) {
            finish({ state: "terminal", snapshot, polls });
            return;
          }
          const gate = wait(intervalMs);
          pendingWait = gate;
          await gate.promise;
          pendingWait = null;
        }
        finish({ state: "cancelled", polls });
      } catch (error) {
        if (settled) {
          // Cancelled while a request was in flight: the caller already got
          // its cancellation, and a poll error after that is not theirs.
          return;
        }
        settled = true;
        fail(error);
      }
    })();
  });

  return {
    done,
    cancel() {
      if (cancelled) {
        return;
      }
      cancelled = true;
      // Settle now instead of waiting out an in-flight request: the caller
      // asked to stop, and no further poll or callback may happen.
      finish({ state: "cancelled", polls });
      if (pendingWait) {
        pendingWait.cancel();
      }
    },
    isCancelled() {
      return cancelled;
    },
  };
}

function readDetail(text) {
  if (typeof text !== "string" || text.trim().length === 0) {
    return "";
  }
  try {
    const data = JSON.parse(text);
    return data && typeof data.detail === "string" ? data.detail : "";
  } catch {
    return "";
  }
}

function parseJsonBody(text) {
  const trimmed = typeof text === "string" ? text.trim() : "";
  if (trimmed.length === 0) {
    return null;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    throw new ServiceClientError("服务返回了无法解析的响应", {
      kind: "parse",
      retryable: false,
    });
  }
}

/**
 * Create a client over one injected transport.
 *
 * @param {{
 *   baseUrl: string,
 *   request: (method: string, url: string, options: object) => Promise<{ status: number, text: string, error?: unknown }>,
 *   requirements?: object,
 * }} options
 */
export function createServiceClient(options = {}) {
  const { baseUrl, request, requirements = DEFAULT_REQUIREMENTS } = options;
  if (typeof baseUrl !== "string" || baseUrl.length === 0) {
    throw new TypeError("littrace service client: baseUrl must be a non-empty string");
  }
  if (typeof request !== "function") {
    throw new TypeError("littrace service client: request must be a function");
  }
  let token = null;
  let pendingToken = null;

  async function fetchToken() {
    const { url, options: requestOptions } = buildRequest("GET", "/api/session", {
      baseUrl,
    });
    const response = await request("GET", url, requestOptions);
    const status = Number(response && response.status) || 0;
    if (status === 0) {
      throw new ServiceClientError("无法连接本地服务，请确认 littrace 服务已启动", {
        kind: "offline",
        cause: response && response.error,
      });
    }
    if (status < 200 || status >= 300) {
      throw new ServiceClientError("获取会话令牌失败", {
        kind: "http",
        status,
        detail: readDetail(response && response.text),
      });
    }
    const data = parseJsonBody(response && response.text);
    const value = data && typeof data === "object" ? data.token : undefined;
    if (typeof value !== "string" || value.length === 0) {
      throw new ServiceClientError("服务未返回会话令牌", {
        kind: "contract",
        retryable: false,
      });
    }
    return value;
  }

  /** The cached token; concurrent callers share one in-flight fetch. */
  function sessionToken() {
    if (token) {
      return Promise.resolve(token);
    }
    if (!pendingToken) {
      pendingToken = fetchToken()
        .then((value) => {
          token = value;
          return value;
        })
        .finally(() => {
          pendingToken = null;
        });
    }
    return pendingToken;
  }

  async function send(method, path, body) {
    const verb = String(method || "GET").toUpperCase();
    const authenticated = !SAFE_METHODS.includes(verb);
    const tokenValue = authenticated ? await sessionToken() : null;
    const { url, options: requestOptions } = buildRequest(verb, path, {
      baseUrl,
      token: tokenValue,
      body,
    });
    const response = await request(verb, url, requestOptions);
    const status = Number(response && response.status) || 0;
    const detail = readDetail(response && response.text);
    if (status === 0) {
      throw new ServiceClientError("无法连接本地服务，请确认服务已启动", {
        kind: "offline",
        cause: response && response.error,
      });
    }
    if (status === 403 && authenticated) {
      // The cached token may have been issued by a previous service process
      // (the token is random per startup, kbapp/web/service.py). Drop it so
      // the caller's explicit retry fetches a fresh one. A 403 for Origin is
      // indistinguishable here and self-heals the same way; the server's
      // message is what the UI shows.
      token = null;
      throw new ServiceClientError(detail || "本机会话校验失败，请刷新页面", {
        kind: "session",
        status,
        detail: detail || null,
      });
    }
    if (status < 200 || status >= 300) {
      throw new ServiceClientError(detail || `服务返回 ${status}`, {
        kind: "http",
        status,
        detail: detail || null,
      });
    }
    return parseJsonBody(response && response.text);
  }

  async function probe() {
    const { url, options: requestOptions } = buildRequest("GET", "/api/version", {
      baseUrl,
    });
    const response = await request("GET", url, requestOptions);
    return assessHealth(response, requirements);
  }

  /**
   * Submit a task and classify the answer.
   *
   * Returns `{outcome, taskId, state?, message?, data}`. Callers branch on
   * `outcome`, never on the presence of `taskId`: only "queued" is pollable,
   * and "queued"/"started"/"running" are all success ("running" means a task
   * was already in flight).
   */
  async function submitTask(path, body) {
    const data = await send("POST", path, body);
    return { ...classifySubmit(data), data };
  }

  function pollTask(pollOptions = {}) {
    const { taskPath, ...rest } = pollOptions;
    if (typeof taskPath !== "string" || taskPath.length === 0) {
      throw new TypeError("littrace service client: pollTask needs a taskPath");
    }
    return startPolling({ ...rest, poll: () => send("GET", taskPath) });
  }

  return {
    baseUrl,
    sessionToken,
    invalidateToken() {
      token = null;
    },
    send,
    get: (path) => send("GET", path),
    post: (path, body) => send("POST", path, body),
    probe,
    submitTask,
    pollTask,
  };
}
