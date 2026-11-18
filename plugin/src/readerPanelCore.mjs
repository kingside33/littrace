/**
 * 阅读器「证据问答」面板：纯状态机 + 视图模型 + 真实响应映射。
 *
 * 刻意写成不带 TypeScript、不带 DOM、不带 Zotero 全局的纯 ESM，
 * 这样 tests/test_plugin_reader.cjs 能用 `node --test` 直接跑真实分支，
 * 不需要 npm install，也不需要 Zotero 进程。src/readerPanel.ts 只负责
 * 把这里的视图模型画成 DOM、把服务响应交给这里映射。
 *
 * 两条硬约束（来自功能需求，改动前先读）：
 *
 *   1. **只追加不局部重绘**。卡片流一旦写入就永久保留：任何后续事件只
 *      追加新卡片，绝不修改、删除或重排历史卡片。实现上的保证是每次
 *      返回新数组、旧卡片对象按引用原样复用；卡片对象本身被冻结。
 *      唯一的例外是「当前阶段」——它是**一行**进度条而不是卡片，
 *      由 stage() 原地替换，所以它不进入 cards。
 *
 *   2. **缺口灰块不可展开**。缺口是「这一问没有可核验证据」的诚实表达，
 *      视图模型给出 expandable === false 且 toggle === null；渲染层因此
 *      不产生任何展开入口。
 *
 * U3 起数据来自本地服务：面板提问走 POST /api/agent-runs + 轮询，查证据走
 * GET /api/v2/search，问谱系走 GET /api/graph。三条链路的响应映射都在本文件
 * 下半部分，端点与字段逐个注明服务端出处。
 */

/**
 * 卡片种类。
 *
 * EVIDENCE 与 GUIDE 是 U3 接真数据时新增的两种卡片：
 *   - EVIDENCE：一次「查证据」的每条命中一张卡片，页码是卡片的主角；
 *   - GUIDE：配置缺失时的引导卡片。它与 FAILURE 的区别是**没有重试**——
 *     重试同一个配置错误只会再撞一次，所以引导卡片只说明「缺什么、去哪配」。
 */
export const CARD_KINDS = Object.freeze({
  QUESTION: "question",
  ANSWER: "answer",
  EVIDENCE: "evidence",
  GAP: "gap",
  GUIDE: "guide",
  FAILURE: "failure",
});

/** 划词两个按钮的动作名。 */
export const SELECTION_ACTIONS = Object.freeze({
  EVIDENCE: "evidence",
  LINEAGE: "lineage",
});

/**
 * 划词弹窗按钮契约：renderTextSelectionPopup 里注入的**恰好两个**按钮。
 * id 用于真机验收时定位元素，action 决定走哪条静态数据路径，
 * labelKey 是不带 Fluent 前缀的文案 key（构建时统一加 `littrace-`）。
 */
export const SELECTION_BUTTONS = Object.freeze([
  Object.freeze({
    id: "littrace-selection-evidence",
    action: SELECTION_ACTIONS.EVIDENCE,
    labelKey: "reader-selection-evidence",
  }),
  Object.freeze({
    id: "littrace-selection-lineage",
    action: SELECTION_ACTIONS.LINEAGE,
    labelKey: "reader-selection-lineage",
  }),
]);

/**
 * 面板承载形态的三条退路。旧版 zotero-plugin-toolkit 的 ReaderTabPanel
 * 在 toolkit v6 与 Zotero main 里都没有对应 API，所以挂载点必须可替换：
 * 真机验收（M0 验收项 ⑨）时只改 src/readerPanel.ts 的 CARRIER 一个常量。
 *
 *   ① toolbar-window     reader 工具栏注入按钮（renderToolbar）+ 独立面板
 *   ② sidebar-container  复用 sidebar 注入容器（append），受限宽度、卡片流紧凑
 *   ③ item-pane-section  降级为条目面板 section（ItemPaneManager.registerSection）
 */
export const CARRIERS = Object.freeze([
  "toolbar-window",
  "sidebar-container",
  "item-pane-section",
]);

/**
 * 原型默认形态。选 ② 的理由：它只用官方文档记载的 reader 事件与 append()，
 * 不需要额外打包 XHTML（①的独立窗口）或注册 section + sidenav 图标（③）。
 * 这只是**真机验收的起点**，不是定案结论。
 */
export const DEFAULT_CARRIER = "sidebar-container";

/**
 * 每种承载形态需要宿主提供什么。渲染层（src/readerPanel.ts）只读这张表，
 * 不去猜「现在装在哪」——真机定案后换 CARRIER 一个常量，布局与入口随之
 * 变化，卡片流本身不受影响。
 *
 * layout:
 *   wide     独立窗口，卡片流按宽栏排（每条结论一行引用徽标）
 *   sidebar  右栏容器，宽度受限，卡片流紧凑
 *   compact  条目面板 section，最窄，引用徽标折行、证据默认收起
 */
const CARRIER_PLANS = Object.freeze({
  "toolbar-window": Object.freeze({
    id: "toolbar-window",
    layout: "wide",
    // renderToolbar 注入一个工具栏按钮，点开独立面板窗口。
    entry: "toolbar-button",
    host: "standalone-window",
  }),
  "sidebar-container": Object.freeze({
    id: "sidebar-container",
    layout: "sidebar",
    // 无需额外入口：面板容器直接注入 reader 右栏。
    entry: "none",
    host: "reader-document",
  }),
  "item-pane-section": Object.freeze({
    id: "item-pane-section",
    layout: "compact",
    // 降级为条目面板 section：由 ItemPaneManager.registerSection 提供容器。
    entry: "item-pane-section",
    host: "item-pane-section",
  }),
});

/**
 * 取承载形态的接线方案。未知形态直接抛错而不是回落到某个默认值——
 * 静默降级会让人以为定案生效了，实际画的是另一套布局。
 */
export function carrierPlan(carrier) {
  const plan = CARRIER_PLANS[carrier];
  if (!plan) {
    throw new Error(
      `littrace: 未知面板承载形态 "${carrier}"（可用：${CARRIERS.join(" / ")}）`,
    );
  }
  return plan;
}

/**
 * 本模块与 src/readerPanel.ts 会用到的全部 Fluent 文案 key（不带前缀）。
 * tests/test_plugin_reader.cjs 据此校验两种语言包都定义了它们——漏一条
 * 文案在真机上就是「按钮没有字」，而类型系统看不见这一点。
 */
