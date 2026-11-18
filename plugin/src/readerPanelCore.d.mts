/** Types for readerPanelCore.mjs; the implementation is plain ESM on purpose. */

export type CardKind = "question" | "answer" | "evidence" | "gap" | "guide" | "failure";

export type SelectionAction = "evidence" | "lineage";

export interface SelectionButton {
  readonly id: string;
  readonly action: SelectionAction;
  readonly labelKey: string;
}

export interface Citation {
  readonly authors: readonly string[];
  readonly year: string | null;
  /** 真实引用常常只有条目短标识与标题，见 normalizeCitation。 */
  readonly citekey: string;
  readonly title: string;
  readonly paperId: string;
  readonly page: number | null;
  readonly pageEnd: number | null;
  /** 可跳页的 open-pdf 深链；跳不了是 null，不是空串。 */
  readonly href: string | null;
}

export interface CitationView {
  readonly label: string;
  readonly page: number | null;
  readonly href: string | null;
}

export interface EvidenceView {
  readonly quote: string;
  readonly label: string;
  readonly href: string | null;
}

export interface QuestionCard {
  readonly id: string;
  readonly kind: "question";
  readonly text: string;
  readonly source: string;
  readonly retryOf: string | null;
}

export interface AnswerCard {
  readonly id: string;
  readonly kind: "answer";
  readonly conclusions: readonly {
    readonly text: string;
    readonly citations: readonly Citation[];
  }[];
  readonly evidence: readonly { readonly quote: string; readonly citation: Citation }[];
}

export interface EvidenceCard {
  readonly id: string;
  readonly kind: "evidence";
  readonly quote: string;
  readonly citation: Citation;
  readonly kindKey: string | null;
}

export interface GapCard {
  readonly id: string;
  readonly kind: "gap";
  readonly reason: string;
  /** Which fixed hint the card shows; see GAP_HINTS. */
  readonly hintKey: string;
}

/** A gap input: the bare sentence, or the sentence plus its cause. */
export type GapInput =
  | string
  | {
      readonly reason: string;
      readonly hint?: "EVIDENCE" | "WARNING" | "TRUNCATED" | "LINEAGE";
      readonly hintKey?: string;
    };

export interface GuideCard {
  readonly id: string;
  readonly kind: "guide";
  readonly message: string;
  readonly messageKey: string;
  readonly hintKey: string;
  readonly actionKey: string;
}

export interface FailureCard {
  readonly id: string;
  readonly kind: "failure";
  readonly message: string;
  readonly questionId: string | null;
  readonly retry: { readonly labelKey: string; readonly action: "retry" };
}

export type PanelCard =
  | QuestionCard
  | AnswerCard
  | EvidenceCard
  | GapCard
  | GuideCard
  | FailureCard;

export interface Stage {
  readonly labelKey: string;
  readonly progress: number;
}

export interface PanelState {
  readonly cards: readonly PanelCard[];
  readonly stage: Stage | null;
  readonly busy: boolean;
}

export interface CardView {
  readonly id: string;
  readonly kind: CardKind;
  /** 缺口灰块永远为 false —— 契约,不是渲染层的选择。 */
  readonly expandable: boolean;
  readonly toggle: { readonly labelKey: string } | null;
  readonly text?: string;
  readonly source?: string;
  readonly retryOf?: string | null;
  readonly conclusions?: readonly {
    readonly text: string;
    readonly citations: readonly CitationView[];
  }[];
  readonly evidence?: readonly EvidenceView[];
  readonly reason?: string;
  readonly hintKey?: string;
  readonly message?: string;
  readonly messageKey?: string;
  readonly retry?: { readonly labelKey: string; readonly action: "retry" } | null;
  readonly quote?: string;
  readonly label?: string;
  readonly page?: number | null;
  readonly href?: string | null;
  readonly kindKey?: string | null;
  readonly action?: { readonly labelKey: string; readonly action: "open-settings" } | null;
}

export const CARD_KINDS: {
  readonly QUESTION: "question";
  readonly ANSWER: "answer";
  readonly EVIDENCE: "evidence";
  readonly GAP: "gap";
  readonly GUIDE: "guide";
  readonly FAILURE: "failure";
};

export const SELECTION_ACTIONS: {
  readonly EVIDENCE: "evidence";
  readonly LINEAGE: "lineage";
};

export const SELECTION_BUTTONS: readonly SelectionButton[];
export const CARRIERS: readonly string[];
export const DEFAULT_CARRIER: string;
export const MESSAGE_KEYS: readonly string[];

export interface CarrierPlan {
  readonly id: string;
  readonly layout: "wide" | "sidebar" | "compact";
  readonly entry: "toolbar-button" | "none" | "item-pane-section";
  readonly host: "standalone-window" | "reader-document" | "item-pane-section";
}

export function carrierPlan(carrier: string): CarrierPlan;
export function sourceForAction(action: SelectionAction | null): string;
export function actionForSource(source: string): SelectionAction | null;
export function messageId(key: string, namespace: string): string;

export function createState(): PanelState;
export function ask(
  state: PanelState,
  question: string,
  options?: { source?: string },
): PanelState;
export function stage(
  state: PanelState,
  event: { labelKey: string; progress?: number },
): PanelState;

