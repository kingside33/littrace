/** Types for statusCore.mjs; the implementation is plain ESM on purpose. */

// The health verdict and the requirement window are S4's, not this feature's:
// the dot renders what the client decided, and a second structural copy here
// would let the two drift the moment S4 adds a field.
import type {
  HealthVerdict,
  Requirements,
} from "../serviceClientCore.mjs";

export type { HealthVerdict, Requirements };

export type DotState = "checking" | "online" | "outdated" | "offline";

export type DotView = {
  state: DotState;
  color: string;
  labelKey: string;
  reason: string | null;
  reasonKey: string;
  serviceVersion: string | null;
  minServiceVersion: string | null;
  blinking: boolean;
};

export type VersionRelation = {
  relation: "ok" | "too-old" | "too-new" | "unknown";
  serviceVersion: string | null;
  minServiceVersion: string | null;
  maxServiceVersion: string | null;
};

export type DiagnosticRow = {
  id: string;
  labelKey: string;
  tone: "ok" | "warn" | "bad" | "unknown";
  value: string | null;
  valueKey: string | null;
  /** Extra Fluent string appended after the value (e.g. a sync state word). */
  noteKey?: string | null;
  /** The service's own error text for this check, when it supplied one. */
  detail?: string | null;
};

export type DiagnosticInput = {
  health?: HealthVerdict | null;
  baseUrl?: string;
  models?: unknown;
  status?: unknown;
  requirements?: Requirements;
};

export type MenuItem = {
  id: string;
  labelKey: string;
  enabled: boolean;
};

export const DOT_STATES: Readonly<{
  CHECKING: "checking";
  ONLINE: "online";
  OUTDATED: "outdated";
  OFFLINE: "offline";
}>;

export const DOT_VIEWS: Readonly<Record<DotState, Readonly<{
  state: DotState;
  color: string;
  labelKey: string;
}>>>;

export const REASON_KEYS: Readonly<Record<string, string>>;
export const RELATION_KEYS: Readonly<Record<string, string>>;
export const OLLAMA_STATES: Readonly<{
  REACHABLE: "reachable";
  UNREACHABLE: "unreachable";
  UNKNOWN: "unknown";
}>;
export const OLLAMA_KEYS: Readonly<Record<string, string>>;
export const SYNC_STATES: Readonly<{
  SYNCED: "synced";
  RUNNING: "running";
  FAILED: "failed";
  UNKNOWN: "unknown";
}>;
export const SYNC_KEYS: Readonly<Record<string, string>>;

export const DIAGNOSTIC_ROWS: ReadonlyArray<Readonly<{ id: string; labelKey: string }>>;

export const STATUS_MENU: ReadonlyArray<Readonly<{
  id: string;
  labelKey: string;
  needsReachable: boolean;
}>>;

export const STATUS_ENDPOINTS: Readonly<{
  version: string;
  status: string;
  models: string;
}>;

export const STATUS_POLL_MS: number;

export function reasonKey(reason: string | null | undefined): string;
export function offlineHealth(reason?: string): HealthVerdict;
export function dotStateOf(health?: HealthVerdict | null): DotState;
export function dotView(
  health?: HealthVerdict | null,
  options?: { pending?: boolean; requirements?: Requirements },
): DotView;
export function minServiceVersionOf(requirements?: Requirements): string | null;
export function versionRelation(
  serviceVersion: string | null | undefined,
  requirements?: Requirements,
): VersionRelation;
export function portOf(baseUrl?: string): number | null;
export function ollamaState(models: unknown): "reachable" | "unreachable" | "unknown";
export function lastSync(status: unknown): {
  state: "synced" | "running" | "failed" | "unknown";
  at: string | null;
  error: string | null;
};
export function lastSyncAt(status: unknown): string | null;
export function displayStamp(value: unknown): string | null;
export function diagnosticRows(input?: DiagnosticInput): DiagnosticRow[];
export function failedRows(rows: DiagnosticRow[]): DiagnosticRow[];
export function menuItems(state: DotState): MenuItem[];
export function workbenchUrl(baseUrl?: string): string | null;
