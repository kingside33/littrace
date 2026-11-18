/** Types for columnDataCore.mjs; the implementation is plain ESM on purpose. */

export type ReadingState = "complete" | "partial" | "none";

export interface ReadingCounts {
  state: ReadingState;
  readPages: number;
  totalPages: number;
}

export interface ReadingStateBadge {
  state: ReadingState;
  symbol: string;
  label: string;
  className: string;
  color: string;
}

export interface ReadingCellBadge extends ReadingStateBadge {
  title: string;
  reading: ReadingCounts;
}

export interface PaperCacheOptions {
  ttlMs?: number;
  failureTtlMs?: number;
  maxEntries?: number;
  now?: () => number;
}

export type PaperCacheEntry = { ok: boolean; data: any };

export interface PaperCache {
  read(key: string): PaperCacheEntry | undefined;
  store(key: string, data: any): void;
  storeFailure(key: string): void;
  inFlight(key: string): Promise<unknown> | undefined;
  track(key: string, promise: Promise<unknown>): Promise<unknown>;
  invalidate(key: string): void;
  clear(): void;
  readonly size: number;
}

export type ColumnDataProvider = (item: unknown, dataKey?: string) => string;

export type PaperIdentity = {
  key: string;
  libraryID: number;
  itemKey: string;
};

export interface PaperDataProvidersOptions {
  fetchPaper: (paper: string, context: { libraryID: number; itemKey: string }) => Promise<any>;
  cache?: PaperCache;
  cacheOptions?: PaperCacheOptions;
  onUpdate?: (key: string) => void;
}

export interface PaperDataProviders {
  cache: PaperCache;
  identityOf(item: unknown): PaperIdentity | null;
  lineageProvider: ColumnDataProvider;
  readingProvider: ColumnDataProvider;
  invalidateAll(): void;
}

export declare const PAPER_ENDPOINT: string;
export declare const PAPER_CACHE_DEFAULTS: {
  ttlMs: number;
  failureTtlMs: number;
  maxEntries: number;
};
export declare const READING_STATES: readonly ReadingState[];

export declare function paperKeyOf(libraryID: unknown, itemKey: unknown): string;
export declare function paperPath(libraryID: unknown, itemKey: unknown): string;
export declare function lineageCountsFromRelations(relations: unknown): {
  confirmed: number;
  pending: number;
  gaps: number;
};
export declare function readingFromUnderstanding(understanding: unknown): ReadingCounts | null;
export declare function readingCellText(reading: unknown): string;
export declare function parseReadingCellText(text: unknown): ReadingCounts | null;
export declare function readingBadgeForText(text: unknown): ReadingCellBadge | null;
export declare function createPaperCache(options?: PaperCacheOptions): PaperCache;
export declare function createPaperDataProviders(
  options: PaperDataProvidersOptions,
): PaperDataProviders;
