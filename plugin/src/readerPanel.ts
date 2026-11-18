import { config } from "../package.json";
import {
  CARD_KINDS,
  CITATION_JUMP_MODES,
  DEFAULT_CARRIER,
  REQUEST_KINDS,
  SELECTION_BUTTONS,
  SERVICE_ENDPOINTS,
  actionForSource,
  ask,
  answer,
  cardViews,
  carrierPlan,
  cloudDisabled,
  createState,
  evidence,
  evidenceOutcome,
  fail,
  guide,
  isCloudConfigError,
  jumpPlan,
  lineageOutcome,
  outcomeFromRun,
  panelStatus,
  paperKeyOf,
  requestFor,
  retry,
  settingsUrl,
  sourceForAction,
  stage,
  stageForRun,
} from "./readerPanelCore.mjs";
import type {
  CarrierPlan,
  CardView,
  CitationContext,
  PanelState,
  SelectionAction,
} from "./readerPanelCore.mjs";
import { canOpenHostLink, openHostLink } from "./tabs/tabs";
import { t } from "./utils/locale";

/**
 * Reader evidence-Q&A panel (A3 prototype, U3 real data).
 *
 * Two responsibilities, kept apart on purpose:
 *
 *   - `readerPanelCore.mjs` owns every state transition, every view model and
 *     every mapping from a service response to cards. It is plain ESM with no
 *     DOM and no Zotero globals, so the append-only contract, the citation
 *     badges, the gap block, the failure/retry path, the cloud-config guide
 *     and the three request shapes are covered by `tests/test_plugin_reader.cjs`.
 *   - this file owns the DOM and the Zotero reader wiring. It never decides
 *     what a state *is*; it asks the core for a view model and paints it.
 *
 * ── Carrier mechanism: the switch point ─────────────────────────────────
 *
 * The old zotero-plugin-toolkit `ReaderTabPanel` / `registerReaderTabPanel`
 * has no counterpart in toolkit v6 or in Zotero's `xpcom/reader.js`; the
 * official API documents only the eight `Zotero.Reader` events. Which shape
 * actually hosts this panel is therefore decided on a real machine (M0
 * acceptance item ⑨), and `CARRIER` below is the single switch point:
 *
 *   ① `toolbar-window`    — `renderToolbar` injects a toolbar button that
 *                           opens the panel in its own window. Widest layout;
 *                           needs a separately packaged window document.
 *   ② `sidebar-container` — the panel container is appended into the reader
 *                           document and styled as a right-hand column.
 *                           Default here: it needs only documented reader
 *                           events plus `append()`.
 *   ③ `item-pane-section` — degrade to an item-pane section through
 *                           `ItemPaneManager.registerSection`. Narrowest
 *                           container; the card stream goes compact.
 *
 * Only this constant and `appendPanelHost()` below change when the
 * acceptance run picks a carrier; the state machine, the card stream and the
 * selection buttons do not. The acceptance ADR records the outcome.
 *
 * ── Data source (U3) ────────────────────────────────────────────────────
 *
 * Every answer now comes from the local service, through the shared S4 client
 * (`addon.service`), which owns the token cache and the request discipline:
 *
 *   - panel question  → POST /api/agent-runs (kind "answer") + poll
 *                       GET /api/agent-runs/{id} for stage + result;
 *   - 「查证据」      → GET /api/v2/search (keyword first, vectors reported);
 *   - 「问谱系」      → GET /api/graph?paper=… (read-only lineage endpoint).
 *
 * Cloud Q&A is only needed by the first path: the panel asks
 * GET /api/status first and, when `cloud_enabled` is false, appends the guide
 * card without creating a doomed run. Selecting text does not consume the
 * user's input unless the question actually started.
 */

/** THE switch point. Change this one value, not the code around it. */
const CARRIER: string = DEFAULT_CARRIER;

/** Class prefix for everything this file injects, so cleanup is exact. */
const ROOT_ID = "littrace-reader-panel";
const STYLE_ID = "littrace-reader-panel-style";

/**
 * How often the answer run is polled. The service writes stage progress into
 * the run row (kbapp/workflows/agents.py:231-239), so this interval is the
 * resolution of the single progress row, not a retry budget: every tick is one
 * GET through the S4 client and no tick is repeated on failure.
 */
const POLL_INTERVAL_MS = 1200;

/**
 * Panel scaffolding styles. Deliberately minimal and system-coloured so the
 * prototype follows the host instead of shipping its own theme; the real
 * design system stays inside the littrace iframes.
 */
