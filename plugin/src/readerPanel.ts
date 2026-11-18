import { config } from "../package.json";
import {
  CARD_KINDS,
  DEFAULT_CARRIER,
  SELECTION_ACTIONS,
  SELECTION_BUTTONS,
  actionForSource,
  ask,
  answer,
  cardViews,
  carrierPlan,
  createState,
  demoOutcome,
  fail,
  panelStatus,
  retry,
  sourceForAction,
  stage,
} from "./readerPanelCore.mjs";
import type {
  CarrierPlan,
  CardView,
  PanelState,
  SelectionAction,
} from "./readerPanelCore.mjs";
import { t } from "./utils/locale";

/**
 * Reader evidence-Q&A panel prototype (A3).
 *
 * Two responsibilities, kept apart on purpose:
 *
 *   - `readerPanelCore.mjs` owns every state transition and every view model.
 *     It is plain ESM with no DOM and no Zotero globals, so the append-only
 *     contract, the citation badges, the gap block and the failure/retry path
 *     are covered by `tests/test_plugin_reader.cjs`.
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
 * ── Data source ─────────────────────────────────────────────────────────
 *
 * Nothing here talks to the local service. `demoOutcome()` returns static fake
 * data so the interaction shape can be judged before the real data features
 * exist. Every place that assumes fake data is marked `STATIC DATA PATH`;
 * those are the exact lines the real integration replaces.
 */

/** THE switch point. Change this one value, not the code around it. */
const CARRIER: string = DEFAULT_CARRIER;

/** Class prefix for everything this file injects, so cleanup is exact. */
const ROOT_ID = "littrace-reader-panel";
const STYLE_ID = "littrace-reader-panel-style";

/**
 * How long the single progress row stays visible before the static answer
 * arrives. Only this prototype needs it: with fake data an answer is ready in
 * the same tick, and a progress row that never renders proves nothing about
 * the design. Real calls replace this with their own event stream.
 */