export const MESSAGE_KEYS = Object.freeze([
  "reader-panel-title",
  "reader-panel-source-note",
  "reader-panel-placeholder",
  "reader-panel-ask",
  "reader-panel-example",
  "reader-panel-wait",
  "reader-panel-empty",
  "reader-panel-stage-queued",
  "reader-panel-stage-searching",
  "reader-panel-stage-writing",
  "reader-panel-stage-verifying",
  "reader-panel-gap",
  "reader-panel-gap-hint",
  "reader-panel-evidence",
  "reader-panel-evidence-title",
  "reader-panel-guide",
  "reader-panel-guide-cloud",
  "reader-panel-guide-settings",
  "reader-panel-jump-copied",
  "reader-panel-failure",
  "reader-panel-retry",
  "reader-panel-toolbar-entry",
  "reader-panel-close",
  "reader-selection-evidence",
  "reader-selection-lineage",
]);

/** 空卡片流用同一个冻结常量，避免每次 createState 都造一个空数组。 */
const EMPTY_CARDS = Object.freeze([]);

/** 划词来源前缀。问题卡片的 source 决定它来自输入框还是哪一个划词按钮。 */
const SELECTION_PREFIX = "selection:";

/**
 * 从划词动作推出来源标记；面板输入框提问传 action = null。
 * 把「来源」写成字符串而不是另一个字段，是为了让重试路径能原样复用同一张
 * 问题卡片的 source，不必再记一份动作映射。
 */
export function sourceForAction(action) {
  return action ? `${SELECTION_PREFIX}${action}` : "panel";
}

/** sourceForAction 的逆运算：未知来源按面板提问处理，不抛错。 */
export function actionForSource(source) {
  const text = String(source ?? "");
  if (!text.startsWith(SELECTION_PREFIX)) {
    return null;
  }
  const action = text.slice(SELECTION_PREFIX.length);
  return Object.values(SELECTION_ACTIONS).includes(action) ? action : null;
}

/**
 * Fluent 消息 id：代码里一律用不带前缀的 key，构建时 scaffold 给 FTL 里的
 * 每条消息加 `littrace-` 前缀，所以运行时查询必须补回前缀。
 * namespace 由调用方传 config.addonRef，这个模块不认识插件配置。
 */
export function messageId(key, namespace) {
  const id = String(key ?? "").trim();
  if (!id) {
    throw new Error("littrace: 消息 key 不能为空");
  }
  const ns = String(namespace ?? "").trim();
  if (!ns || id.startsWith(`${ns}-`)) {
    return id;
  }
  return `${ns}-${id}`;
}

/** 初始状态：没有任何卡片、没有阶段行、没有进行中的请求。 */
export function createState() {
  return { cards: EMPTY_CARDS, stage: null, busy: false };
}

/**
 * 追加卡片。所有写入都经这里，保证「新数组 + 旧卡片按引用复用」这一条
 * 只有一处实现——分散到各事件函数里迟早会有人图省事写 push()。
 */
function appendCards(state, cards) {
  if (cards.length === 0) {
    return state;
  }
  return { ...state, cards: Object.freeze([...state.cards, ...cards]) };
}

/** 卡片编号按「追加后的位置」生成，同一状态序列必然得到同一编号。 */
function cardId(state, offset) {
  return `c${state.cards.length + offset}`;
}

/** 问答框里空白问题直接拒绝：空问题会让进度条永远停在原地。 */
function requireQuestion(question) {
  const text = String(question ?? "").trim();
  if (!text) {
    throw new Error("littrace: 问题不能为空");
  }
  return text;
}

/**
 * 提交一个问题：追加一张问题卡片，并把单行阶段行置为「检索证据 0%」。
 * 历史卡片不动。
 */
export function ask(state, question, { source = "panel" } = {}) {
  const text = requireQuestion(question);
  const card = Object.freeze({
    id: cardId(state, 1),
    kind: CARD_KINDS.QUESTION,
    text,
    source,
    retryOf: null,
  });
  const next = appendCards(state, [card]);
  return {
    ...next,
    busy: true,
    stage: Object.freeze({
      labelKey: "reader-panel-stage-searching",
      progress: 0,
    }),
  };
}

/** 进度百分比夹到 0–100，并接受 NaN（视作 0），避免进度条宽度变成 "NaN%"。 */
function clampProgress(progress) {
  const value = Number(progress);
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.min(100, Math.max(0, Math.round(value)));
}

/**
 * 阶段推进：**替换**那一行进度条，不追加卡片。
 * 这是「追加」规则的唯一例外，也是设计里「阶段事件=单行进度条」的落点。
 */
export function stage(state, { labelKey, progress }) {
  const key = String(labelKey ?? "").trim();
  if (!key) {
    throw new Error("littrace: 阶段行必须有文案 key");
  }
  return {
    ...state,
    stage: Object.freeze({ labelKey: key, progress: clampProgress(progress) }),
  };
}

/**
 * 服务端页码/年份是必填的数值字段，但缺字段、0、字符串数字都出现过：
 * 统一收敛成「正整数或 null」，绝不把 `"3"` 当成 3 之后又显示成 `p.3.0`。
 */