const STYLE = `
#${ROOT_ID} {
  --lt-line: #d8d8dc;
  --lt-muted: #6b6b73;
  position: fixed;
  right: 12px;
  bottom: 12px;
  z-index: 2147483000;
  display: flex;
  flex-direction: column;
  max-height: 70vh;
  border: 1px solid #b9b9c0;
  border-radius: 6px;
  background: #fff;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
  font: menu;
  color: inherit;
}
#${ROOT_ID}[data-layout="wide"] { width: min(720px, 92vw); }
#${ROOT_ID}[data-layout="sidebar"] { width: min(360px, 40vw); }
#${ROOT_ID}[data-layout="compact"] {
  width: 100%; right: 0; bottom: 0; max-height: 50vh;
  border-radius: 0; box-shadow: none;
}
#${ROOT_ID} header {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 10px; border-bottom: 1px solid var(--lt-line);
}
#${ROOT_ID} header h2 { margin: 0; font-size: 1em; font-weight: 600; }
#${ROOT_ID} .lt-note { color: var(--lt-muted); font-size: 0.85em; }
#${ROOT_ID} .lt-close { margin-left: auto; }
#${ROOT_ID} .lt-stream {
  overflow-y: auto; padding: 8px 10px;
  display: flex; flex-direction: column; gap: 8px;
}
#${ROOT_ID} .lt-card { border: 1px solid var(--lt-line); border-radius: 4px; padding: 6px 8px; }
#${ROOT_ID} .lt-card p { margin: 4px 0; }
#${ROOT_ID} .lt-question { font-weight: 600; }
#${ROOT_ID} .lt-citations { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
#${ROOT_ID} .lt-badge {
  border: 1px solid var(--lt-line); border-radius: 999px;
  padding: 0 6px; font-size: 0.8em; color: var(--lt-muted);
}
/* 有深链的徽标才是按钮；跳不了时保持普通胶囊，不给可点的错觉。 */
#${ROOT_ID} .lt-badge[data-jump] { cursor: pointer; color: #1a4d8f; border-color: #9dbbdd; }
#${ROOT_ID} .lt-badge[data-jump]:hover { background: #eef4fb; }
#${ROOT_ID} .lt-evidence { border-left: 3px solid #c7d6e8; }
#${ROOT_ID} .lt-evidence .lt-quote { margin: 2px 0 4px; }
#${ROOT_ID} .lt-evidence .lt-kind { color: var(--lt-muted); font-size: 0.8em; margin-right: 4px; }
#${ROOT_ID} .lt-guide {
  border-color: #d9c58a; background: #fdf7e3; color: #6b5b1f;
}
#${ROOT_ID} .lt-guide p { margin: 2px 0; font-size: 0.9em; }
#${ROOT_ID} .lt-guide button { margin-top: 4px; }
#${ROOT_ID} .lt-gap { background: #f1f1f3; border-color: #dcdce0; color: var(--lt-muted); }
#${ROOT_ID} .lt-gap p { margin: 2px 0; font-size: 0.9em; }
#${ROOT_ID} .lt-failure { border-color: #d9a3a3; }
#${ROOT_ID} .lt-stage {
  display: flex; align-items: center; gap: 8px;
  font-size: 0.85em; color: var(--lt-muted);
}
#${ROOT_ID} .lt-stage progress { flex: 1; height: 6px; }
#${ROOT_ID} .lt-empty { color: var(--lt-muted); font-size: 0.9em; }
#${ROOT_ID} .lt-notice {
  border: 1px solid #d9c58a; border-radius: 4px;
  background: #fdf7e3; color: #6b5b1f;
  padding: 4px 8px; margin: 0; font-size: 0.85em;
}
#${ROOT_ID} form button:disabled { opacity: 0.6; }
#${ROOT_ID} form {
  display: flex; gap: 6px; padding: 8px 10px;
  border-top: 1px solid var(--lt-line);
}
#${ROOT_ID} form input { flex: 1; }
`;

/** One in-flight request this panel can still cancel (a poller, mostly). */
interface PanelFlight {
  cancel(): void;
}

/**
 * What starting a flight hands back: a liveness check for the response, and a
 * slot for the cancellable request itself. A named type rather than an inline
 * object, so a `function` body stays findable by brace matching in tests.
 */
interface FlightGuard {
  alive(): boolean;
  setCancel(cancel: () => void): void;
}

/**
 * What one reader document needs: its panel root, its state, the in-flight
 * request, the current paper identity and a disposer.
 */
interface PanelMount {
  root: HTMLElement;
  state: PanelState;
  /**
   * Pending request; non-null is exactly "a question is in flight", so the
   * form, the selection buttons and retry all gate on it through canAsk().
   */
  flight: PanelFlight | null;
  /**
   * Request generation. Every start and every teardown bumps it; a callback
   * whose epoch is stale paints nothing. Without this a slow poll answer can
   * land in a panel that was closed and reopened for another paper.
   */
  epoch: number;
  /** The current PDF's paper id (`z:<libraryID>:<itemKey>`), or "". */
  paperId: string;
  /** The current PDF's Zotero attachment key, used only for same-paper jumps. */
  attachmentKey: string;
}

/** Panels are keyed by reader document: several PDFs can be open at once. */
const mounts = new Map<Document, PanelMount>();

function element<K extends keyof HTMLElementTagNameMap>(
  doc: Document,
  tag: K,
  className?: string,
): HTMLElementTagNameMap[K] {
  const node = doc.createElement(tag);
  if (className) {
    node.className = className;
  }
  return node;
}

/**
 * A citation badge. It is a <button> exactly when the citation carries a jump
 * target, and a plain <span> otherwise: a pill that looks clickable but does
 * nothing is the "点了没反应" defect this repository treats as a bug.
 */
function renderBadge(doc: Document, label: string, href: string | null): HTMLElement {
  if (!href) {
    const badge = element(doc, "span", "lt-badge");
    badge.textContent = label;
    return badge;
  }
  const badge = element(doc, "button", "lt-badge");
  badge.type = "button";
  badge.dataset.jump = href;
  badge.textContent = label;
  return badge;
}

/**
 * Paint one card from its view model. Every decision about *what* the card
 * means was already made in the core; this function only maps it to nodes.
 */