export interface CitationInput {
  authors?: readonly string[] | string;
  year?: number | string | null;
  citekey?: string;
  title?: string;
  paperId?: string;
  paper_id?: string;
  url?: string;
  page?: number | null;
  pageEnd?: number | null;
}

export interface Outcome {
  readonly conclusions?: readonly {
    text: string;
    citations?: readonly CitationInput[];
  }[];
  readonly gaps?: readonly GapInput[];
  readonly evidence?: readonly { quote?: string; citation?: CitationInput }[];
}

export interface CitationContext {
  /** 当前 PDF 的文献标识，用于判断一条引用是不是同篇文献。 */
  paperId?: string;
  /** 当前 PDF 的 Zotero 附件 key，只用于同篇文献的深链。 */
  attachmentKey?: string;
}

export function answer(
  state: PanelState,
  outcome: Outcome,
  options?: CitationContext,
): PanelState;
export function evidence(
  state: PanelState,
  outcome: {
    hits?: readonly {
      quote?: string;
      kindKey?: string | null;
      citation?: CitationInput;
    }[];
    gaps?: readonly GapInput[];
  },
  options?: CitationContext,
): PanelState;
export function guide(
  state: PanelState,
  options?: {
    message?: string;
    messageKey?: string;
    hintKey?: string;
    actionKey?: string;
  },
): PanelState;
export function fail(state: PanelState, failure: { message: string }): PanelState;
export function retry(state: PanelState, cardId: string): PanelState;
export function citationLabel(citation: CitationInput): string;
export function cardView(card: PanelCard): CardView;
export function cardViews(state: PanelState): readonly CardView[];
export function panelStatus(
  state: PanelState,
): { readonly kind: "empty" } | { readonly kind: "busy"; readonly stage: Stage | null } | { readonly kind: "ready" };

/* ── U3: 真实数据 ─────────────────────────────────────────────────────── */

export const SERVICE_ENDPOINTS: {
  readonly ASK: "/api/agent-runs";
  readonly STATUS: "/api/status";
  readonly SEARCH: "/api/v2/search";
  readonly GRAPH: "/api/graph";
};
export const QUESTION_MAX: number;
export const RUN_ACTIVE_STATES: readonly string[];
export const RUN_TERMINAL_STATES: readonly string[];
export const EVIDENCE_HIT_LIMIT: number;
export const LINEAGE_EDGE_LIMIT: number;

export const REQUEST_KINDS: {
  readonly RUN: "run";
  readonly READ: "read";
  readonly BLOCKED: "blocked";
};

export interface RunRequest {
  readonly kind: "run";
  readonly path: string;
  readonly body: {
    readonly kind: "answer";
    readonly query: string;
    readonly filters: { readonly paper?: string };
  };
}

export interface ReadRequest {
  readonly kind: "read";
  readonly shape: "evidence" | "lineage";
  readonly path: string;
}

export interface BlockedRequest {
  readonly kind: "blocked";
  readonly message: string;
}

export type PanelRequest = RunRequest | ReadRequest | BlockedRequest;

export function cloudDisabled(status: unknown): boolean;
export function isCloudConfigError(message: unknown): boolean;
export function askRunRequest(question: string, options?: { paperId?: string }): RunRequest;
export function evidenceSearchRequest(
  selection: string,
  options?: { paperId?: string; limit?: number },
): ReadRequest;
export function lineageRequest(
  paperId: string,
  options?: { limit?: number },
): ReadRequest | BlockedRequest;
export function requestFor(
  action: SelectionAction | null,
  text: string,
  options?: { paperId?: string },
): PanelRequest;

export function stageForRun(run: unknown, previousProgress?: number): Stage | null;

export type RunOutcome =
  | ({ readonly ok: true } & Outcome)
  | { readonly ok: false; readonly kind: "guide" | "failure"; readonly message: string };

export function answerOutcome(payload: unknown): { readonly ok: true } & Outcome;
export function outcomeFromRun(run: unknown): RunOutcome | null;
export function evidenceOutcome(payload: unknown): {
  readonly ok: true;
  readonly hits: readonly {
    readonly quote: string;
    readonly kindKey: string | null;
    readonly citation: CitationInput;
  }[];
  readonly gaps: readonly string[];
};
export function lineageOutcome(
  payload: unknown,
  options?: { paperId?: string },
): { readonly ok: true } & Outcome;

export function withPageParam(url: string, page: number | null | undefined): string;
export function citationDeepLink(
  citation: CitationInput,
  options?: CitationContext,
): string | null;

export const CITATION_JUMP_MODES: {
  readonly OPEN: "open";
  readonly COPY: "copy";
  readonly NONE: "none";
};

export interface JumpPlan {
  readonly mode: "open" | "copy" | "none";
  readonly href: string | null;
}

export function jumpPlan(
  href: string | null | undefined,
  options?: { bridgeAvailable?: boolean },
): JumpPlan;
export function settingsUrl(baseUrl: string | null | undefined): string | null;
/**
 * The library paper behind a reader's item: its PARENT item only.
 *
 * Returns "" when there is no parent (a standalone/attachment item, which the
 * service never indexes as a paper: kbapp/integrations/zotero.py:7,57) or when
 * the library id is not a positive integer. Never guesses a library.
 */
export function paperKeyOf(identity: {
  readonly itemKey?: string;
  readonly libraryId?: number | null;
  readonly parentKey?: string;
  readonly parentLibraryId?: number | null;
}): string;