function positiveInt(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

function textOf(value) {
  return typeof value === "string" ? value.trim() : "";
}

function recordOf(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function listOf(value) {
  return Array.isArray(value) ? value : [];
}

/** 标题/短标识的展示上限：徽标是一枚小胶囊，不是一条参考文献。 */
const LABEL_CHARS = 24;

function shorten(text) {
  const value = String(text ?? "").trim();
  return value.length <= LABEL_CHARS ? value : `${value.slice(0, LABEL_CHARS - 1)}…`;
}

/**
 * 把一条引用规范成视图模型：作者年份 + 页码范围 + 跳转深链。
 *
 * 真实数据的引用（kbapp/research/qa.py:128）带的是 title / citekey /
 * page_start / page_end / url，不一定有 authors/year，所以规范化同时保留
 * 两套标识；跳转目标在这里算好并冻结进卡片，历史卡片不会因为后来知道更多
 * 附件信息而改变。
 */
function normalizeCitation(citation, options = {}) {
  const record = recordOf(citation);
  const authors = Array.isArray(record.authors)
    ? record.authors.map((name) => String(name).trim()).filter(Boolean)
    : String(record.authors ?? "")
        .split(/[,、;]|\band\b/)
        .map((name) => name.trim())
        .filter(Boolean);
  const page = positiveInt(record.page);
  const end = positiveInt(record.pageEnd);
  const href = citationDeepLink(
    {
      url: textOf(record.url),
      page,
      paperId: textOf(record.paperId ?? record.paper_id),
    },
    options,
  );
  return Object.freeze({
    authors: Object.freeze(authors),
    year: record.year === 0 || record.year ? String(record.year) : null,
    citekey: textOf(record.citekey),
    title: textOf(record.title),
    paperId: textOf(record.paperId ?? record.paper_id),
    page,
    pageEnd: end !== null && end !== page ? end : null,
    href,
  });
}

/** 页码片段：p.7 / p.7–9 / p.?（页码缺失时如实标问号，不猜）。 */
function pageLabel(citation) {
  if (citation.page === null) {
    return "p.?";
  }
  return citation.pageEnd === null
    ? `p.${citation.page}`
    : `p.${citation.page}–${citation.pageEnd}`;
}

/**
 * 引用徽标文案：「作者 年份 · p.页码」。
 * 三位及以上作者折成「第一作者 等」，与 littrace 正文里的 `等` 用法一致。
 *
 * 真实引用常常没有作者/年份（证据片段只带论文标题与 citekey），此时依次
 * 退到 citekey、截断标题；三者都没有才写「佚名」。年份的补法分两种：
 *   - 有作者但没年份 → 补 `n.d.`，让人看出这是缺失而不是遗忘；
 *   - 用 citekey 兜底时，citekey 本身通常已含年份（`Karaman2011`），
 *     再补一次会写成「Karaman2011 2011」，所以只有 citekey 不含该年份
 *     时才追加。
 */
export function citationLabel(citation) {
  const c = normalizeCitation(citation);
  const [first, second] = c.authors;
  const authorsLabel =
    c.authors.length === 0
      ? ""
      : c.authors.length === 1
        ? first
        : c.authors.length === 2
          ? `${first} & ${second}`
          : `${first} 等`;
  const who = authorsLabel || c.citekey || shorten(c.title) || "佚名";
  let year = "";
  if (c.year) {
    const alreadyInCitekey = !authorsLabel && c.citekey.includes(c.year);
    year = alreadyInCitekey ? "" : c.year;
  } else if (authorsLabel) {
    year = "n.d.";
  }
  return `${[who, year].filter(Boolean).join(" ")} · ${pageLabel(c)}`;
}

/**
 * 一条结论：正文 + 它引用到的徽标。引用可以有多条。
 * 没有引用的结论在视图模型里依然保留，渲染层会把它当未核结论标出来。
 */
function normalizeConclusion(conclusion, options) {
  const text = String(conclusion.text ?? "").trim();
  if (!text) {
    throw new Error("littrace: 结论正文不能为空");
  }
  const citations = (conclusion.citations ?? []).map((citation) =>
    normalizeCitation(citation, options),
  );
  return Object.freeze({ text, citations: Object.freeze(citations) });
}

/**
 * 回答到达：追加一张回答卡片（有结论时），再为每条缺口追加一张缺口卡片，
 * 然后收起阶段行。
 *
 * 缺口以**独立卡片**追加而不是回答卡片的字段，是为了让「问了但没答上」
 * 在时间线上留下和结论同等重量的痕迹——收进折叠区就看不见了。
 *
 * options 只有一项用途：让同一篇文献的引用能补成 open-pdf 深链（见
 * citationDeepLink），所以它是请求参数，不是渲染参数。
 */
export function answer(state, { conclusions = [], gaps = [], evidence = [] } = {}, options = {}) {
  const next = [];
  let offset = 1;
  if (conclusions.length > 0) {
    next.push(
      Object.freeze({
        id: cardId(state, offset),
        kind: CARD_KINDS.ANSWER,
        conclusions: Object.freeze(
          conclusions.map((conclusion) => normalizeConclusion(conclusion, options)),
        ),
        evidence: Object.freeze(
          evidence.map((item) =>
            Object.freeze({
              quote: String(item.quote ?? "").trim(),
              citation: normalizeCitation(item.citation ?? {}, options),
            }),
          ),
        ),
      }),
    );
    offset += 1;
  }
  for (const gap of gaps) {
    const reason = String(gap ?? "").trim();
    if (!reason) {
      continue;
    }
    next.push(
      Object.freeze({
        id: cardId(state, offset),
        kind: CARD_KINDS.GAP,
        reason,
      }),
    );
    offset += 1;
  }
  return { ...appendCards(state, next), busy: false, stage: null };
}

/** 找出某张卡片之前最近的一张问题卡片，用于失败卡片记住「当时问的是什么」。 */
function precedingQuestion(cards, index) {
  for (let i = index - 1; i >= 0; i -= 1) {
    if (cards[i].kind === CARD_KINDS.QUESTION) {
      return cards[i];
    }
  }
  return null;
}

/**
 * 失败：追加一张失败卡片，带上错误文案与重试动作，历史全部保留。
 * 失败绝不删除/替换问题卡片——用户得看得见自己问过什么。
 */
export function fail(state, { message }) {
  const text = String(message ?? "").trim();
  if (!text) {
    throw new Error("littrace: 失败卡片必须有错误文案");
  }
  const question = precedingQuestion(state.cards, state.cards.length);
  const card = Object.freeze({
    id: cardId(state, 1),
    kind: CARD_KINDS.FAILURE,
    message: text,
    questionId: question ? question.id : null,
    retry: Object.freeze({ labelKey: "reader-panel-retry", action: "retry" }),
  });
  return { ...appendCards(state, [card]), busy: false, stage: null };
}

/**
 * 重试某张失败卡片：重新追加一张同样内容的问题卡片（retryOf 指回失败卡片），
 * 失败卡片本身留在原位。只接受失败卡片，传别的 id 说明调用方接线错了。
 */
export function retry(state, failedCardId) {
  const index = state.cards.findIndex((card) => card.id === failedCardId);
  if (index === -1) {
    throw new Error(`littrace: 找不到卡片 ${failedCardId}`);
  }
  const failed = state.cards[index];
  if (failed.kind !== CARD_KINDS.FAILURE) {
    throw new Error(`littrace: 卡片 ${failedCardId} 不是失败卡片，不能重试`);
  }
  const origin =
    state.cards.find((card) => card.id === failed.questionId) ?? null;
  if (!origin) {
    throw new Error(`littrace: 失败卡片 ${failedCardId} 没有对应的问题，无法重试`);
  }
  const card = Object.freeze({
    id: cardId(state, 1),
    kind: CARD_KINDS.QUESTION,
    text: origin.text,
    source: origin.source,
    retryOf: failedCardId,
  });
  const next = appendCards(state, [card]);
  return {
    ...next,
    busy: true,
    stage: Object.freeze({
      // A retry re-submits to the service, so it starts where a first ask does.
      labelKey: "reader-panel-stage-queued",
      progress: 0,
    }),
  };
}

/** 单张卡片的视图模型。渲染层只读它，不再自己判断卡片种类。 */
export function cardView(card) {
  switch (card.kind) {
    case CARD_KINDS.QUESTION:
      return Object.freeze({
        id: card.id,
        kind: card.kind,
        text: card.text,
        // 问题卡片本身没有可展区域。retryOf 让渲染层能标「重试」。
        expandable: false,
        toggle: null,
        retryOf: card.retryOf,
      });
    case CARD_KINDS.ANSWER:
      return Object.freeze({
        id: card.id,
        kind: card.kind,
        conclusions: Object.freeze(
          card.conclusions.map((conclusion) =>
            Object.freeze({
              text: conclusion.text,
              citations: Object.freeze(
                conclusion.citations.map((citation) =>
                  Object.freeze({
                    label: citationLabel(citation),
                    page: citation.page,
                    // 有深链的徽标才是可点的：渲染层只认 href，不再自己拼链接。
                    href: citation.href,
                  }),
                ),
              ),
            }),
          ),
        ),
        evidence: Object.freeze(
          card.evidence.map((item) =>
            Object.freeze({
              quote: item.quote,
              label: citationLabel(item.citation),
              href: item.citation.href,
            }),
          ),
        ),
        // 证据原文可以展开；结论本身始终可见，不需要展开。
        expandable: card.evidence.length > 0,
        toggle:
          card.evidence.length > 0
            ? Object.freeze({ labelKey: "reader-panel-evidence" })
            : null,
      });
    case CARD_KINDS.EVIDENCE:
      // 证据卡片是「查证据」的结果：一段原文 + 一个带页码的引用徽标。
      // 它不做折叠——命中的原文本身就是这张卡片要展示的内容。
      return Object.freeze({
        id: card.id,
        kind: card.kind,
        quote: card.quote,
        label: citationLabel(card.citation),
        page: card.citation.page,
        href: card.citation.href,
        kindKey: card.kindKey,
        expandable: false,
        toggle: null,
      });
    case CARD_KINDS.GUIDE:
      return Object.freeze({
        id: card.id,
        kind: card.kind,
        message: card.message,
        messageKey: card.messageKey,
        hintKey: card.hintKey,
        action: Object.freeze({
          labelKey: card.actionKey,
          action: "open-settings",
        }),
        // 引导卡片**没有重试**：同一个配置错误重试多少次都还是同一个错误。
        retry: null,
        expandable: false,
        toggle: null,
      });
    case CARD_KINDS.GAP:
      return Object.freeze({
        id: card.id,
        kind: card.kind,
        reason: card.reason,
        hintKey: "reader-panel-gap-hint",
        // 缺口灰块**不可展开**：这是契约，不是渲染层的选择。
        expandable: false,
        toggle: null,
      });
    case CARD_KINDS.FAILURE:
      return Object.freeze({
        id: card.id,
        kind: card.kind,
        message: card.message,
        retry: card.retry,
        expandable: false,
        toggle: null,
      });
    default:
      throw new Error(`littrace: 未知卡片种类 ${card.kind}`);
  }
}

/** 整条卡片流的视图模型，顺序与 state.cards 一致。 */
export function cardViews(state) {
  return Object.freeze(state.cards.map(cardView));
}

/** 面板顶部那行状态（空态 / 进行中 / 就绪）。 */
export function panelStatus(state) {
  if (state.busy) {
    return Object.freeze({ kind: "busy", stage: state.stage });
  }
  if (state.cards.length === 0) {
    return Object.freeze({ kind: "empty" });
  }
  return Object.freeze({ kind: "ready" });
}

/* ────────────────────────────────────────────────────────────────────────
 * U3 · 真实数据
 *
 * 下面这些函数把本地服务的真实响应转成 answer() / evidence() / guide()
 * 需要的状态输入。全部是纯函数：不发请求、不碰 DOM，由
 * tests/test_plugin_reader.cjs 用真实响应形状的样例直接执行。
 *
 * 端点与字段逐个核对过服务端实现：
 *   POST /api/agent-runs          kbapp/web/research_api.py:24-27
 *   GET  /api/agent-runs/{id}     kbapp/web/research_api.py:29-32
 *   问答结果 = qa.answer 的输出    kbapp/research/qa.py:147
 *   运行状态词表                   kbapp/workflows/states.py:22-24
 *   GET  /api/status              kbapp/web/system_api.py:40, cloud_enabled 见 workflows/status.py:57
 *   GET  /api/v2/search           kbapp/web/v2_api.py:28-41, SearchHit 见 web/schemas.py:85-102
 *   GET  /api/graph               kbapp/web/graph_api.py:14-16, 边由 research/viewmodels.py:63-82 生成
 * ──────────────────────────────────────────────────────────────────────── */

/** 服务端点。路径只在这里出现，测试直接断言。 */
export const SERVICE_ENDPOINTS = Object.freeze({
  ASK: "/api/agent-runs",
  STATUS: "/api/status",
  SEARCH: "/api/v2/search",
  GRAPH: "/api/graph",
});

/** 问题长度上限，与服务端 create_run 的校验一致（kbapp/workflows/agents.py:63）。 */
export const QUESTION_MAX = 2000;

/** 运行状态（kbapp/workflows/states.py:22-24）。 */
export const RUN_ACTIVE_STATES = Object.freeze(["queued", "running", "stopping"]);
export const RUN_TERMINAL_STATES = Object.freeze([
  "complete",
  "partial",
  "blocked",
  "failed",
  "interrupted",
]);

/** 一次请求的三种去向：启动运行、只读 GET、直接拒绝。 */
export const REQUEST_KINDS = Object.freeze({
  RUN: "run",
  READ: "read",
  BLOCKED: "blocked",
});

/**
 * 未启用云端问答：只看 `GET /api/status` 的 `cloud_enabled`
 * （kbapp/workflows/status.py:57）。字段缺失（旧服务）不算未配置——
 * 把「不知道」当成「没配」会让升级中的用户莫名其妙地提问被拦下。
 */
export function cloudDisabled(status) {
  return recordOf(status).cloud_enabled === false;
}

/**
 * 服务端「云端未启用」文案的兜底识别（kbapp/research/qa.py:59）。
 *
 * 主路径是 cloudDisabled() 的显式字段；预检之后仍可能因为并发改配置而
 * 落到这个文案上，那时把它归到同一种引导卡片，而不是让用户对着「重试」
 * 反复撞同一个配置问题。
 */
export function isCloudConfigError(message) {
  return /启用云端问答/.test(String(message ?? ""));
}

function requestParams(entries) {
  const params = new URLSearchParams();
  for (const [key, value] of entries) {
    const text = value === undefined || value === null ? "" : String(value).trim();
    if (text !== "") {
      params.set(key, text);
    }
  }
  return params.toString();
}

/**
 * 面板提问：POST /api/agent-runs，kind=answer。
 *
 * 与工作台同一条链路（kbapp/workflows/agents.py:58-96）：提交拿到 run id，
 * 再轮询终态。同步的 POST /api/ask 要等完整回答，会超出 S4 客户端
 * 10 秒的请求超时纪律，也拿不到阶段事件。
 */
export function askRunRequest(question, { paperId = "" } = {}) {
  const query = requireQuestion(question);
  if (query.length > QUESTION_MAX) {
    throw new Error(`littrace: 问题不能超过 ${QUESTION_MAX} 字符`);
  }
  const paper = textOf(paperId);
  return {
    kind: REQUEST_KINDS.RUN,
    path: SERVICE_ENDPOINTS.ASK,
    body: { kind: "answer", query, filters: paper ? { paper } : {} },
  };
}

/**
 * 划词「查证据」：GET /api/v2/search。
 *
 * `wait_vectors=false`：等向量会拖到超过请求超时；先拿关键词结果，
 * 服务端在 warning 里说明向量还没就绪，那条 warning 会变成缺口卡片。
 *
 * 与 askRunRequest 一样套 QUESTION_MAX 守卫：`/api/v2/search` 的 `q` 是
 * `Query(min_length=1, max_length=2000)`（kbapp/web/v2_api.py:29），超长划词
 * 必然 422；而 422 的 detail 是数组，S4 客户端的 readDetail 只认字符串
 * （plugin/src/serviceClientCore.mjs:473-483），卡片只会显示「服务返回 422」
 * 并给出一个每次都以同样方式失败的重试。宁可本地拒绝并说清原因。
 * 3000 字中文的 q 还会生成约 27KB 的 URL，也不该发出去。
 */
export function evidenceSearchRequest(selection, { paperId = "", limit = 6 } = {}) {
  const query = requireQuestion(selection);
  if (query.length > QUESTION_MAX) {
    throw new Error(`littrace: 选中的正文不能超过 ${QUESTION_MAX} 字符`);
  }
  const search = requestParams([
    ["q", query],
    ["k", limit],
    ["level", "passage"],
    ["vectors", "true"],
    ["wait_vectors", "false"],
    ["paper", textOf(paperId)],
  ]);
  return {
    kind: REQUEST_KINDS.READ,
    shape: "evidence",
    path: `${SERVICE_ENDPOINTS.SEARCH}?${search}`,
  };
}

/**
 * 划词「问谱系」：GET /api/graph?paper=…（只读）。
 *
 * 这是服务端的谱系端点：graph_page 按文献取回邻域节点与已分级的关系边
 * （kbapp/research/graph.py:57-85），边的 evidence 带 quote/page/url。
 * 没有当前文献标识时直接拒绝——不带 paper 的 /api/graph 会返回全库节点，
 * 那看起来像「答案」，其实是另一回事。
 */
export function lineageRequest(paperId, { limit = 40 } = {}) {
  const paper = textOf(paperId);
  if (!paper) {
    return {
      kind: REQUEST_KINDS.BLOCKED,
      message: "无法识别当前文献，谱系查询需要文献已入库。",
    };
  }
  const graph = requestParams([
    ["paper", paper],
    ["candidates", "false"],
    ["limit", limit],
  ]);
  return {
    kind: REQUEST_KINDS.READ,
    shape: "lineage",
    path: `${SERVICE_ENDPOINTS.GRAPH}?${graph}`,
  };
}

/** 三条入口的唯一分派：面板提问 / 查证据 / 问谱系。 */
export function requestFor(action, text, options = {}) {
  if (action === SELECTION_ACTIONS.EVIDENCE) {
    return evidenceSearchRequest(text, options);
  }
  if (action === SELECTION_ACTIONS.LINEAGE) {
    return lineageRequest(options.paperId, options);
  }
  return askRunRequest(text, options);
}

/**
 * 服务端阶段角色 → 单行进度条文案。角色名来自 kbapp/research/qa.py:72/88/91
 * 的 progress() 调用，经 execute_unit 写进 run.result.role
 * （kbapp/workflows/agents.py:231-239）。
 */
const ROLE_STAGE_KEYS = Object.freeze([
  [/检索|retriever/i, "reader-panel-stage-searching"],
  [/写作|写作者|writer|生成/i, "reader-panel-stage-writing"],
  [/核验|verifier|核实/i, "reader-panel-stage-verifying"],
]);

/** 阶段顺序只决定进度条起点，不决定卡片顺序。 */
const STAGE_ORDER = Object.freeze({
  "reader-panel-stage-queued": 0,
  "reader-panel-stage-searching": 1,
  "reader-panel-stage-writing": 2,
  "reader-panel-stage-verifying": 3,
});

/**
 * 一次轮询快照 → 那一行进度条。仍在执行时返回 {labelKey, progress}，
 * 终态返回 null（终态的收尾由 outcomeFromRun 负责）。
 *
 * 进度是**单调不减**的：调用方传入上一帧的 percent，函数把结果夹在
 * [上一帧, 95] 之间。进度条倒退会让人以为任务被重跑了。
 */
export function stageForRun(run, previousProgress = 0) {
  const snapshot = recordOf(run);
  const status = textOf(snapshot.status);
  if (!RUN_ACTIVE_STATES.includes(status)) {
    return null;
  }
  const result = recordOf(snapshot.result);
  const role = textOf(result.role);
  let labelKey = "reader-panel-stage-searching";
  if (status === "queued" && !role) {
    labelKey = "reader-panel-stage-queued";
  } else {
    const match = ROLE_STAGE_KEYS.find(([pattern]) => pattern.test(role));
    if (match) {
      labelKey = match[1];
    }
  }
  const order = STAGE_ORDER[labelKey] ?? 1;
  let progress = 8 + order * 24;
  const total = Number(snapshot.total);
  const processed = Number(snapshot.processed);
  if (Number.isFinite(total) && total > 0 && Number.isFinite(processed)) {
    progress += Math.round((Math.min(Math.max(processed, 0), total) / total) * 12);
  }
  const floor = Math.min(95, clampProgress(previousProgress));
  return Object.freeze({
    labelKey,
    progress: Math.max(floor, Math.min(95, clampProgress(progress))),
  });
}

const CLAIM_GAP_LIMIT = 3;
const GAP_LIMIT = 8;
const QUOTE_CHARS = 280;

function truncate(text, limit) {
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

/** 服务端 citations[] 的键是 (source_id, quote)，按 source_id 建标题索引。 */
function sourcesById(payload) {
  const map = new Map();
  for (const entry of listOf(payload.citations)) {
    const record = recordOf(entry);
    const id = textOf(record.id) || textOf(record.source_id);
    if (id && !map.has(id)) {
      map.set(id, record);
    }
  }
  return map;
}

/**
 * 一条证据引用 → 引用模型。
 * 摘录自带 page_start/page_end/url（kbapp/research/evidence.py:88），
 * 标题与 citekey 来自同一 source_id 的 citations 条目。
 */
function citationFromRef(ref, byId) {
  const record = recordOf(ref);
  const source = byId.get(textOf(record.source_id)) ?? {};
  return {
    authors: listOf(source.authors).map(String).filter(Boolean),
    year: positiveInt(source.year),
    citekey: textOf(source.citekey),
    title: textOf(source.title),
    page: positiveInt(record.page_start) ?? positiveInt(source.page_start) ?? positiveInt(source.page),
    pageEnd: positiveInt(record.page_end) ?? positiveInt(source.page_end),
    url: textOf(record.url) || textOf(source.url),
    paperId: textOf(record.paper_id) || textOf(source.paper_id),
  };
}

/**
 * qa.answer 的输出 → answer() 的状态输入（kbapp/research/qa.py:147）。
 *
 * 缺口来自 limitations（服务端自己列出的诚实缺口）与至多三条未通过核验的
 * 理由；没有通过核验的结论时，把服务端那句「证据不足以形成结论」放进缺口
 * 卡片，而不是造一条没有证据的结论。
 */
export function answerOutcome(payload) {
  const record = recordOf(payload);
  const byId = sourcesById(record);
  const conclusions = [];
  for (const item of listOf(record.claims)) {
    const claim = recordOf(item);
    const text = textOf(claim.text);
    if (!text) {
      continue;
    }
    conclusions.push({
      text,
      citations: listOf(claim.evidence).map((ref) => citationFromRef(ref, byId)),
    });
  }
  const evidence = [];
  for (const item of listOf(record.citations)) {
    const source = recordOf(item);
    const quote = textOf(source.quote);
    if (!quote) {
      continue;
    }
    evidence.push({
      quote,
      citation: citationFromRef(
        {
          source_id: textOf(source.id),
          page_start: source.page_start,
          page_end: source.page_end,
          url: source.url,
          paper_id: source.paper_id,
        },
        byId,
      ),
    });
  }
  const gaps = [];
  for (const gap of listOf(record.limitations)) {
    const text = textOf(gap);
    if (text) {
      gaps.push(text);
    }
  }
  for (const item of listOf(record.verification_gaps).slice(0, CLAIM_GAP_LIMIT)) {
    const reason = textOf(recordOf(item).reason) || textOf(item);
    if (reason) {
      gaps.push(`未通过核验：${reason}`);
    }
  }
  if (conclusions.length === 0) {
    gaps.unshift(textOf(record.answer) || "本轮没有通过逐项核验的结论。");
  }
  return {
    ok: true,
    conclusions,
    evidence,
    gaps: [...new Set(gaps)].slice(0, GAP_LIMIT),
  };
}

/**
 * 一个 agent run 的终态 → 面板结果。
 *
 *   { ok: true, conclusions, evidence, gaps }  回答到手
 *   { ok: false, kind: "guide", message }      云端问答未启用 → 引导卡片（不重试）
 *   { ok: false, kind: "failure", message }    其他失败 → 错误卡片 + 重试
 *   null                                       仍在执行（queued/running/stopping）
 */
export function outcomeFromRun(run) {
  const snapshot = recordOf(run);
  const status = textOf(snapshot.status);
  if (RUN_ACTIVE_STATES.includes(status)) {
    return null;
  }
  const error = textOf(snapshot.error);
  if (status === "failed" || status === "interrupted" || status === "blocked") {
    if (isCloudConfigError(error)) {
      return { ok: false, kind: "guide", message: error };
    }
    return {
      ok: false,
      kind: "failure",
      message: error || "本轮问答没有完成，服务未给出原因",
    };
  }
  const result = recordOf(snapshot.result);
  if (result.halt_error) {
    const message = textOf(result.message) || error;
    if (isCloudConfigError(message)) {
      return { ok: false, kind: "guide", message };
    }
    return {
      ok: false,
      kind: "failure",
      message: message || "本轮问答已中止",
    };
  }
  if (
    !Array.isArray(result.claims) &&
    !Array.isArray(result.citations) &&
    !Array.isArray(result.limitations)
  ) {
    return {
      ok: false,
      kind: "failure",
      message: error || "服务返回的任务结果不是问答结果",
    };
  }
  return answerOutcome(result);
}

/** 查证据的命中上限：卡片流是给人读的，不是分页列表。 */
export const EVIDENCE_HIT_LIMIT = 5;

/** 语料 kind → 文案 key（与 kbapp/research/panel.py:20-26 的 kind 集合一致）。 */
const EVIDENCE_KIND_KEYS = Object.freeze({
  paper_text: "evidence-kind-paper_text",
  figure: "evidence-kind-figure",
  formula: "evidence-kind-formula",
  metadata: "evidence-kind-metadata",
  external_abstract: "evidence-kind-external_abstract",
});

/**
 * GET /api/v2/search 的 SearchHit[] → 证据卡片输入
 * （kbapp/web/schemas.py:85-102）。一条命中一张卡片，页码来自 page_start。
 */
export function evidenceOutcome(payload) {
  const record = recordOf(payload);
  const rows = listOf(record.results);
  const hits = [];
  for (const item of rows) {
    if (hits.length >= EVIDENCE_HIT_LIMIT) {
      break;
    }
    const row = recordOf(item);
    const quote = textOf(row.snippet) || textOf(row.text);
    if (!quote) {
      continue;
    }
    hits.push({
      quote: truncate(quote, QUOTE_CHARS),
      kindKey: EVIDENCE_KIND_KEYS[textOf(row.kind)] ?? null,
      citation: {
        authors: [],
        year: null,
        citekey: textOf(row.citekey),
        title: textOf(row.title),
        page: positiveInt(row.page_start),
        pageEnd: positiveInt(row.page_end),
        url: textOf(row.url),
        paperId: textOf(row.paper_id),
      },
    });
  }
  const gaps = [];
  if (hits.length === 0) {
    gaps.push("本次检索没有找到可定位页码的原文证据。");
  }
  const warning = textOf(record.warning);
  if (warning) {
    gaps.push(warning);
  }
  if (rows.length > hits.length) {
    gaps.push(`共命中 ${rows.length} 条，只展示前 ${hits.length} 条。`);
  }
  return { ok: true, hits, gaps: [...new Set(gaps)].slice(0, GAP_LIMIT) };
}

/** 一条谱系卡片的边数上限。 */
export const LINEAGE_EDGE_LIMIT = 5;

/** 方法关系优先：谱系问的是方法，引用只是线索。 */
const METHOD_RELATION_KINDS = Object.freeze(["inherits", "improves", "combines", "transfers"]);

/** 关系 kind 的中文名，与 kbapp/research/panel.py:29-35 的口径一致。 */
const RELATION_LABELS = Object.freeze({
  inherits: "方法继承",
  improves: "具体改进",
  combines: "方法融合",
  transfers: "应用迁移",
  cites: "引用",
  compares: "比较",
  similar: "相似",
  parallel: "并行",
  contradicts: "相反",
});

function nodeTitle(node, fallback) {
  return textOf(node.citekey) || textOf(node.title) || fallback;
}

/** 谱系关系的一条证据 → 引用模型（按 paper_id 从 nodes 补标题）。 */
function lineageCitation(ref, nodes) {
  const record = recordOf(ref);
  const paperId = textOf(record.paper_id);
  const node = nodes.get(paperId) ?? {};
  return {
    authors: listOf(node.authors).map(String).filter(Boolean),
    year: positiveInt(node.year),
    citekey: textOf(node.citekey),
    title: textOf(node.title),
    page: positiveInt(record.page) ?? positiveInt(record.page_start),
    pageEnd: positiveInt(record.page_end),
    url: textOf(record.url),
    paperId,
  };
}

/**
 * GET /api/graph?paper=… → answer() 的状态输入。
 *
 * 每条已确认关系一条结论：对方文献 + 关系中文名 + 服务端给的解释/改变/条件
 * + 证据等级（viewmodels.py:63-82）。关系自带的 quote/page/url 变成引用徽标
 * 与证据原文；没有关系时给缺口卡片，并区分「不在谱系里」与「只有引用关系」。
 */
export function lineageOutcome(payload, { paperId = "" } = {}) {
  const record = recordOf(payload);
  const self = textOf(paperId);
  const nodes = new Map();
  for (const item of listOf(record.nodes)) {
    const node = recordOf(item);
    const id = textOf(node.id);
    if (id) {
      nodes.set(id, node);
    }
  }
  const edges = listOf(record.edges)
    .map(recordOf)
    .filter((edge) => textOf(edge.kind));
  const ordered = edges
    .map((edge, index) => ({ edge, index }))
    .sort(
      (a, b) =>
        (METHOD_RELATION_KINDS.includes(a.edge.kind) ? 0 : 1) -
          (METHOD_RELATION_KINDS.includes(b.edge.kind) ? 0 : 1) ||
        a.index - b.index,
    )
    .map((item) => item.edge);
  const conclusions = [];
  const evidence = [];
  for (const edge of ordered.slice(0, LINEAGE_EDGE_LIMIT)) {
    const src = textOf(edge.src);
    const dst = textOf(edge.dst);
    const otherId = src === self ? dst : dst === self ? src : dst || src;
    const other = nodes.get(otherId) ?? {};
    const label = RELATION_LABELS[textOf(edge.kind)] || textOf(edge.kind);
    const parts = [`与《${nodeTitle(other, otherId)}》的关系：${label}`];
    if (textOf(edge.explanation)) {
      parts.push(textOf(edge.explanation));
    }
    if (textOf(edge.change)) {
      parts.push(`具体改变：${textOf(edge.change)}`);
    }
    if (textOf(edge.conditions)) {
      parts.push(`适用条件：${textOf(edge.conditions)}`);
    }
    if (textOf(edge.evidence_label)) {
      parts.push(`证据等级：${textOf(edge.evidence_label)}`);
    }
    const refs = listOf(edge.evidence).map((ref) => recordOf(ref));
    conclusions.push({
      text: parts.join(" · "),
      citations: refs.map((ref) => lineageCitation(ref, nodes)),
    });
    for (const ref of refs) {
      const quote = textOf(ref.quote);
      if (quote) {
        evidence.push({ quote, citation: lineageCitation(ref, nodes) });
      }
    }
  }
  const gaps = [];
  if (edges.length === 0) {
    gaps.push(
      nodes.size === 0
        ? "这篇文献还没有进入方法谱系：先完成解析与索引。"
        : "这篇文献还没有已确认的方法关系。",
    );
  } else {
    if (!edges.some((edge) => METHOD_RELATION_KINDS.includes(textOf(edge.kind)))) {
      gaps.push("本文目前只有引用关系，还没有已确认的方法关系。");
    }
    if (ordered.length > LINEAGE_EDGE_LIMIT) {
      gaps.push(`共 ${ordered.length} 条关系，只展示前 ${LINEAGE_EDGE_LIMIT} 条。`);
    }
  }
  return { ok: true, conclusions, evidence, gaps: [...new Set(gaps)].slice(0, GAP_LIMIT) };
}

/** 把页码写进链接；链接已带 page 参数时以服务端认定的页码为准。 */
export function withPageParam(url, page) {
  const link = textOf(url);
  if (!link || page === null || page === undefined) {
    return link;
  }
  const [base, ...rest] = link.split("#");
  const hash = rest.length > 0 ? `#${rest.join("#")}` : "";
  if (/[?&]page=\d+/.test(base)) {
    return `${base.replace(/([?&])page=\d+/, `$1page=${page}`)}${hash}`;
  }
  const joiner = base.includes("?") ? "&" : "?";
  return `${base}${joiner}page=${page}${hash}`;
}

/**
 * 一条引用能跳到的 zotero 深链；跳不了返回 null（不猜附件 key）。
 *
 * 服务端在 paper_url（kbapp/library/sources.py:202-204）里已经把
 * `zotero://open-pdf/library/items/<attachment_key>?page=N` 放进 citation.url，
 * 所以首选它。只有「同篇文献 + 调用方知道当前 PDF 的附件 key」时，才把
 * `/api/documents/…` 这类非 zotero 链接补成 open-pdf 深链；跨文献的
 * attachment key 猜错会打开另一篇 PDF，所以宁可返回 null。
 */
export function citationDeepLink(citation, options = {}) {
  const record = recordOf(citation);
  const url = textOf(record.url);
  const page = positiveInt(record.page);
  if (/^zotero:\/\/open-pdf\//i.test(url)) {
    return withPageParam(url, page);
  }
  if (/^zotero:\/\//i.test(url)) {
    // zotero://select 只能选中条目，不能跳页；不假装能跳。
    return null;
  }
  const attachmentKey = textOf(options.attachmentKey);
  const owner = textOf(options.paperId);
  const target = textOf(record.paperId) || textOf(record.paper_id);
  if (attachmentKey && owner && target && owner === target && page !== null) {
    return withPageParam(`zotero://open-pdf/library/items/${attachmentKey}`, page);
  }
  return null;
}

/** 点击徽标的两种动作：交给宿主打开，或把深链文本复制出来。 */
export const CITATION_JUMP_MODES = Object.freeze({
  OPEN: "open",
  COPY: "copy",
  NONE: "none",
});

/**
 * 徽标点击的计划。`bridgeAvailable` 必须显式传 true 才走打开——
 * 没确认过的桥接不能假装能跳；其余情况一律降级为复制深链文本。
 */
export function jumpPlan(href, options = {}) {
  const link = textOf(href);
  if (!link) {
    return Object.freeze({ mode: CITATION_JUMP_MODES.NONE, href: null });
  }
  const mode =
    options.bridgeAvailable === true ? CITATION_JUMP_MODES.OPEN : CITATION_JUMP_MODES.COPY;
  return Object.freeze({ mode, href: link });
}

/** 引导卡片「去设置」的目标：工作台设置页（kbapp/web/assets/modules/shell.js:17）。 */
export function settingsUrl(baseUrl) {
  const base = textOf(baseUrl).replace(/\/+$/, "");
  return base ? `${base}/#settings` : null;
}

/**
 * 当前 PDF 的文献标识 `z:<libraryID>:<itemKey>`。
 *
 * 与 kbapp/library/sources.py:34-40 的 identity 同一口径：优先用父条目
 * （PDF 附件挂在论文条目下），独立 PDF 退回附件自身；条目 key 缺失时返回空串，
 * 面板随后按「无法识别当前文献」处理，而不是拿空 key 去查询。
 */
export function paperKeyOf(identity) {
  const record = recordOf(identity);
  const parentKey = textOf(record.parentKey);
  const key = parentKey || textOf(record.itemKey);
  if (!key) {
    return "";
  }
  const raw = parentKey ? (record.parentLibraryId ?? record.libraryId) : record.libraryId;
  const libraryID = Number.isInteger(Number(raw)) && Number(raw) >= 0 ? Math.trunc(Number(raw)) : 1;
  return `z:${libraryID}:${key}`;
}

/**
 * 引导卡片：配置缺失时明确说「现在做不了、去哪做」，并且**不给重试**。
 * 它是卡片，所以同样只追加——用户改完配置回来再问，历史里留着这一张。
 */
export function guide(
  state,
  {
    message = "",
    messageKey = "reader-panel-guide",
    hintKey = "reader-panel-guide-cloud",
    actionKey = "reader-panel-guide-settings",
  } = {},
) {
  const card = Object.freeze({
    id: cardId(state, 1),
    kind: CARD_KINDS.GUIDE,
    message: textOf(message),
    messageKey,
    hintKey,
    actionKey,
  });
  return { ...appendCards(state, [card]), busy: false, stage: null };
}

/**
 * 一次「查证据」的结果：一条命中一张证据卡片，再为缺口追加缺口卡片。
 * 与 answer() 一样只追加，且同样收起阶段行。
 */
export function evidence(state, { hits = [], gaps = [] } = {}, options = {}) {
  const next = [];
  let offset = 1;
  for (const item of hits) {
    const quote = textOf(item?.quote);
    if (!quote) {
      continue;
    }
    next.push(
      Object.freeze({
        id: cardId(state, offset),
        kind: CARD_KINDS.EVIDENCE,
        quote,
        citation: normalizeCitation(item?.citation ?? {}, options),
        kindKey: item?.kindKey ?? null,
      }),
    );
    offset += 1;
  }
  for (const gap of gaps) {
    const reason = textOf(gap);
    if (!reason) {
      continue;
    }
    next.push(
      Object.freeze({
        id: cardId(state, offset),
        kind: CARD_KINDS.GAP,
        reason,
      }),
    );
    offset += 1;
  }
  return { ...appendCards(state, next), busy: false, stage: null };
}
