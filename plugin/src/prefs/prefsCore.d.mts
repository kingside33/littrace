/** Types for prefsCore.mjs; the implementation is plain ESM on purpose. */

export type PrefType = "bool" | "string";
export type PrefUIMode = "text" | "checkbox" | null;

export type PrefEntry = {
  key: string;
  group: "core" | "connection" | "integration" | "pro";
  type: PrefType;
  default: boolean | string;
  ui: PrefUIMode;
  requiresPro: boolean;
  labelKey: string | null;
  descriptionKey: string | null;
  readBy: string;
};

export type PrefGroup = {
  id: string;
  titleKey: string;
  descriptionKey: string;
};

export type LicenseView = {
  state: "not-running" | "authorized" | "unauthorized" | "unknown";
  labelKey: string;
};

export type PlatformCard = {
  supported: boolean;
  titleKey: string | null;
  descriptionKey: string | null;
};

export const PREF_PREFIX: string;
export const FTL_NAMESPACE: string;
export const FTL_SEPARATOR: string;
export const SERVICE_HOST: string;
export const DEFAULT_SERVICE_PORT: number;
export const PANE_LABEL_KEY: string;
export const PREF_GROUPS: ReadonlyArray<Readonly<PrefGroup>>;
export const PREF_ENTRIES: ReadonlyArray<Readonly<PrefEntry>>;
export const PANE_FLUENT_KEYS: ReadonlyArray<string>;
export const STARTUP_ONLY_KEYS: ReadonlyArray<string>;
export const SUPPORTED_PLATFORMS: ReadonlyArray<string>;
export const PRO_STATES: Readonly<{
  NOT_RUNNING: "not-running";
  AUTHORIZED: "authorized";
  UNAUTHORIZED: "unauthorized";
  UNKNOWN: "unknown";
}>;
export const PRO_KEYS: Readonly<Record<string, string>>;
export const CLIENT_ORIGINS: Readonly<{ REUSE: "reuse"; REBUILD: "rebuild" }>;

export function clientOriginVerdict(
  currentBaseUrl: string | null | undefined,
  desiredBaseUrl: string | null | undefined,
): "reuse" | "rebuild";

export function ftlId(key: string): string;
export function prefKey(key: string): string;
export function prefEntry(key: string): Readonly<PrefEntry> | null;
export function entriesForGroup(group: string): Array<Readonly<PrefEntry>>;
export function prefFluentKeys(): string[];
export function normalizePort(value: unknown, fallback?: number): number;
export function serviceBaseUrl(port: unknown): string;
export function licenseView(response: unknown): LicenseView;
export function platformCard(platform: string): PlatformCard;

/** Result of validating a typed cache directory. */
export interface CacheDirVerdict {
  ok: boolean;
  /** Normalized absolute path, or "" when the input was empty/refused. */
  value: string;
  /** Fluent key describing why it was refused, or null when ok. */
  reason: string | null;
}

/** Empty means "server default"; anything non-empty must be absolute. */
export declare function normalizeCacheDir(value: unknown): CacheDirVerdict;

/** Binary-unit size for the cache readout ("1.5 KB", "5.0 MB"). */
export declare function formatBytes(bytes: unknown): string;
