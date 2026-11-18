"use strict";

// Anchors the unified local-service client (S4: 统一服务客户端与轮询纪律).
//
// The logic under test lives in plugin/src/serviceClientCore.mjs, which is
// plain ESM with no Zotero globals, so it runs here with node built-ins only
// and needs no npm install. src/service-client.ts supplies the
// Zotero.HTTP.request transport and is exercised on a real machine only.
//
// Everything asserted below is a contract, not an implementation detail:
//   - every request the client makes carries errorDelayMax: 0 and
//     timeout: 10000, and non-safe requests carry x-kb-token
//     (kbapp/web/service.py's local_guard rejects them otherwise);
//   - the session token is fetched once and cached, and a 403 drops it;
//   - GET /api/version maps to exactly one of available / unsupported /
//     offline, with version comparison;
//   - the poller stops on a terminal state, can be cancelled without another
//     request or callback, and never retries a failed poll.

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const CORE_URL = pathToFileURL(
  path.join(__dirname, "..", "plugin", "src", "serviceClientCore.mjs"),
).href;

async function loadCore() {
  return import(CORE_URL);
}

const BASE = "http://127.0.0.1:8765";

/**
 * A transport recorder. `routes` maps "METHOD /path" to a handler returning
 * `{status, text}`; an unlisted route is a test bug, so it throws rather
 * than guesses.
 */
function recorder(routes = {}) {
  const calls = [];
  async function request(method, url, options) {
    calls.push({ method, url, options });
    const route = routes[`${method} ${url}`];
    if (!route) {
      throw new Error(`unexpected request ${method} ${url}`);
    }
    return typeof route === "function" ? route(options) : route;
  }
  return { calls, request };
}

function json(body, status = 200) {
  return { status, text: JSON.stringify(body) };
}

test("every request carries errorDelayMax: 0 and timeout: 10000", async () => {
  const core = await loadCore();
  const { calls, request } = recorder({
    [`GET ${BASE}/api/session`]: json({ token: "t0k" }),
    // The real POST /api/jobs shape: kbapp/workflows/jobs.py:68 returns
    // {'state': 'started'} with no id.
    [`POST ${BASE}/api/jobs`]: json({ state: "started" }),
    [`GET ${BASE}/api/documents/jobs/job-1`]: json({ id: "job-1", status: "running" }),
    [`GET ${BASE}/api/version`]: json({ service_version: "0.1.0", api_version: "v2" }),
  });
  const client = core.createServiceClient({ baseUrl: BASE, request });

  await client.probe();
  await client.submitTask("/api/jobs", { action: "vectors" });
  await client.get("/api/documents/jobs/job-1");

  assert.equal(calls.length, 4, "session + probe + submit + poll");
  for (const call of calls) {
    assert.equal(
      call.options.errorDelayMax,
      0,
      `${call.method} ${call.url} must disable Zotero's 429/5xx retry budget`,
    );
    assert.equal(
      call.options.timeout,
      10000,
      `${call.method} ${call.url} must use the short local timeout`,
    );
  }
});

test("the discipline constants are single-sourced", async () => {
  const core = await loadCore();
  assert.deepEqual({ ...core.REQUEST_DISCIPLINE }, { errorDelayMax: 0, timeout: 10000 });
  // A request built directly, without the client, carries the same values.
  const { options } = core.buildRequest("GET", "/api/version", { baseUrl: BASE });
  assert.equal(options.errorDelayMax, 0);
  assert.equal(options.timeout, 10000);
});

test("non-safe methods carry x-kb-token, safe methods do not", async () => {
  const core = await loadCore();

  const post = core.buildRequest("POST", "/api/jobs", { baseUrl: BASE, token: "t0k", body: { a: 1 } });
  assert.equal(post.options.headers["x-kb-token"], "t0k");
  assert.equal(post.options.headers["Content-Type"], "application/json");
  assert.equal(post.options.body, JSON.stringify({ a: 1 }));
  assert.equal(post.url, `${BASE}/api/jobs`);

  for (const method of ["GET", "HEAD", "OPTIONS"]) {
    const safe = core.buildRequest(method, "/api/session", { baseUrl: BASE, token: "t0k" });
    assert.equal(
      "x-kb-token" in safe.options.headers,
      false,
      `${method} must not send a token`,
    );
  }

  // An unauthenticated write is a programming error: fail before the wire,
  // rather than let the service answer 403 for a request that never could
  // have succeeded.
  assert.throws(
    () => core.buildRequest("POST", "/api/jobs", { baseUrl: BASE }),
    (error) => error instanceof core.ServiceClientError && error.kind === "session",
  );
});