function renderCard(doc: Document, view: CardView): HTMLElement {
  const card = element(doc, "article", "lt-card");
  card.dataset.cardId = view.id;
  card.dataset.kind = view.kind;

  switch (view.kind) {
    case CARD_KINDS.QUESTION: {
      card.classList.add("lt-question");
      // A retried question keeps the original failure where it happened; the
      // retry marker is the only trace that history was not rewritten.
      if (view.retryOf) {
        card.dataset.retryOf = view.retryOf;
      }
      card.textContent = view.text ?? "";
      return card;
    }
    case CARD_KINDS.ANSWER: {
      for (const conclusion of view.conclusions ?? []) {
        const line = element(doc, "p");
        line.textContent = conclusion.text;
        card.append(line);
        if (conclusion.citations.length > 0) {
          const badges = element(doc, "div", "lt-citations");
          for (const citation of conclusion.citations) {
            badges.append(renderBadge(doc, citation.label, citation.href));
          }
          card.append(badges);
        }
      }
      // Evidence stays behind a disclosure: it is long, and the conclusions
      // above must remain what the eye lands on.
      const evidence = view.evidence ?? [];
      if (view.expandable && view.toggle && evidence.length > 0) {
        const details = element(doc, "details");
        const summary = element(doc, "summary");
        summary.textContent = t(view.toggle.labelKey);
        details.append(summary);
        for (const item of evidence) {
          const quote = element(doc, "blockquote");
          quote.textContent = item.quote;
          details.append(quote, renderBadge(doc, item.label, item.href));
        }
        card.append(details);
      }
      return card;
    }
    case CARD_KINDS.EVIDENCE: {
      // A search hit: the page is the point, so the badge sits right under
      // the quote and carries the jump. No disclosure — the quote IS the card.
      card.classList.add("lt-evidence");
      if (view.kindKey) {
        const kind = element(doc, "span", "lt-kind");
        kind.textContent = t(view.kindKey);
        card.append(kind);
      }
      const quote = element(doc, "blockquote", "lt-quote");
      quote.textContent = view.quote ?? "";
      card.append(quote);
      const badges = element(doc, "div", "lt-citations");
      badges.append(renderBadge(doc, view.label ?? "", view.href ?? null));
      card.append(badges);
      return card;
    }
    case CARD_KINDS.GAP: {
      // A gap block is NOT a disclosure. `view.expandable` is false by
      // contract and the core offers no toggle, so no <details> is created.
      card.classList.add("lt-gap");
      const title = element(doc, "strong");
      title.textContent = t("reader-panel-gap");
      const reason = element(doc, "p");
      reason.textContent = view.reason ?? "";
      const hint = element(doc, "p", "lt-note");
      hint.textContent = t(view.hintKey ?? "reader-panel-gap-hint");
      card.append(title, reason, hint);
      return card;
    }
    case CARD_KINDS.GUIDE: {
      // Missing configuration is not a failure to retry: the card names what
      // is missing and offers exactly one action (open the settings page).
      card.classList.add("lt-guide");
      const title = element(doc, "strong");
      title.textContent = t(view.messageKey ?? "reader-panel-guide");
      const hint = element(doc, "p");
      hint.textContent = t(view.hintKey ?? "reader-panel-guide-cloud");
      card.append(title, hint);
      if (view.message) {
        const detail = element(doc, "p", "lt-note");
        detail.textContent = view.message;
        card.append(detail);
      }
      if (view.action) {
        const button = element(doc, "button");
        button.type = "button";
        button.dataset.settings = "1";
        button.textContent = t(view.action.labelKey);
        card.append(button);
      }
      return card;
    }
    case CARD_KINDS.FAILURE: {
      card.classList.add("lt-failure");
      const title = element(doc, "strong");
      title.textContent = t("reader-panel-failure");
      const message = element(doc, "p");
      message.textContent = view.message ?? "";
      card.append(title, message);
      // `retry` is present on every failure card; a card without one would be
      // a dead end, which is why the core never produces it.
      if (view.retry) {
        const button = element(doc, "button");
        button.type = "button";
        button.dataset.retry = view.id;
        button.textContent = t(view.retry.labelKey);
        card.append(button);
      }
      return card;
    }
    default:
      throw new Error(`littrace: 未知卡片视图 ${String(view.kind)}`);
  }
}

/**
 * One progress row. Stage events replace this row; they never append a card,
 * which is what keeps the stream append-only while progress keeps moving.
 */
function renderStage(doc: Document, state: PanelState): HTMLElement {
  const row = element(doc, "div", "lt-stage");
  row.setAttribute("role", "status");
  row.setAttribute("aria-label", t(state.stage?.labelKey ?? "reader-panel-title"));
  const label = element(doc, "span");
  label.textContent = t(state.stage?.labelKey ?? "reader-panel-title");
  const bar = doc.createElement("progress");
  bar.max = 100;
  bar.value = state.stage?.progress ?? 0;
  row.append(label, bar);
  return row;
}

/** The zero-card hint, so an untouched panel is not a blank rectangle. */
function renderEmpty(doc: Document): HTMLElement {
  const hint = element(doc, "p", "lt-empty");
  hint.textContent = t("reader-panel-empty");
  return hint;
}

/**
 * Full repaint of the stream.
 *
 * Card nodes are keyed by id and reused, so a repaint never recreates a card
 * and `<details open>` survives. The stream's child list is REPLACED rather
 * than appended to: the progress row and the empty hint carry no card id, so
 * an append-only rebuild leaves the previous progress row (and the first-run
 * "no question yet" hint) in the stream forever -- a stale 35% bar sitting
 * under every finished answer, one more per question.
 */
