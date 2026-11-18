/** Types for readerPanelCore.mjs; the implementation is plain ESM on purpose. */

export type CardKind = "question" | "answer" | "gap" | "failure";

export type SelectionAction = "evidence" | "lineage";

export interface SelectionButton {
  readonly id: string;
  readonly action: SelectionAction;
  readonly labelKey: string;
}

export interface Citation {
  readonly authors: readonly string[];
  readonly year: string | null;
  readonly page: number | null;
  readonly pageEnd: number | null;
}

export interface CitationView {
  readonly label: string;
  readonly page: number | null;
}

export interface EvidenceView {
  readonly quote: string;
  readonly label: string;
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

export interface GapCard {
  readonly id: string;
  readonly kind: "gap";
  readonly reason: string;
}

export interface FailureCard {
  readonly id: string;
  readonly kind: "failure";
  readonly message: string;
  readonly questionId: string | null;
  readonly retry: { readonly labelKey: string; readonly action: "retry" };
}

export type PanelCard = QuestionCard | AnswerCard | GapCard | FailureCard;

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
  readonly retry?: { readonly labelKey: string; readonly action: "retry" };
}

export interface DemoOutcome {
  readonly ok: boolean;
  readonly error?: string;
  readonly conclusions?: readonly {
    readonly text: string;
    readonly citations: readonly {
      readonly authors: readonly string[];
      readonly year: number;
      readonly page: number;
      readonly pageEnd?: number;
    }[];
  }[];
  readonly evidence?: readonly {
    readonly quote: string;
    readonly citation: {
      readonly authors: readonly string[];
      readonly year: number;
      readonly page: number;
    };
  }[];
  readonly gaps?: readonly string[];
}

export const CARD_KINDS: {
  readonly QUESTION: "question";
  readonly ANSWER: "answer";
  readonly GAP: "gap";
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
export function answer(
  state: PanelState,
  outcome: {
    conclusions?: readonly {
      text: string;
      citations?: readonly {
        authors?: readonly string[];
        year?: number;
        page?: number;
        pageEnd?: number;
      }[];
    }[];
    gaps?: readonly string[];
    evidence?: readonly {
      quote?: string;
      citation?: {
        authors?: readonly string[];
        year?: number;
        page?: number;
        pageEnd?: number;
      };
    }[];
  },
): PanelState;
export function fail(state: PanelState, failure: { message: string }): PanelState;
export function retry(state: PanelState, cardId: string): PanelState;
export function citationLabel(citation: {
  authors?: readonly string[];
  year?: number;
  page?: number;
  pageEnd?: number;
}): string;
export function cardView(card: PanelCard): CardView;
export function cardViews(state: PanelState): readonly CardView[];
export function panelStatus(
  state: PanelState,
): { readonly kind: "empty" } | { readonly kind: "busy"; readonly stage: Stage | null } | { readonly kind: "ready" };
export function demoOutcome(action: SelectionAction, question: string): DemoOutcome;
