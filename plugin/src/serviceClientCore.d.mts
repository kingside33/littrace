/** Types for serviceClientCore.mjs; the implementation is plain ESM on purpose. */

export type TransportResponse = {
  status: number;
  text: string;
  error?: unknown;
};

export type Transport = (
  method: string,
  url: string,
  options: Record<string, unknown>,
) => Promise<TransportResponse>;

export type HealthState = "available" | "unsupported" | "offline";

export type HealthVerdict = {
  state: HealthState;
  ok: boolean;
  reason: string | null;
  status: number;
  serviceVersion?: string | null;
  apiVersion?: string | null;
};

export type Requirements = {
  apiVersion?: string;
  /** Absent = default floor. Explicit `null` = no floor check. */
  minServiceVersion?: string | null;
  /** Absent = default ceiling (none). Explicit `null` = no ceiling check. */
  maxServiceVersion?: string | null;
};

/**
 * The answer to a task submit.
 *
 * - `queued`  the service named a task; `taskId` is pollable
 * - `started` accepted, but the service names no task (`POST /api/jobs`)
 * - `running` a task was already running; `message` is the service's text
 * - `unknown` 2xx without a recognizable outcome; not a success, not retried
 */
export type SubmitResult =
  | { outcome: "queued"; taskId: string; message: string | null; data: any }
  | { outcome: "started"; taskId: null; state: string; message: string | null; data: any }
  | { outcome: "running"; taskId: null; state: string; message: string | null; data: any }
  | { outcome: "unknown"; taskId: null; state: string | null; data: any };

export type WaitHandle = { promise: Promise<void>; cancel: () => void };

export type PollResult = {
  state: "terminal" | "cancelled";
  snapshot?: any;
  polls: number;
};

export type Poller = {
  done: Promise<PollResult>;
  cancel: () => void;
  isCancelled: () => boolean;
};

export type PollOptions = {
  taskPath: string;
  intervalMs?: number;
  wait?: (ms: number) => WaitHandle;
  isTerminal?: (snapshot: any) => boolean;
  onUpdate?: (snapshot: any, info: { polls: number }) => void;
};

export type ServiceClient = {
  baseUrl: string;
  sessionToken(): Promise<string>;
  invalidateToken(): void;
  send(method: string, path: string, body?: unknown): Promise<any>;
  get(path: string): Promise<any>;
  post(path: string, body?: unknown): Promise<any>;
  probe(): Promise<HealthVerdict>;
  submitTask(path: string, body?: unknown): Promise<SubmitResult>;
  pollTask(options: PollOptions): Poller;
};

export declare const TERMINAL_STATUSES: readonly string[];
export declare const SAFE_METHODS: readonly string[];
export declare const IDLESS_SUBMIT_STATES: readonly string[];
export declare const REQUEST_DISCIPLINE: { errorDelayMax: number; timeout: number };
export declare const HEALTH_STATES: {
  AVAILABLE: "available";
  UNSUPPORTED: "unsupported";
  OFFLINE: "offline";
};
export declare const DEFAULT_REQUIREMENTS: Requirements;

export declare class ServiceClientError extends Error {
  kind: "offline" | "http" | "session" | "parse" | "contract";
  status: number;
  detail: string | null;
  retryable: boolean;
}

export declare function joinUrl(baseUrl: string, path: string): string;
export declare function buildRequest(
  method: string,
  path: string,
  context?: { baseUrl: string; token?: string | null; body?: unknown },
): { url: string; options: Record<string, unknown> };
export declare function parseVersion(
  text: string,
): { major: number; minor: number; patch: number } | null;
export declare function compareVersions(
  left: string | { major: number; minor: number; patch: number },
  right: string | { major: number; minor: number; patch: number },
): number | null;
export declare function assessHealth(
  response: { status?: number; text?: string },
  requirements?: Requirements,
): HealthVerdict;
export declare function classifySubmit(data: any): {
  outcome: "queued" | "started" | "running" | "unknown";
  taskId: string | null;
  state?: string | null;
  message?: string | null;
};
export declare function timerWait(ms: number): WaitHandle;
export declare function startPolling(options: {
  poll: () => Promise<any>;
  intervalMs?: number;
  wait?: (ms: number) => WaitHandle;
  isTerminal?: (snapshot: any) => boolean;
  onUpdate?: (snapshot: any, info: { polls: number }) => void;
}): Poller;
export declare function createServiceClient(options: {
  baseUrl: string;
  request: Transport;
  requirements?: Requirements;
}): ServiceClient;