test("relative paths are rejected instead of resolving against chrome://", async () => {
  const core = await loadCore();
  assert.equal(core.joinUrl(BASE, "/api/session"), `${BASE}/api/session`);
  assert.equal(core.joinUrl(`${BASE}/`, "/api/session"), `${BASE}/api/session`);
  assert.throws(() => core.joinUrl(BASE, "api/session"), /absolute/);
});

test("the session token is fetched once and cached", async () => {
  const core = await loadCore();
  let sessionCalls = 0;
  const { request } = recorder({
    [`GET ${BASE}/api/session`]: () => {
      sessionCalls += 1;
      return json({ token: "t0k" });
    },
    // Real POST /api/jobs shape: no id (kbapp/workflows/jobs.py:68).
    [`POST ${BASE}/api/jobs`]: json({ state: "started" }),
  });
  const client = core.createServiceClient({ baseUrl: BASE, request });

  await client.post("/api/jobs", { action: "vectors" });
  await client.post("/api/jobs", { action: "vectors" });
  await client.post("/api/jobs", { action: "vectors" });

  assert.equal(sessionCalls, 1, "three writes, one token fetch");
  assert.equal(await client.sessionToken(), "t0k", "the cached token is returned");
  assert.equal(sessionCalls, 1, "reading the cache does not refetch");
});

test("concurrent first writes share one token fetch", async () => {
  const core = await loadCore();
  let sessionCalls = 0;
  const { request } = recorder({
    [`GET ${BASE}/api/session`]: () => {
      sessionCalls += 1;
      return json({ token: "t0k" });
    },
    // Real POST /api/jobs shape: no id (kbapp/workflows/jobs.py:68).
    [`POST ${BASE}/api/jobs`]: json({ state: "started" }),
  });
  const client = core.createServiceClient({ baseUrl: BASE, request });

  await Promise.all([
    client.post("/api/jobs", { action: "a" }),
    client.post("/api/jobs", { action: "b" }),
    client.post("/api/jobs", { action: "c" }),
  ]);

  assert.equal(sessionCalls, 1, "no thundering herd against /api/session");
});

test("a 403 drops the cached token and the next request refetches it", async () => {
  const core = await loadCore();
  const tokens = ["first", "second"];
  let sessionCalls = 0;
  const seen = [];
  const { request } = recorder({
    [`GET ${BASE}/api/session`]: () => {
      sessionCalls += 1;
      return json({ token: tokens[sessionCalls - 1] });
    },
    [`POST ${BASE}/api/jobs`]: (options) => {
      seen.push(options.headers["x-kb-token"]);
      // The second token is the one the current service process issued; the
      // first is stale, exactly like a token left over from a previous run.
      return options.headers["x-kb-token"] === "second"
        ? json({ state: "started" })
        : { status: 403, text: JSON.stringify({ detail: "本机会话校验失败，请刷新页面" }) };
    },
  });
  const client = core.createServiceClient({ baseUrl: BASE, request });

  await assert.rejects(client.post("/api/jobs", {}), (error) => {
    assert.equal(error.kind, "session");
    assert.equal(error.status, 403);
    assert.match(error.message, /本机会话校验失败/);
    return true;
  });
  // The cached token was invalidated by the 403, so this attempt refetches.
  const result = await client.post("/api/jobs", {});
  assert.deepEqual(result, { state: "started" }, "the raw body is passed through");
  assert.deepEqual(seen, ["first", "second"], "the 403 forced a re-auth");
  assert.equal(sessionCalls, 2);
});

test("failures are thrown, never retried and never swallowed", async () => {
  const core = await loadCore();
  const calls = [];
  const client = core.createServiceClient({
    baseUrl: BASE,
    request: async (method, url, options) => {
      calls.push({ method, url, options });
      if (url.endsWith("/api/version")) {
        return { status: 0, text: "", error: new Error("connection refused") };
      }
      return { status: 500, text: JSON.stringify({ detail: "内部错误" }) };
    },
  });

  // The probe reports, it does not throw: its whole job is to turn "no
  // answer" into the offline state the UI disables an entry on.
  const verdict = await client.probe();
  assert.equal(verdict.state, "offline");
  assert.equal(calls.length, 1, "one probe, one request: no retry loop here");

  // A plain request has no verdict to express, so it throws.
  await assert.rejects(client.get("/api/status"), (error) => {
    assert.equal(error.kind, "http");
    assert.equal(error.status, 500);
    assert.match(error.message, /内部错误/, "the service's own message is presented");
    return true;
  });
  assert.equal(calls.length, 2, "the failed request was presented, not retried");
});