function paintStream(doc: Document, mount: PanelMount): void {
  const stream = mount.root.querySelector<HTMLElement>(".lt-stream");
  if (!stream) {
    return;
  }
  const views = cardViews(mount.state);

  // Reuse live card nodes by id. Anything else in the stream (a previous
  // progress row, the empty hint, a card no longer in the model) is dropped
  // by replaceChildren below instead of being kept around.
  const existing = new Map<string, Element>();
  for (const node of Array.from(stream.querySelectorAll("[data-card-id]"))) {
    const id = (node as HTMLElement).dataset.cardId;
    if (id) {
      existing.set(id, node);
    }
  }

  const ordered: Element[] = views.map(
    (view) => existing.get(view.id) ?? renderCard(doc, view),
  );

  const status = panelStatus(mount.state);
  if (status.kind === "busy") {
    ordered.push(renderStage(doc, mount.state));
  } else if (status.kind === "empty") {
    ordered.push(renderEmpty(doc));
  }

  // The "previous question is still running" notice is transient state, not a
  // card, but it must survive the repaint that follows it, or the user never
  // sees why their submission was refused.
  const notice = stream.querySelector(".lt-notice");
  if (notice) {
    ordered.push(notice);
  }

  // Moving an existing node keeps its state, so reusing card objects and
  // replacing the child list keeps `<details>` open and closed as they were.
  const scroll = stream.scrollTop;
  stream.replaceChildren(...ordered);
  stream.scrollTop = scroll;
}

/**
 * Apply one finished request to the stream.
 *
 * `apply` builds the new state; it runs AFTER the in-flight marker is cleared.
 * The mappers can throw on a malformed response, and if that happened with the
 * marker still set, every later question and retry would be refused forever
 * by canAsk(). A throw becomes a failure card with a retry instead.
 */
function settle(doc: Document, mount: PanelMount, apply: () => void): void {
  // Clear the in-flight marker FIRST, then build the outcome.
  mount.flight = null;
  try {
    apply();
  } catch (error) {
    report(error, "reader panel answer");
    try {
      mount.state = fail(mount.state, {
        message: "本轮回答构造失败,已保留问题,可重试",
      });
    } catch (nested) {
      report(nested, "reader panel failure card");
    }
  }
  syncFormState(mount);
  paintStream(doc, mount);
}

/** Report a contract violation to the Zotero console instead of freezing. */
function report(error: unknown, context: string): void {
  Zotero.logError(
    error instanceof Error
      ? new Error(`littrace: ${context}: ${error.message}`)
      : new Error(`littrace: ${context}: ${String(error)}`),
  );
}

/** The user-facing text of any thrown value, with a printable fallback. */
function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }
  const text = String(error ?? "").trim();
  return text || "请求本地服务失败，未拿到可用响应";
}

/** The citation context handed to the core: same paper, same attachment. */
function citationContext(mount: PanelMount): CitationContext {
  return { paperId: mount.paperId, attachmentKey: mount.attachmentKey };
}

/**
 * Claim the in-flight slot and give the caller an epoch to check.
 *
 * Called BEFORE the question card and the progress row are painted, so
 * `canAsk()` is already false when `syncFormState()` runs: the submit button is
 * disabled and the placeholder says why for the whole request, not only after
 * the first await. (The A3 ordering — paint, then start — was invisible when a
 * "request" was a 350 ms timer; with a real run it would leave the button live
 * for the entire answer.)
 *
 * `alive()` is false as soon as any newer flight starts or the panel is torn
 * down, so a slow response for a question asked in another PDF can never paint
 * into this stream. `setCancel` stores the poller's cancel so disposal stops
 * the request instead of leaking it.
 */
function beginFlight(mount: PanelMount): FlightGuard {
  mount.epoch += 1;
  const epoch = mount.epoch;
  const flight: PanelFlight = { cancel() {} };
  mount.flight = flight;
  return {
    alive: () => mount.epoch === epoch,
    setCancel: (cancel) => {
      flight.cancel = cancel;
    },
  };
}

/**
 * The single dispatch for the three entries: ask, evidence, lineage.
 *
 * The caller has already claimed the in-flight slot (see run()), so a request
 * started here is visible on the form from the moment it exists.
 */
function dispatch(
  doc: Document,
  mount: PanelMount,
  request: ReturnType<typeof requestFor>,
  flight: FlightGuard,
): void {
  if (request.kind === REQUEST_KINDS.BLOCKED) {
    // Not retryable config: the question itself cannot be formed (no paper
    // identity for the lineage endpoint). Say so on a failure card, which
    // keeps the retry entry — once the paper is indexed it will work.
    // Nothing was sent, so no request is in flight; settle() clears the slot.
    settle(doc, mount, () => {
      mount.state = fail(mount.state, { message: request.message });
    });
    return;
  }
  if (request.kind === REQUEST_KINDS.READ) {
    void runRead(doc, mount, request, flight);
    return;
  }
  void runAnswer(doc, mount, request, flight);
}

/**
 * The Q&A path: check the cloud configuration, submit one answer run, then
 * poll it while the single progress row advances.
 *
 * The pre-flight `GET /api/status` is what turns "cloud Q&A is not configured"
 * into a guide card BEFORE a run is created: submitting anyway would start a
 * task that can only fail, and the user would get a retry button for a
 * configuration problem.
 */
