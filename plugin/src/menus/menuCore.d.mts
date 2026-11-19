/** Types for menuCore.mjs; the implementation is plain ESM on purpose. */

export type MenuTarget = "main/library/item" | "main/menubar/tools";

export type MenuGroupID = "item" | "tools";

export type MenuServiceState = "checking" | "online" | "incompatible" | "offline";

export type MenuGroup = {
  id: MenuGroupID;
  target: MenuTarget;
  menuID: string;
  labelKey: string | null;
  shape: "submenu" | "flat";
};

export type MenuEntry = {
  id: string;
  group: MenuGroupID;
  labelKey: string;
  incompatibleLabelKey?: string;
  command: string;
  menuType: "menuitem";
  needsService: boolean;
  selection: "none" | "any" | "single";
  compare: "none" | "add" | "run" | "clear";
  visible: "always" | "offline" | "compare";
  registered: boolean;
  blockedReasonKey?: string;
};

export type PaperTarget = { libraryID: number; key: string };

export type SelectionCount = { count: number };

export type MenuContextInput = {
  health?: unknown;
  pending?: boolean;
  service?: MenuServiceState;
  selection?: number | PaperTarget[] | SelectionCount;
  compareSize?: number;
};

export type MenuContext = {
  service: MenuServiceState;
  selection: SelectionCount;
  compareSize: number;
};

export type EntryState = {
  id: string;
  group: MenuGroupID;
  labelKey: string;
  command: string;
  menuType: "menuitem";
  visible: boolean;
  enabled: boolean;
  reasonKey: string | null;
};

export type MenuSpec = {
  id: MenuGroupID;
  target: MenuTarget;
  menuID: string;
  labelKey: string | null;
  shape: "submenu" | "flat";
  entries: Array<{
    id: string;
    labelKey: string;
    command: string;
    menuType: "menuitem";
  }>;
};

export type CompareSet = readonly PaperTarget[];

export type CompareAddResult = {
  entries: CompareSet;
  added: number;
  duplicate: number;
  full: number;
  size: number;
};

export type CompareRemoveResult = {
  entries: CompareSet;
  removed: boolean;
};

export type CompareReadiness = {
  size: number;
  ready: boolean;
  reasonKey: string | null;
};

export type CompareRunRequest = {
  path: string;
  body: { kind: "compare"; paper_ids: string[] };
};

export type ResolutionPair = { paperId: string; resolvedId: string | null };

export type ResolutionOutcome = {
  resolved: ReadonlyArray<string>;
  missing: ReadonlyArray<string>;
};

export type TargetsResult = {
  targets: ReadonlyArray<PaperTarget>;
  skipped: number;
};

export type GuideAction = { id: "settings" | "recheck" | "close"; labelKey: string };

export type GuideView = {
  service: MenuServiceState;
  titleKey: string;
  bodyKeys: ReadonlyArray<string>;
  actions: ReadonlyArray<GuideAction>;
};

export type VerdictCache = {
  read(): unknown;
  stale(now?: number): boolean;
  write(health: unknown, now?: number): unknown;
  clear(): void;
};

export const MENU_TARGETS: Readonly<{ ITEM: MenuTarget; TOOLS: MenuTarget }>;
export const MENU_IDS: Readonly<{ item: string; tools: string }>;
export const SERVICE_STATES: Readonly<{
  CHECKING: "checking";
  ONLINE: "online";
  INCOMPATIBLE: "incompatible";
  OFFLINE: "offline";
}>;
export const GATE_REASON_KEYS: Readonly<Record<string, string>>;
export const COMPARE_MIN: number;
export const COMPARE_MAX: number;
export const MENU_ENTRIES: ReadonlyArray<MenuEntry>;
export const MENU_GROUPS: ReadonlyArray<MenuGroup>;
export const EMPTY_COMPARE_SET: CompareSet;
export const EMPTY_SELECTION: SelectionCount;
export const GUIDE_CARD: Readonly<{
  titleKey: string;
  incompatibleTitleKey: string;
  bodyKeys: ReadonlyArray<string>;
  incompatibleKey: string;
  actions: ReadonlyArray<GuideAction>;
}>;
export const GUIDE_STATUS_KEYS: Readonly<Record<string, string>>;
export const FEEDBACK_KEYS: Readonly<Record<string, string>>;
export const PROBE_TTL_MS: number;

export declare function serviceStateOf(
  health: unknown,
  options?: { pending?: boolean },
): MenuServiceState;
export declare function targetOf(item: unknown): PaperTarget | null;
export declare function targetKeyOf(target: PaperTarget): string;
export declare function paperIdOf(target: PaperTarget): string;
export declare function targetsOf(items: unknown): TargetsResult;
export declare function compareSetAdd(
  entries: CompareSet | ReadonlyArray<unknown>,
  targets: ReadonlyArray<unknown>,
): CompareAddResult;
export declare function compareSetRemove(
  entries: CompareSet | ReadonlyArray<unknown>,
  key: string,
): CompareRemoveResult;
export declare function compareSetPaperIds(entries: CompareSet): string[];
export declare function compareReadiness(entries: CompareSet | ReadonlyArray<unknown>): CompareReadiness;
export declare function compareRunRequest(entries: CompareSet | ReadonlyArray<unknown>): CompareRunRequest | null;
export declare function compareRunBody(paperIds: ReadonlyArray<string>): CompareRunRequest | null;
export declare function resolutionOutcome(pairs: ReadonlyArray<ResolutionPair | null>): ResolutionOutcome;
export declare function normalizeSelection(
  value: unknown,
): SelectionCount;
export declare function menuContext(input?: MenuContextInput): MenuContext;
export declare function visibleOf(entry: MenuEntry, context: MenuContext | MenuContextInput): boolean;
export declare function gateReasonOf(
  entry: MenuEntry,
  context: MenuContext | MenuContextInput,
): string | null;
export declare function entryStateOf(
  entry: MenuEntry,
  context: MenuContext | MenuContextInput,
): EntryState;
export declare function labelKeyOf(entry: MenuEntry, service: MenuServiceState): string;
export declare function registeredEntries(groupId?: MenuGroupID | null): MenuEntry[];
export declare function menuStates(
  groupId: MenuGroupID,
  context?: MenuContextInput,
): ReadonlyArray<EntryState>;
export declare function menuSpecs(): ReadonlyArray<MenuSpec>;
export declare function guideView(context?: MenuContextInput): GuideView;
export declare function compareAddFeedback(result: CompareAddResult | null): string;
export declare function menuFluentKeys(): ReadonlyArray<string>;
export declare function createVerdictCache(options?: { ttl?: number }): VerdictCache;