test("a malformed 2xx body is a parse error, not a silent null", async () => {
  const core = await loadCore();
  const client = core.createServiceClient({
    baseUrl: BASE,
    request: async () => ({ status: 200, text: "<html>not json</html>" }),
  });
  await assert.rejects(client.sessionToken(), (error) => {
    assert.equal(error.kind, "parse");
    return true;
  });
});

test("the health verdict is tri-state and pure", async () => {
  const core = await loadCore();
  const { assessHealth, HEALTH_STATES } = core;

  const cases = [
    // [describe, response, requirements, expected state, expected reason]
    ["service down", { status: 0, text: "" }, {}, "offline", "unreachable"],
    ["non-2xx answer", { status: 502, text: "" }, {}, "offline", "http-502"],
    ["not JSON", { status: 200, text: "OK" }, {}, "unsupported", "malformed-response"],
    ["JSON array", { status: 200, text: "[]" }, {}, "unsupported", "malformed-response"],
    ["wrong api version", json({ service_version: "0.1.0", api_version: "v1" }), {}, "unsupported", "api-version-mismatch"],
    ["missing service version", json({ api_version: "v2" }), {}, "unsupported", "malformed-version"],
    ["unparsable service version", json({ service_version: "latest", api_version: "v2" }), {}, "unsupported", "malformed-version"],
    ["exactly the floor", json({ service_version: "0.1.0", api_version: "v2" }), {}, "available", null],
    ["below the floor", json({ service_version: "0.0.9", api_version: "v2" }), {}, "unsupported", "version-too-old"],
    ["above the floor", json({ service_version: "0.2.0", api_version: "v2" }), {}, "available", null],
    ["at the ceiling", json({ service_version: "0.3.0", api_version: "v2" }), { maxServiceVersion: "0.3.0" }, "available", null],
    ["above the ceiling", json({ service_version: "0.3.1", api_version: "v2" }), { maxServiceVersion: "0.3.0" }, "unsupported", "version-too-new"],
    ["inside a range", json({ service_version: "0.2.5", api_version: "v2" }), { minServiceVersion: "0.2.0", maxServiceVersion: "0.3.0" }, "available", null],
    ["custom floor, below", json({ service_version: "0.1.9", api_version: "v2" }), { minServiceVersion: "0.2.0" }, "unsupported", "version-too-old"],
    // Explicit null disables a bound; only an absent key takes the default.
    // `??` used to collapse the two, so "no floor" was unreachable.
    ["explicitly no floor", json({ service_version: "0.0.1", api_version: "v2" }), { minServiceVersion: null }, "available", null],
    ["explicitly no ceiling", json({ service_version: "9.9.9", api_version: "v2" }), { maxServiceVersion: null }, "available", null],
    ["both bounds off", json({ service_version: "9.9.9", api_version: "v2" }), { minServiceVersion: null, maxServiceVersion: null }, "available", null],
    ["absent keys keep the defaults", json({ service_version: "0.0.1", api_version: "v2" }), {}, "unsupported", "version-too-old"],
  ];

  for (const [label, response, requirements, state, reason] of cases) {
    const verdict = assessHealth(response, requirements);
    assert.equal(verdict.state, state, `${label}: ${verdict.reason}`);
    assert.equal(verdict.reason, reason, label);
    assert.equal(verdict.ok, state === HEALTH_STATES.AVAILABLE, `${label}: ok mirrors the state`);
  }
});

test("version comparison is numeric, not lexical", async () => {
  const core = await loadCore();
  assert.equal(core.compareVersions("0.10.0", "0.9.9"), 1, "10 is newer than 9");
  assert.equal(core.compareVersions("1.0.0", "1.0.0"), 0);
  assert.equal(core.compareVersions("0.1.0", "0.1.1"), -1);
  assert.equal(core.compareVersions("1.2.3-beta.1", "1.2.3"), 0, "suffix is ignored");
  assert.equal(core.compareVersions("1.2", "1.2.0"), null, "malformed input is not silently equal");
  assert.deepEqual(core.parseVersion("2.3.4"), { major: 2, minor: 3, patch: 4 });
  assert.equal(core.parseVersion("2.3"), null);
});