async function runAnswer(
  doc: Document,
  mount: PanelMount,
  request: Extract<ReturnType<typeof requestFor>, { kind: "run" }>,
  flight: FlightGuard,
): Promise<void> {
  try {
    const status = await addon.service.get(SERVICE_ENDPOINTS.STATUS);
    if (!flight.alive()) {
      return;
    }
    if (cloudDisabled(status)) {
      settle(doc, mount, () => {
        mount.state = guide(mount.state);
      });
      return;
    }
    const submitted = await addon.service.submitTask(request.path, request.body);
    if (!flight.alive()) {
      return;
    }
    if (submitted.outcome !== "queued" || !submitted.taskId) {
      // 2xx without a task id: the service accepted the request without
      // saying what happened (S4 ADR 0020). Never claim success.
      throw new Error("服务未确认问答任务，请稍后重试");
    }
    let progress = mount.state.stage?.progress ?? 0;
    const poller = addon.service.pollTask({
      taskPath: `${SERVICE_ENDPOINTS.ASK}/${encodeURIComponent(submitted.taskId)}`,
      intervalMs: POLL_INTERVAL_MS,
      onUpdate: (snapshot: unknown) => {
        if (!flight.alive()) {
          return;
        }
        const next = stageForRun(snapshot, progress);
        if (!next) {
          return;
        }
        progress = next.progress;
        mount.state = stage(mount.state, next);
        paintStream(doc, mount);
      },
    });
    flight.setCancel(() => poller.cancel());
    const result = await poller.done;
    if (!flight.alive() || result.state === "cancelled") {
      return;
    }
    const outcome = outcomeFromRun(result.snapshot);
    if (!outcome) {
      // Only active states can produce null, and the poller stops on terminal
      // ones; reaching here means the terminal set and this set disagree.
      throw new Error("问答任务停在非终态，请重试");
    }
    settle(doc, mount, () => {
      if (outcome.ok) {
        mount.state = answer(mount.state, outcome, citationContext(mount));
      } else if (outcome.kind === "guide") {
        mount.state = guide(mount.state, { message: outcome.message });
      } else {
        mount.state = fail(mount.state, { message: outcome.message });
      }
    });
  } catch (error) {
    if (!flight.alive()) {
      return;
    }
    settle(doc, mount, () => {
      const message = errorMessage(error);
      // The pre-flight check can be bypassed by a concurrent settings change;
      // the service's own wording then routes to the same guide card.
      mount.state = isCloudConfigError(message)
        ? guide(mount.state, { message })
        : fail(mount.state, { message });
    });
  }
}

/** The two read-only paths: search hits and the lineage neighbourhood. */
async function runRead(
  doc: Document,
  mount: PanelMount,
  request: Extract<ReturnType<typeof requestFor>, { kind: "read" }>,
  flight: FlightGuard,
): Promise<void> {
  try {
    const payload = await addon.service.get(request.path);
    if (!flight.alive()) {
      return;
    }
    settle(doc, mount, () => {
      if (request.shape === "lineage") {
        mount.state = answer(
          mount.state,
          lineageOutcome(payload, { paperId: mount.paperId }),
          citationContext(mount),
        );
      } else {
        mount.state = evidence(
          mount.state,
          evidenceOutcome(payload),
          citationContext(mount),
        );
      }
    });
  } catch (error) {
    if (!flight.alive()) {
      return;
    }
    settle(doc, mount, () => {
      const message = errorMessage(error);
      mount.state = isCloudConfigError(message)
        ? guide(mount.state, { message })
        : fail(mount.state, { message });
    });
  }
}

/**
 * Ask one question and advance the stream. The panel form and both selection
 * buttons come through here, so there is exactly one flow to reason about.
 *
 * Returns false when the question was NOT started — either one is already in
 * flight or the question cannot form a request. The caller must then leave the
 * user's input where it is: clearing it and returning silently is the "input
 * vanishes with no feedback" bug this return value exists to prevent.
 */
function run(
  doc: Document,
  mount: PanelMount,
  question: string,
  action: SelectionAction | null,
): boolean {
  if (!canAsk(mount)) {
    showNotice(mount, "reader-panel-wait");
    return false;
  }
  // Build the request BEFORE touching the stream: a question that cannot form
  // a request must not leave a question card with a progress row that never
  // finishes.
  let request: ReturnType<typeof requestFor>;
  try {
    request = requestFor(action, question, { paperId: mount.paperId });
  } catch (error) {
    report(error, "reader panel question");
    showMessage(mount, errorMessage(error));
    return false;
  }
  // Claim the in-flight slot BEFORE painting: syncFormState() reads canAsk(),
  // and a request is only really "in flight" once this is set. Painting first
  // would leave the submit button live until the first await resolves.
  let flight: FlightGuard;
  try {
    clearNotice(mount);
    flight = beginFlight(mount);
    mount.state = ask(mount.state, question, {
      source: sourceForAction(action),
    });
    mount.state = stage(mount.state, {
      labelKey: "reader-panel-stage-queued",
      progress: 8,
    });
    syncFormState(mount);
    paintStream(doc, mount);
  } catch (error) {
    report(error, "reader panel question");
    // A failed paint must not leave the panel permanently busy.
    mount.flight = null;
    syncFormState(mount);
    paintStream(doc, mount);
    return false;
  }
  dispatch(doc, mount, request, flight);
  return true;
}

/**
 * Whether a new question may start. One question in flight at a time: two
 * concurrent streams would interleave and the append-only contract would stop
 * meaning anything. Callers must ASK this before consuming the user's input.
 */
function canAsk(mount: PanelMount): boolean {
  return mount.flight === null;
}

/**
 * A short visible notice for a question that could not be started.
 *
 * The repository rule is that a disabled entry says why and never fails
 * quietly; refusing a second question while one is running is exactly such a
 * state, so it gets a line in the stream rather than a silent return.
 */
function showNotice(mount: PanelMount, messageKey: string): void {
  showMessage(mount, t(messageKey));
}

/** Same notice, with text that is not a Fluent key (an error, a copied link). */
function showMessage(mount: PanelMount, text: string): void {
  const stream = mount.root.querySelector<HTMLElement>(".lt-stream");
  if (!stream) {
    return;
  }
  const value = String(text ?? "").trim();
  if (!value) {
    return;
  }
  const existing = stream.querySelector(".lt-notice");
  if (existing) {
    existing.textContent = value;
    return;
  }
  const notice = element(mount.root.ownerDocument, "p", "lt-notice");
  notice.setAttribute("role", "alert");
  notice.textContent = value;
  stream.append(notice);
}

/** Clear a stale notice: a question that did start makes the old one wrong. */
function clearNotice(mount: PanelMount): void {
  mount.root.querySelector(".lt-notice")?.remove();
}