const STATIC_STAGE_MS = 350;

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
#${ROOT_ID} .lt-gap { background: #f1f1f3; border-color: #dcdce0; color: var(--lt-muted); }
#${ROOT_ID} .lt-gap p { margin: 2px 0; font-size: 0.9em; }
#${ROOT_ID} .lt-failure { border-color: #d9a3a3; }
#${ROOT_ID} .lt-stage {
  display: flex; align-items: center; gap: 8px;
  font-size: 0.85em; color: var(--lt-muted);
}
#${ROOT_ID} .lt-stage progress { flex: 1; height: 6px; }
#${ROOT_ID} .lt-empty { color: var(--lt-muted); font-size: 0.9em; }
#${ROOT_ID} form {
  display: flex; gap: 6px; padding: 8px 10px;
  border-top: 1px solid var(--lt-line);
}
#${ROOT_ID} form input { flex: 1; }
`;

/** What one reader document needs: its panel root, its state, a disposer. */
interface PanelMount {
  root: HTMLElement;
  state: PanelState;
  /** Pending static-stage timer, cleared on dispose so a closed panel is inert. */
  timer: number | null;
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
            const badge = element(doc, "span", "lt-badge");
            badge.textContent = citation.label;
            // The badge is the jump target once real data lands: the reference
            // builds `zotero://open-pdf/library/items/<key>?page=N`, which
            // needs an attachment key this prototype has no source for, so the
            // badge only carries the page number as data-attrs for now.
            if (citation.page !== null) {
              badge.dataset.page = String(citation.page);
            }
            badges.append(badge);
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
          const badge = element(doc, "span", "lt-badge");
          badge.textContent = item.label;
          details.append(quote, badge);
        }
        card.append(details);
      }
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
    case CARD_KINDS.FAILURE: {
      card.classList.add("lt-failure");
      const title = element(doc, "strong");
      title.textContent = t("reader-panel-failure");
      const message = element(doc, "p");
      message.textContent = view.message ?? "";
      const button = element(doc, "button");
      button.type = "button";
      button.dataset.retry = view.id;
      button.textContent = t(view.retry?.labelKey ?? "reader-panel-retry");
      card.append(title, message, button);
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

/** Full repaint of the stream. Card nodes are keyed by id and reused. */
function paintStream(doc: Document, mount: PanelMount): void {
  const stream = mount.root.querySelector<HTMLElement>(".lt-stream");
  if (!stream) {
    return;
  }
  const views = cardViews(mount.state);
  const wanted = new Set(views.map((view) => view.id));

  // Reuse existing nodes by id so a repaint keeps <details> state; drop only
  // nodes whose card no longer exists (a no-op under the append-only rule).
  const existing = new Map<string, Element>();
  for (const node of Array.from(stream.querySelectorAll("[data-card-id]"))) {
    const id = (node as HTMLElement).dataset.cardId;
    if (id && wanted.has(id)) {
      existing.set(id, node);
    } else {
      node.remove();
    }
  }

  const ordered: Element[] = views.map((view) => {
    const node = existing.get(view.id) ?? renderCard(doc, view);
    return node;
  });

  const status = panelStatus(mount.state);
  if (status.kind === "busy") {
    ordered.push(renderStage(doc, mount.state));
  } else if (status.kind === "empty") {
    ordered.push(renderEmpty(doc));
  }

  // Rebuild order in one pass; nodes are the same objects, so nothing that
  // holds DOM state is recreated.
  for (const node of ordered) {
    stream.append(node);
  }
}

/** Everything one question needs: the question card, the row, then the outcome. */
function settle(
  doc: Document,
  mount: PanelMount,
  action: SelectionAction | null,
  question: string,
): void {
  // STATIC DATA PATH: no service call. `demoOutcome` is the only source of
  // answers in this prototype, and the timer is the only source of progress.
  const outcome = demoOutcome(action ?? SELECTION_ACTIONS.EVIDENCE, question);
  mount.state = outcome.ok
    ? answer(mount.state, outcome)
    : fail(mount.state, { message: outcome.error ?? "" });
  mount.timer = null;
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

/**
 * Ask one question and advance the stream. The panel form and both selection
 * buttons come through here, so there is exactly one flow to reason about.
 */
function run(
  doc: Document,
  mount: PanelMount,
  question: string,
  action: SelectionAction | null,
): void {
  if (mount.timer !== null) {
    // One question in flight at a time: a second submit while the static
    // stage is running would interleave two streams and prove nothing.
    return;
  }
  try {
    mount.state = ask(mount.state, question, {
      source: sourceForAction(action),
    });
    mount.state = stage(mount.state, {
      labelKey: "reader-panel-stage-searching",
      progress: 35,
    });
    paintStream(doc, mount);
    mount.timer = setTimeout(() => {
      settle(doc, mount, action, question);
    }, STATIC_STAGE_MS) as unknown as number;
  } catch (error) {
    report(error, "reader panel question");
    paintStream(doc, mount);
  }
}

/** Retry a failed card: the core appends, the timer re-runs the same question. */
function retryCard(doc: Document, mount: PanelMount, cardId: string): void {
  if (mount.timer !== null) {
    return;
  }
  try {
    mount.state = retry(mount.state, cardId);
    const fresh = mount.state.cards[mount.state.cards.length - 1];
    if (!fresh || fresh.kind !== CARD_KINDS.QUESTION || !("text" in fresh)) {
      throw new Error(`littrace: 重试没有生成问题卡片 (${cardId})`);
    }
    const action = actionForSource((fresh as { source: string }).source);
    const question = fresh.text;
    paintStream(doc, mount);
    mount.timer = setTimeout(() => {
      // Keep the same static path as the original ask, including its action,
      // so a retried lineage question does not silently become an evidence one.
      settle(doc, mount, action, question);
    }, STATIC_STAGE_MS) as unknown as number;
  } catch (error) {
    report(error, "reader panel retry");
    paintStream(doc, mount);
  }
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
  note.textContent = t("reader-panel-demo-note");
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

  const mount: PanelMount = { root, state: createState(), timer: null };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) {
      return;
    }
    input.value = "";
    run(doc, mount, question, null);
  });

  close.addEventListener("click", () => {
    disposeMount(doc);
  });

  // Retry is delegated: the stream repaints, so per-node listeners would be
  // lost on every paint.
  stream.addEventListener("click", (event) => {
    const target = event.target as HTMLElement | null;
    const cardId = target?.dataset.retry;
    if (cardId) {
      retryCard(doc, mount, cardId);
    }
  });

  return mount;
}

/** Tear down one reader document's panel and any pending static-stage timer. */
function disposeMount(doc: Document): void {
  const mount = mounts.get(doc);
  if (!mount) {
    return;
  }
  mounts.delete(doc);
  if (mount.timer !== null) {
    clearTimeout(mount.timer);
    mount.timer = null;
  }
  mount.root.remove();
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

/** Open (or focus) the panel for one reader document. */
function openPanel(doc: Document, plan: CarrierPlan): PanelMount {
  const existing = mounts.get(doc);
  if (existing) {
    return existing;
  }
  ensureStyle(doc);
  const mount = createPanel(doc, plan.layout);
  appendPanelHost(doc, mount.root, plan);
  mounts.set(doc, mount);
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
  const { doc, params, append } = event;
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
      const mount = openPanel(doc, plan);
      // A popup with no text still opens the panel; asking with an empty
      // string would throw inside the core, so only ask when there is text.
      if (selection) {
        run(doc, mount, selection, button.action);
      } else {
        const input = mount.root.querySelector<HTMLInputElement>("form input");
        input?.focus();
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
  const { doc, append } = event;
  const plan = carrierPlan(CARRIER);
  const button = doc.createElement("button");
  button.type = "button";
  button.id = "littrace-reader-toolbar-entry";
  button.className = "toolbar-button";
  button.textContent = t("reader-panel-toolbar-entry");
  button.addEventListener("click", () => {
    openPanel(doc, plan);
  });
  append(button);
}

/** Handlers this feature registers, keyed by the documented reader event name. */
export const READER_EVENTS = Object.freeze({
  renderTextSelectionPopup: onRenderTextSelectionPopup,
  renderToolbar: onRenderToolbar,
});

/** Dispose every panel this plugin opened. */
export function disposeReaderPanels(): void {
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
