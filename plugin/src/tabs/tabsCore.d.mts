/**
 * Types for tabsCore.mjs; the implementation is plain ESM on purpose so
 * tests/test_plugin_tabs.cjs can run it without Zotero or a build step.
 * `tsc` does not type-check the .mjs file itself (see ADR 0010), so this
 * declaration is the single typed view of the module.
 */

export type TabId = "graph" | "search" | "research" | "approvals";

export interface TabEntry {
  id: TabId;
  hash: string;
  titleKey: string;
  builder: "iframe";
}

export interface TabOptions {
  baseUrl?: string;
  paper?: string;
}

export interface TabNodeSpec {
  kind: "iframe";
  tabId: TabId;
  src: string;
  titleKey: string;
}

export type CleanupErrorReporter = (error: unknown, id: string) => void;

/** One window's cleanup table; see the contract in tabsCore.mjs. */
export interface CleanupLedger {
  add(id: string, dispose: () => void): string;
  remove(id: string): boolean;
  clear(): number;
  has(id: string): boolean;
  readonly size: number;
  ids(): string[];
}

export type BridgeMode = "capture" | "native";

export interface BridgePlan {
  mode: BridgeMode;
  capture: boolean;
}

export type OpenMechanism = "zotero-tabs" | "dialog";

export interface OpenPlan {
  mechanism: OpenMechanism;
  preferred: OpenMechanism;
  fallback: boolean;
  reason: string;
}

export interface LocateRequest {
  tab: "graph";
  paper: string;
}

export declare const TAB_REGISTRY: readonly TabEntry[];
export declare const TAB_IDS: readonly TabId[];
export declare const TAB_BUILDERS: readonly string[];
export declare const DEEP_LINK_PREFIXES: readonly string[];
export declare const BRIDGE_MODES: readonly BridgeMode[];
export declare const DEFAULT_BRIDGE_MODE: BridgeMode;
export declare const OPEN_MECHANISMS: readonly OpenMechanism[];
export declare const DEFAULT_OPEN_MECHANISM: OpenMechanism;

export declare function tabById(id: unknown): TabEntry | null;
export declare function tabIdForHash(hash: unknown): TabId | null;
export declare function hashForTab(id: unknown): string | null;
export declare function tabUrl(id: unknown, options?: TabOptions): string | null;
export declare function tabNodeSpec(
  id: unknown,
  options?: TabOptions,
): TabNodeSpec | null;
export declare function createCleanupLedger(
  onError?: CleanupErrorReporter,
): CleanupLedger;
export declare function isDeepLink(href: unknown): boolean;
export declare function bridgePlan(mode: unknown): BridgePlan;
export declare function resolveOpenPlan(
  preferred: unknown,
  options?: { zoteroTabsAvailable?: boolean },
): OpenPlan;
export declare function locateRequestOf(detail: unknown): LocateRequest | null;