/**
 * Reflect the in-flight state on the form: while a question is running the
 * submit button is disabled and the input says why. Together with run()'s
 * boolean return and the notice, this is what turns "one at a time" from a
 * silent drop into a visible state.
 */
function syncFormState(mount: PanelMount): void {
  const busy = !canAsk(mount);
  const input = mount.root.querySelector<HTMLInputElement>("form input");
  const submit = mount.root.querySelector<HTMLButtonElement>("form button");
  if (submit) {
    submit.disabled = busy;
  }
  if (input) {
    input.placeholder = busy
      ? t("reader-panel-wait")
      : t("reader-panel-placeholder");
  }
}

/** Retry a failed card: the core appends, the service re-runs the question. */
function retryCard(doc: Document, mount: PanelMount, cardId: string): void {
  if (!canAsk(mount)) {
    showNotice(mount, "reader-panel-wait");
    return;
  }
  let request: ReturnType<typeof requestFor>;
  let flight: FlightGuard;
  try {
    mount.state = retry(mount.state, cardId);
    const fresh = mount.state.cards[mount.state.cards.length - 1];
    if (!fresh || fresh.kind !== CARD_KINDS.QUESTION || !("text" in fresh)) {
      throw new Error(`littrace: 重试没有生成问题卡片 (${cardId})`);
    }
    const action = actionForSource((fresh as { source: string }).source);
    // The retry reuses the original question, including its selection action,
    // so a retried lineage question does not silently become an evidence one.
    request = requestFor(action, fresh.text, { paperId: mount.paperId });
    // Claim the slot before painting, exactly as run() does: retry() already
    // set busy: true in the state, but canAsk() reads the flight, not the
    // state.
    flight = beginFlight(mount);
  } catch (error) {
    report(error, "reader panel retry");
    mount.flight = null;
    showMessage(mount, errorMessage(error));
    syncFormState(mount);
    paintStream(doc, mount);
    return;
  }
  mount.state = stage(mount.state, {
    labelKey: "reader-panel-stage-queued",
    progress: 8,
  });
  syncFormState(mount);
  paintStream(doc, mount);
  dispatch(doc, mount, request, flight);
}

/**
 * Copy a deep link, with a visible fallback when the host cannot open it.
 *
 * Two mechanisms, tried in order, and the notice states what actually
 * happened:
 *   1. `navigator.clipboard.writeText`, awaited — the promise really can
 *      reject (not a user gesture, insecure context), so awaiting is the only
 *      way to know whether the text landed;
 *   2. a temporary textarea + `execCommand("copy")`, which is what privileged
 *      reader documents still support when (1) is unavailable.
 * If both fail the link itself is shown in the notice, so the user can copy it
 * by hand. Never a silent no-op, and never a false "copied".
 */
async function copyLink(doc: Document, mount: PanelMount, href: string): Promise<void> {
  let copied = false;
  try {
    const view = doc.defaultView;
    const clipboard = view?.navigator?.clipboard;
    if (clipboard) {
      await clipboard.writeText(href);
      copied = true;
    }
  } catch (error) {
    // A refused clipboard write is expected on some hosts; the textarea path
    // below is the documented fallback, not an error to surface on its own.
    copied = false;
  }
  if (!copied) {
    try {
      const field = doc.createElement("textarea");
      field.value = href;
      field.setAttribute("readonly", "");
      field.style.position = "fixed";
      field.style.opacity = "0";
      doc.body?.append(field);
      field.select();
      copied = doc.execCommand("copy");
      field.remove();
    } catch (error) {
      report(error, "reader panel copy deep link");
      copied = false;
    }
  }
  showMessage(
    mount,
    copied ? `${t("reader-panel-jump-copied")} ${href}` : href,
  );
}

/**
 * A citation badge click: hand the deep link to the host, or fall back to
 * copying it in plain sight. `jumpPlan` makes that choice from one explicit
 * capability check, shared with the U4 bridge.
 */
function activateJump(doc: Document, mount: PanelMount, href: string): void {
  const plan = jumpPlan(href, { bridgeAvailable: canOpenHostLink() });
  if (plan.mode === CITATION_JUMP_MODES.NONE || !plan.href) {
    return;
  }
  if (plan.mode === CITATION_JUMP_MODES.OPEN && openHostLink(plan.href)) {
    return;
  }
  void copyLink(doc, mount, plan.href);
}

/**
 * The guide card's one action: the workbench settings page, which is where
 * cloud Q&A is configured (kbapp/web/assets/modules/shell.js:17,44). Until the
 * in-plugin settings page ships (U5) this opens the service UI in the browser;
 * the same fallback to copying applies if the host cannot launch a link.
 */
function openSettings(doc: Document, mount: PanelMount): void {
  const url = settingsUrl(addon.service.baseUrl);
  if (!url) {
    showMessage(mount, "服务地址未配置，无法打开设置页");
    return;
  }
  if (openHostLink(url)) {
    return;
  }
  void copyLink(doc, mount, url);
}

