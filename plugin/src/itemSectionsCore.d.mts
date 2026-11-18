/** Types for itemSectionsCore.mjs; the implementation is plain ESM on purpose. */

export type SectionID = "evidence" | "lineage" | "reading";

export type PanelStatus = "loading" | "ready" | "empty" | "error" | "offline";

export type PanelState = {
  cacheKey: string;
  status: PanelStatus;
  messageKey: string | null;
  actionKey: string | null;
  error: string | null;
  retryable: boolean;
  snapshot: any;
};

export type ItemRef = {
  _objectType?: string;
  key?: unknown;
  libraryID?: unknown;
};

export type SnapshotLoader = (cacheKey: string) => Promise<any>;

export type PanelCache = {
  get(cacheKey: string): any | null;
  peek(cacheKey: string): any | null;
  put(cacheKey: string, value: any): any;
  fetch(cacheKey: string): Promise<any>;
  invalidate(cacheKey: string): boolean;
  size(): number;
  inflightSize(): number;
  stats(): { hits: number; misses: number; requests: number; size: number };
  clear(): void;
};

export type RenderPlan = {
  state: PanelState;
  request: boolean | null;
};

export type RenderGeneration = {
  begin(): number;
  isCurrent(token: number): boolean;
  current(): number;
};

export type SectionRegistration = {
  dataKey: string | null;
  option: Record<string, unknown> | null;
  dispose(): boolean;
};

export type SectionOption = {
  paneID: string;
  pluginID: string;
  header: { l10nID: string; icon: string };
  sidenav: { l10nID: string; icon: string };
};

export type SectionRow =
  | { kind: "count"; labelKey: string; value: string }
  | { kind: "excerpt"; page: number; text: string }
  | {
      kind: "relation";
      id: string;
      label: string;
      peerTitle: string;
      peerYear: number | null;
      direction: "previous" | "following" | "unknown";
      grade: string;
      gradeLabel: string;
      note: string;
    }
  | { kind: "coverage"; readPages: number; totalPages: number; state: string }
  | { kind: "page-class"; labelKey: string; value: string }
  | { kind: "gap"; text: string };

export declare const SECTIONS: readonly SectionID[];
export declare const PANEL_STATES: {
  LOADING: "loading";
  READY: "ready";
  EMPTY: "empty";
  ERROR: "error";
  OFFLINE: "offline";
};

export declare function cacheKeyFor(item: ItemRef | null, defaults?: { libraryID?: number }): string;
export declare function snapshotPath(cacheKey: string): string;
export declare function paperIdFor(state: PanelState | null): string;
export declare function workbenchUrl(baseUrl: string, page: string, paperId: string): string;
export declare function loadingState(cacheKey: string): PanelState;
export declare function idleState(): PanelState;
export declare function offlineState(cacheKey: string): PanelState;
export declare function errorState(cacheKey: string, error: unknown): PanelState;
export declare function stateForFailure(cacheKey: string, error: unknown): PanelState;
export declare function stateForStatus(cacheKey: string, status: number): PanelState;
export declare function isEmptyPayload(sectionID: string, snapshot: any): boolean;
export declare function readyState(sectionID: string, cacheKey: string, snapshot: any): PanelState;
export declare function acceptResponse(
  state: PanelState | null,
  cacheKey: string,
  nextState: PanelState | null,
): PanelState | null;
export declare function sectionRows(sectionID: string, snapshot: any): SectionRow[];
export declare function canParse(snapshot: any): boolean;
export declare function sectionOptions(
  pluginID: string,
  kind: string,
  iconBase?: string,
  namespace?: string,
): SectionOption;
export declare function createSectionRegistration(
  manager: { registerSection(option: any): string | false; unregisterSection(key: string): boolean },
  pluginID: string,
  kind: string,
  hooks?: Record<string, unknown>,
  iconBase?: string,
  namespace?: string,
): SectionRegistration;
export declare function createPanelCache(options: {
  load: SnapshotLoader;
  limit?: number;
  now?: () => number;
}): PanelCache;
export declare function planRender(sectionID: string, cacheKey: string, cached: any): RenderPlan;
export declare function settleRender(
  sectionID: string,
  cacheKey: string,
  outcome: { snapshot?: any; status?: number; error?: unknown },
): PanelState;
export declare function createRenderGeneration(): RenderGeneration;
