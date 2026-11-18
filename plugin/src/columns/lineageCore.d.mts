/**
 * Types for lineageCore.mjs; the implementation is plain ESM on purpose so
 * tests/test_plugin_column.cjs can run it without Zotero or a build step.
 * `tsc` does not type-check the .mjs file itself (see ADR 0010), so this
 * declaration is the single typed view of the module.
 */

export type LineageState = "verified" | "pending" | "gap" | "empty";

export type LineageBadgeState = Exclude<LineageState, "empty">;

export interface LineageCounts {
  confirmed: number;
  pending: number;
  gaps: number;
}

export interface LineageStateBadge {
  state: LineageBadgeState;
  symbol: string;
  label: string;
  className: string;
  color: string;
}

export interface LineageCellBadge extends LineageStateBadge {
  counts: LineageCounts;
  title: string;
}

export interface LineageLocatePayload {
  source: "lineage-column";
  itemKey: string;
  libraryID: number | null;
  state: LineageBadgeState;
}

export interface LineageColumnOption {
  dataKey: string;
  label: string;
  pluginID: string;
  enabledTreeIDs: string[];
  showInColumnPicker: boolean;
  flex: number;
  minWidth: number;
  zoteroPersist: string[];
}

type DataProvider = (item: unknown, dataKey: string) => string;
type RecordResolver = (item: unknown, dataKey: string) => unknown;

export interface LineageColumnRegistration {
  /** Namespaced dataKey returned by registerColumn; null when registration failed. */
  dataKey: string | null;
  option: LineageColumnOption;
  /** Idempotent: unregisters by the returned key; false when nothing was registered. */
  dispose(): boolean;
}

export interface LineageColumnHooks {
  dataProvider?: DataProvider;
  renderCell?: (this: unknown, ...args: any[]) => unknown;
}

export declare const LINEAGE_STATES: readonly LineageBadgeState[];
export declare const LINEAGE_LOCATE_EVENT: string;
export declare const LINEAGE_COLUMN_DATA_KEY: string;
export declare const fakeLineageDataProvider: DataProvider;

export declare function toCount(value: unknown): number;
export declare function normalizeCounts(record: unknown): LineageCounts;
export declare function badgeState(counts: unknown): LineageState;
export declare function stateBadge(state: string): LineageStateBadge | null;
export declare function badgeForCounts(counts: unknown): LineageStateBadge | null;
export declare function relationCount(counts: unknown): number;
export declare function badgeTitle(counts: unknown): string;
export declare function cellText(counts: unknown): string;
export declare function parseCellText(text: unknown): LineageCounts | null;
export declare function badgeForText(text: unknown): LineageCellBadge | null;
export declare function createDataProvider(
  resolveRecord: RecordResolver,
): DataProvider;
export declare function isItemRef(ref: unknown): boolean;
export declare function itemKeyOf(item: unknown): string;
export declare function itemRefOf(
  context: unknown,
  index: unknown,
  doc?: unknown,
): unknown;
export declare function stableHash(text: unknown): number;
export declare function fakeLineageFor(key: unknown): LineageCounts;
export declare function lineageColumnOption(pluginID: unknown): LineageColumnOption;
export declare function createColumnRegistration(
  manager: {
    registerColumn(option: unknown): unknown;
    unregisterColumn(dataKey: string): unknown;
  },
  pluginID: unknown,
  hooks?: LineageColumnHooks,
): LineageColumnRegistration;
export declare function createLocatePayload(input?: {
  itemKey?: unknown;
  libraryID?: unknown;
  state?: unknown;
}): LineageLocatePayload | null;
export declare function dispatchLineageLocate(
  target: unknown,
  payload: LineageLocatePayload | null,
): boolean;
export declare function listenLineageLocate(
  target: unknown,
  handler: (payload: LineageLocatePayload | null) => void,
): () => void;