/** Build the panel DOM for one reader document and wire its events. */
function createPanel(doc: Document, layout: string): PanelMount {
  const root = element(doc, "section");
  root.id = ROOT_ID;
  root.dataset.layout = layout;
  root.setAttribute("role", "region");
  root.setAttribute("aria-label", t("reader-panel-title"));

  const header = element(doc, "header");
  const title = element(doc, "h2");
  title.textContent = t("reader-panel-title");
  const note = element(doc, "span", "lt-note");
  note.textContent = t("reader-panel-source-note");
  const close = element(doc, "button", "lt-close");
  close.type = "button";
  close.textContent = "×";
  close.title = t("reader-panel-close");
  close.setAttribute("aria-label", t("reader-panel-close"));
  header.append(title, note, close);

  const stream = element(doc, "div", "lt-stream");
  stream.setAttribute("aria-live", "polite");

  const form = element(doc, "form");
  const input = element(doc, "input");
  input.type = "text";
  input.placeholder = t("reader-panel-placeholder");
  input.setAttribute("aria-label", t("reader-panel-placeholder"));
  input.title = t("reader-panel-example");
  const submit = element(doc, "button");
  submit.type = "submit";
  submit.textContent = t("reader-panel-ask");
  form.append(input, submit);

  root.append(header, stream, form);

  const mount: PanelMount = {
    root,
    state: createState(),
    flight: null,
    epoch: 0,
    paperId: "",
    attachmentKey: "",
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) {
      return;
    }
    // Only consume the input when the question actually started. Clearing it
    // first and letting run() refuse would make a mid-flight second question
    // disappear with no card and no message.
    if (run(doc, mount, question, null)) {
      input.value = "";
    }
    input.focus();
  });

  close.addEventListener("click", () => {
    disposeMount(doc);
  });

  // Clicks are delegated: the stream repaints, so per-node listeners would be
  // lost on every paint. Four targets: retry, a citation jump, the guide
  // card's settings action, and nothing else.
  stream.addEventListener("click", (event) => {
    const target = event.target as HTMLElement | null;
    if (!target) {
      return;
    }
    const cardId = target.dataset.retry;
    if (cardId) {
      retryCard(doc, mount, cardId);
      return;
    }
    const href = target.dataset.jump;
    if (href) {
      activateJump(doc, mount, href);
      return;
    }
    if (target.dataset.settings) {
      openSettings(doc, mount);
    }
  });

  return mount;
}

/**
 * Tear down one reader document's panel and cancel its in-flight request.
 *
 * Cancelling matters for correctness, not just tidiness: a poller left running
 * would keep hitting the service for a panel nobody can see, and its callbacks
 * are gated on the epoch, so bumping it here is what makes a late answer from
 * this panel inert.
 */
function disposeMount(doc: Document): void {
  const mount = mounts.get(doc);
  if (!mount) {
    return;
  }
  mounts.delete(doc);
  mount.epoch += 1;
  if (mount.flight) {
    try {
      mount.flight.cancel();
    } catch (error) {
      report(error, "reader panel cancel");
    }
    mount.flight = null;
  }
  mount.root.remove();
}

/**
 * Drop entries whose panel is no longer in a live document.
 *
 * Closing a reader tab destroys its document but fires no event this plugin
 * subscribes to, so without this sweep every closed PDF would keep its
 * Document (and the whole panel DOM under `mount.root`) reachable through
 * `mounts` for the rest of the session. `isConnected` is the check that needs
 * no particular reader event name, which matters while the carrier is still
 * undecided; a reader-closed event can be added on top once the acceptance
 * run pins that event name down.
 */
function pruneDetachedMounts(): void {
  for (const [doc, mount] of Array.from(mounts.entries())) {
    if (mount.root.isConnected) {
      continue;
    }
    mounts.delete(doc);
    mount.epoch += 1;
    if (mount.flight) {
      try {
        mount.flight.cancel();
      } catch (error) {
        report(error, "reader panel cancel");
      }
      mount.flight = null;
    }
    mount.root.remove();
  }
}

/** Inject the stylesheet once per reader document. */
function ensureStyle(doc: Document): void {
  if (doc.getElementById(STYLE_ID)) {
    return;
  }
  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.textContent = STYLE;
  doc.head?.append(style);
}

/**
 * Attach the panel root for the active carrier.
 *
 * Carrier ② (the default) appends into the reader document as a right-hand
 * column. Carriers ① and ③ need a host this prototype does not ship: a
 * separately packaged window document (①) and an `ItemPaneManager`
 * section host (③). They attach to the reader document as a placeholder so
 * the switch is still one edit and the prototype is still visible; the
 * acceptance run replaces exactly this function body.
 */
function appendPanelHost(doc: Document, root: HTMLElement, plan: CarrierPlan): void {
  switch (plan.host) {
    case "reader-document":
      doc.body?.append(root);
      return;
    case "standalone-window":
    case "item-pane-section":
      // TODO(A3 真机定案): replace with the real host before this carrier is
      // declared supported. Keeping the panel visible here is deliberate --
      // a carrier with no render target would make acceptance unobservable.
      doc.body?.append(root);
      return;
    default:
      throw new Error(`littrace: 未知面板宿主 ${String(plan.host)}`);
  }
}

/**
 * The paper and attachment identity behind a reader event.
 *
 * A reader tab is opened for an attachment (usually a PDF) whose parent is the
 * library paper. The service names a library paper `z:<libraryID>:<itemKey>`
 * (kbapp/library/sources.py:34-40), so the panel must ask the parent item; for
 * a standalone PDF the attachment itself is the paper. Everything here is
 * read-only and defensive: a reader whose item cannot be resolved yields an
 * empty key, and the panel then refuses lineage questions with a visible
 * message instead of querying with an empty id.
 */