test("a misconfigured requirement is loud, not a disabled feature", async () => {
  const core = await loadCore();
  const healthy = json({ service_version: "0.1.0", api_version: "v2" });
  assert.throws(
    () => core.assessHealth(healthy, { minServiceVersion: "not-a-version" }),
    TypeError,
    "a floor that is not a version",
  );
  assert.throws(
    () => core.assessHealth(healthy, { maxServiceVersion: "1.x" }),
    TypeError,
    "a ceiling that is not a version",
  );
  // "Disabled" has exactly one spelling: null. A wrong type is a
  // configuration bug, not a silently coerced bound.
  assert.throws(() => core.assessHealth(healthy, { minServiceVersion: 1 }), TypeError);
  assert.throws(() => core.assessHealth(healthy, { maxServiceVersion: {} }), TypeError);
  // The protocol version cannot be disabled: a client that does not pin one
  // cannot claim compatibility.
  assert.throws(() => core.assessHealth(healthy, { apiVersion: null }), TypeError);
  assert.throws(() => core.assessHealth(healthy, { apiVersion: "" }), TypeError);
});

test("the poller stops on a terminal state", async () => {
  const core = await loadCore();
  const statuses = ["running", "running", "complete"];
  let polls = 0;
  const updates = [];
  const poller = core.startPolling({
    poll: async () => {
      const status = statuses[polls];
      polls += 1;
      return { id: "job-1", status };
    },
    intervalMs: 0,
    wait: () => ({ promise: Promise.resolve(), cancel: () => {} }),
    onUpdate: (snapshot, info) => updates.push(`${snapshot.status}:${info.polls}`),
  });

  const result = await poller.done;
  assert.equal(result.state, "terminal");
  assert.equal(result.snapshot.status, "complete");
  assert.equal(polls, 3, "one request per tick, sequential");
  assert.deepEqual(updates, ["running:1", "running:2", "complete:3"]);
});

test("every terminal status in the service vocabulary ends the poll", async () => {
  const core = await loadCore();
  for (const status of ["complete", "partial", "blocked", "failed", "interrupted"]) {
    let polls = 0;
    const result = await core
      .startPolling({
        poll: async () => {
          polls += 1;
          return { status };
        },
        intervalMs: 0,
        wait: () => ({ promise: Promise.resolve(), cancel: () => {} }),
      })
      .done;
    assert.equal(result.state, "terminal", status);
    assert.equal(polls, 1, `${status} ends immediately`);
  }
  // The vocabulary is the service's (kbapp/workflows/states.py TERMINAL).
  assert.deepEqual(
    [...core.TERMINAL_STATUSES].sort(),
    ["blocked", "complete", "failed", "interrupted", "partial"],
  );
});

test("cancel stops the loop: no further request, no further callback", async () => {
  const core = await loadCore();
  let polls = 0;
  const updates = [];
  const poller = core.startPolling({
    poll: async () => {
      polls += 1;
      return { status: "running", polls };
    },
    intervalMs: 0,
    // Resolve only when the test says so, so the poller is suspended between
    // ticks exactly like it would be in the UI.
    wait: () => {
      let release;
      const promise = new Promise((resolve) => {
        release = resolve;
      });
      return { promise, cancel: () => release() };
    },
    onUpdate: (_snapshot) => updates.push(_snapshot.polls),
  });

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(polls, 1, "first tick ran");
  assert.deepEqual(updates, [1]);

  poller.cancel();
  assert.equal(poller.isCancelled(), true);

  const result = await poller.done;
  assert.equal(result.state, "cancelled");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(polls, 1, "cancel must not issue another request");
  assert.deepEqual(updates, [1], "cancel must not deliver another snapshot");
});