function identityOfReader(reader: unknown): {
  itemKey: string;
  libraryId: number | null;
  parentKey: string;
  parentLibraryId: number | null;
} {
  const empty = {
    itemKey: "",
    libraryId: null as number | null,
    parentKey: "",
    parentLibraryId: null as number | null,
  };
  const holder = reader as { itemID?: unknown; _item?: unknown } | null;
  if (!holder) {
    return empty;
  }
  let item = holder._item as
    | {
        key?: unknown;
        libraryID?: unknown;
        parentKey?: unknown;
        parentItemKey?: unknown;
      }
    | undefined;
  if (!item && holder.itemID !== undefined) {
    try {
      item = Zotero.Items.get(holder.itemID) ?? undefined;
    } catch (error) {
      report(error, "reader item identity");
      return empty;
    }
  }
  if (!item) {
    return empty;
  }
  const itemKey = typeof item.key === "string" ? item.key.trim() : "";
  const libraryId = Number.isInteger(Number(item.libraryID))
    ? Math.trunc(Number(item.libraryID))
    : null;
  const parentKey =
    typeof item.parentItemKey === "string"
      ? item.parentItemKey.trim()
      : typeof item.parentKey === "string"
        ? item.parentKey.trim()
        : "";
  return {
    itemKey,
    libraryId,
    parentKey,
    // A parent in a different library is not expressible through this API;
    // the service's default (1) applies, which is also what `identity()`
    // assumes for older records.
    parentLibraryId: parentKey ? libraryId : null,
  };
}

/** Open (or focus) the panel for one reader document. */
function openPanel(
  doc: Document,
  plan: CarrierPlan,
  reader?: unknown,
): PanelMount {
  pruneDetachedMounts();
  const existing = mounts.get(doc);
  if (existing) {
    return existing;
  }
  ensureStyle(doc);
  const mount = createPanel(doc, plan.layout);
  const identity = identityOfReader(reader);
  mount.paperId = paperKeyOf(identity);
  mount.attachmentKey = identity.itemKey;
  appendPanelHost(doc, mount.root, plan);
  mounts.set(doc, mount);
  syncFormState(mount);
  paintStream(doc, mount);
  return mount;
}

/**
 * `renderTextSelectionPopup` handler: append the two buttons to the popup.
 *
 * The contract (id / action / labelKey) lives in `SELECTION_BUTTONS` and is
 * asserted by tests/test_plugin_reader.cjs; this function only renders it.
 * Documented event shape: `{ reader, doc, params, append }`, where
 * `params.annotation.text` is the selected text and `append(node)` inserts
 * into the popup.
 */
export function onRenderTextSelectionPopup(event: {
  reader: unknown;
  doc: Document;
  params: { annotation?: { text?: string } };
  append: (node: Node) => void;
}): void {
  const { reader, doc, params, append } = event;
  const plan = carrierPlan(CARRIER);
  const container = element(doc, "div");
  container.id = "littrace-selection-actions";
  container.style.display = "flex";
  container.style.gap = "6px";
  container.style.padding = "2px 0";

  const selection = (params.annotation?.text ?? "").trim();

  for (const button of SELECTION_BUTTONS) {
    const node = element(doc, "button");
    node.type = "button";
    node.id = button.id;
    node.dataset.action = button.action;
    node.textContent = t(button.labelKey);
    node.addEventListener("click", () => {
      const mount = openPanel(doc, plan, reader);
      const input = mount.root.querySelector<HTMLInputElement>("form input");
      if (!selection) {
        // A popup with no text still opens the panel; asking with an empty
        // string would throw inside the core, so only focus the input.
        input?.focus();
        return;
      }
      if (run(doc, mount, selection, button.action)) {
        return;
      }
      // The question could not start this time. The notice already says why;
      // put the selected text in the input so it is not lost, and let the
      // user resubmit once the current question finishes.
      if (input) {
        input.value = selection;
        input.focus();
      }
    });
    container.append(node);
  }

  append(container);
}

/**
 * `renderToolbar` handler for carrier ①, registered unconditionally: the
 * toolbar entry is what makes the standalone-window carrier reachable, and in
 * the sidebar carrier it is simply a second entry into the same panel. The
 * acceptance run decides whether it stays.
 */
export function onRenderToolbar(event: {
  reader: unknown;
  doc: Document;
  params: unknown;
  append: (node: Node) => void;
}): void {
  const { reader, doc, append } = event;
  const plan = carrierPlan(CARRIER);
  const button = doc.createElement("button");
  button.type = "button";
  button.id = "littrace-reader-toolbar-entry";
  button.className = "toolbar-button";
  button.textContent = t("reader-panel-toolbar-entry");
  button.addEventListener("click", () => {
    openPanel(doc, plan, reader);
  });
  append(button);
}

/** Handlers this feature registers, keyed by the documented reader event name. */
export const READER_EVENTS = Object.freeze({
  renderTextSelectionPopup: onRenderTextSelectionPopup,
  renderToolbar: onRenderToolbar,
});

/** Dispose every panel this plugin opened, plus any whose document is gone. */
export function disposeReaderPanels(): void {
  pruneDetachedMounts();
  for (const doc of Array.from(mounts.keys())) {
    try {
      disposeMount(doc);
    } catch (error) {
      report(error, "reader panel dispose");
    }
  }
  mounts.clear();
}

/**
 * Register the reader event listeners with Zotero.
 *
 * `Zotero.Reader.registerEventListener(type, handler, pluginID)` pairs with
 * `unregisterEventListener(type, handler)`; returning the disposer keeps the
 * registry contract in src/registry.ts unchanged.
 */
export function registerReaderEvents(): () => void {
  const pluginID = config.addonID;
  const registered: string[] = [];
  for (const [type, handler] of Object.entries(READER_EVENTS)) {
    try {
      Zotero.Reader.registerEventListener(type, handler, pluginID);
      registered.push(type);
    } catch (error) {
      report(error, `cannot register reader event "${type}"`);
    }
  }
  return () => {
    for (const type of registered.reverse()) {
      try {
        Zotero.Reader.unregisterEventListener(
          type,
          READER_EVENTS[type as keyof typeof READER_EVENTS],
        );
      } catch (error) {
        report(error, `cannot unregister reader event "${type}"`);
      }
    }
    disposeReaderPanels();
  };
}