test("cancel during an in-flight request settles immediately and ignores its result", async () => {
  const core = await loadCore();
  let resolvePoll;
  let callbacks = 0;
  const poller = core.startPolling({
    poll: () =>
      new Promise((resolve) => {
        resolvePoll = resolve;
      }),
    intervalMs: 0,
    wait: () => ({ promise: Promise.resolve(), cancel: () => {} }),
    onUpdate: () => {
      callbacks += 1;
    },
  });

  await new Promise((resolve) => setImmediate(resolve));
  poller.cancel();
  const result = await poller.done;
  assert.equal(result.state, "cancelled");

  // The in-flight response arrives after the cancellation: it must not
  // resurrect the loop or fire a callback.
  resolvePoll({ status: "running" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(callbacks, 0);
});

test("a failed poll rejects the poller and is not retried", async () => {
  const core = await loadCore();
  let polls = 0;
  const poller = core.startPolling({
    poll: async () => {
      polls += 1;
      throw new core.ServiceClientError("服务返回 500", { kind: "http", status: 500 });
    },
    intervalMs: 0,
    wait: () => ({ promise: Promise.resolve(), cancel: () => {} }),
  });

  await assert.rejects(poller.done, (error) => {
    assert.equal(error.kind, "http");
    return true;
  });
  assert.equal(polls, 1, "one failed poll is presented, not retried");
});

test("pollTask wires submit + poll through one client", async () => {
  const core = await loadCore();
  const { calls, request } = recorder({
    [`GET ${BASE}/api/session`]: json({ token: "t0k" }),
    // Real POST /api/documents/process shape (kbapp/web/document_api.py:178).
    [`POST ${BASE}/api/documents/process`]: json({ id: "job-7", state: "started" }),
    [`GET ${BASE}/api/documents/jobs/job-7`]: json({ id: "job-7", status: "complete", progress: "done" }),
  });
  const client = core.createServiceClient({ baseUrl: BASE, request });

  const submitted = await client.submitTask("/api/documents/process", { paper_id: "p" });
  assert.equal(submitted.outcome, "queued", "an id means pollable");
  assert.equal(submitted.taskId, "job-7");

  const poller = client.pollTask({
    taskPath: `/api/documents/jobs/${submitted.taskId}`,
    intervalMs: 0,
    wait: () => ({ promise: Promise.resolve(), cancel: () => {} }),
  });
  const result = await poller.done;
  assert.equal(result.state, "terminal");
  assert.equal(result.snapshot.progress, "done");

  const taskPoll = calls.find((call) => call.url.endsWith("/jobs/job-7"));
  assert.equal(taskPoll.options.errorDelayMax, 0, "polling carries the discipline too");
  assert.equal(taskPoll.options.timeout, 10000);
  assert.equal("x-kb-token" in taskPoll.options.headers, false, "polling is a GET");
});

test("an id-less submit is an outcome, not a failure", async () => {
  // The reviewer's finding: POST /api/jobs answers {'state': 'started'} or
  // {'state': 'running', 'message': ...} and never an id
  // (kbapp/workflows/jobs.py:42/46/48/53/68). Reporting that as a
  // retryable:false contract error told the user "submit failed" for a submit
  // that had succeeded.
  const core = await loadCore();
  const cases = [
    ["accepted with no id", { state: "started" }, "started", null],
    ["already running", { state: "running", message: "更新任务已在运行" }, "running", "更新任务已在运行"],
    ["running without a message", { state: "running" }, "running", null],
    ["a 2xx with no state", {}, "unknown", null],
    ["a non-object body", [], "unknown", null],
  ];

  for (const [label, body, outcome, message] of cases) {
    const client = core.createServiceClient({
      baseUrl: BASE,
      request: async (method, url) =>
        url.endsWith("/api/session") ? json({ token: "t0k" }) : json(body),
    });
    const submitted = await client.submitTask("/api/jobs", { action: "update" });
    assert.equal(submitted.outcome, outcome, label);
    assert.equal(submitted.taskId, null, `${label}: no id to poll`);
    if (message !== null || outcome === "running") {
      assert.equal(submitted.message, message, `${label}: the service's text is kept`);
    }
  }
});

test("a real vector submit names its task and stays pollable", async () => {
  // kbapp/workflows/vector_jobs.py:25 both starts and names the task.
  const core = await loadCore();
  const client = core.createServiceClient({
    baseUrl: BASE,
    request: async (method, url) =>
      url.endsWith("/api/session")
        ? json({ token: "t0k" })
        : json({ state: "started", id: "vec-1", kind: "vectors", message: "已启动本地向量索引" }),
  });
  const submitted = await client.submitTask("/api/jobs", { action: "vectors" });
  assert.equal(submitted.outcome, "queued");
  assert.equal(submitted.taskId, "vec-1");
});

test("submit classification covers the whole service vocabulary", async () => {
  const core = await loadCore();
  assert.deepEqual([...core.IDLESS_SUBMIT_STATES], ["started", "running"]);
  assert.deepEqual(core.classifySubmit({ id: "a", task_id: "b" }), {
    outcome: "queued",
    taskId: "a",
  });
  assert.deepEqual(core.classifySubmit({ task_id: "b" }), {
    outcome: "queued",
    taskId: "b",
  });
  // An empty id is not an id: fall through to the state classification.
  assert.equal(core.classifySubmit({ id: "", state: "started" }).outcome, "started");
  assert.equal(core.classifySubmit(null).outcome, "unknown");
});

test("the timer wait is cancellable and resolves once", async () => {
  const core = await loadCore();
  const gate = core.timerWait(60000);
  let resolved = 0;
  gate.promise.then(() => {
    resolved += 1;
  });
  gate.cancel();
  gate.cancel();
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(resolved, 1, "a cancelled wait resolves immediately, exactly once");
});
