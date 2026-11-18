/**
 * 阅读器「证据问答」面板原型：纯状态机 + 视图模型。
 *
 * 刻意写成不带 TypeScript、不带 DOM、不带 Zotero 全局的纯 ESM，
 * 这样 tests/test_plugin_reader.cjs 能用 `node --test` 直接跑真实分支，
 * 不需要 npm install，也不需要 Zotero 进程。src/readerPanel.ts 只负责
 * 把这里的视图模型画成 DOM 并挂到宿主容器上。
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
 */

/** 卡片种类。 */
export const CARD_KINDS = Object.freeze({
  QUESTION: "question",
  ANSWER: "answer",
  GAP: "gap",
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
  "reader-panel-demo-note",
  "reader-panel-placeholder",
  "reader-panel-ask",
  "reader-panel-example",
  "reader-panel-wait",
  "reader-panel-empty",
  "reader-panel-stage-searching",
  "reader-panel-stage-verifying",
  "reader-panel-gap",
  "reader-panel-gap-hint",
  "reader-panel-evidence",
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

/** 把一条引用规范成视图模型：作者年份 + 页码。 */
function normalizeCitation(citation) {
  const authors = Array.isArray(citation.authors)
    ? citation.authors.map((name) => String(name).trim()).filter(Boolean)
    : String(citation.authors ?? "")
        .split(/[,、;]|\band\b/)
        .map((name) => name.trim())
        .filter(Boolean);
  return Object.freeze({
    authors: Object.freeze(authors),
    year: citation.year === 0 || citation.year ? String(citation.year) : null,
    page: Number.isFinite(citation.page) ? citation.page : null,
    pageEnd:
      Number.isFinite(citation.pageEnd) && citation.pageEnd !== citation.page
        ? citation.pageEnd
        : null,
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
 */
export function citationLabel(citation) {
  const c = normalizeCitation(citation);
  const [first, second] = c.authors;
  const who =
    c.authors.length === 0
      ? "佚名"
      : c.authors.length === 1
        ? first
        : c.authors.length === 2
          ? `${first} & ${second}`
          : `${first} 等`;
  return `${who} ${c.year ?? "n.d."} · ${pageLabel(c)}`;
}

/**
 * 一条结论：正文 + 它引用到的徽标。引用可以有多条。
 * 没有引用的结论在视图模型里依然保留，渲染层会把它当未核结论标出来。
 */
function normalizeConclusion(conclusion) {
  const text = String(conclusion.text ?? "").trim();
  if (!text) {
    throw new Error("littrace: 结论正文不能为空");
  }
  const citations = (conclusion.citations ?? []).map(normalizeCitation);
  return Object.freeze({ text, citations: Object.freeze(citations) });
}

/**
 * 回答到达：追加一张回答卡片（有结论时），再为每条缺口追加一张缺口卡片，
 * 然后收起阶段行。
 *
 * 缺口以**独立卡片**追加而不是回答卡片的字段，是为了让「问了但没答上」
 * 在时间线上留下和结论同等重量的痕迹——收进折叠区就看不见了。
 */
export function answer(state, { conclusions = [], gaps = [], evidence = [] } = {}) {
  const next = [];
  let offset = 1;
  if (conclusions.length > 0) {
    next.push(
      Object.freeze({
        id: cardId(state, offset),
        kind: CARD_KINDS.ANSWER,
        conclusions: Object.freeze(conclusions.map(normalizeConclusion)),
        evidence: Object.freeze(
          evidence.map((item) =>
            Object.freeze({
              quote: String(item.quote ?? "").trim(),
              citation: normalizeCitation(item.citation ?? {}),
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
      labelKey: "reader-panel-stage-searching",
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

/**
 * 原型静态数据。**不请求任何服务、不接模型**：这一轮只验证交互与渲染形态。
 *
 * 返回值与 answer() 的参数同形；ok 为 false 时给出脚本化错误，
 * 用来在真机上看到「失败卡片 + 重试」——原型没有真实失败来源。
 * 触发方式：问题里带 `#fail`。
 */
export function demoOutcome(action, question) {
  const text = String(question ?? "").trim();
  if (text.includes("#fail")) {
    return Object.freeze({
      ok: false,
      error: "本地服务连接中断（原型脚本化失败，用于验收失败卡片与重试）",
    });
  }
  const commonGap = "本轮没有找到可定位页码的原文证据，保留为缺口。";
  if (action === SELECTION_ACTIONS.LINEAGE) {
    return Object.freeze({
      ok: true,
      conclusions: [
        {
          text: "被选段落承接的是 2019 年的构型方案，其后作把「连续体近似」换成了解析分段，适用条件因此收窄到定常流。",
          citations: [
            { authors: ["Zhang"], year: 2019, page: 4 },
            { authors: ["Li", "Wang"], year: 2023, page: 11, pageEnd: 13 },
          ],
        },
      ],
      evidence: [
        {
          quote: "…因此本文把前作的连续体近似替换为分段解析解，以换取定常流下的闭式表达。",
          citation: { authors: ["Li", "Wang"], year: 2023, page: 12 },
        },
      ],
      gaps: ["2019 年方案里「非定常流」的处理方式没有找到原文依据，" + commonGap],
    });
  }
  if (action === SELECTION_ACTIONS.EVIDENCE) {
    return Object.freeze({
      ok: true,
      conclusions: [
        {
          text: "被选段落的主张在库内有 2 条原文证据支持，均来自同一篇全文精读通过的文献。",
          citations: [{ authors: ["Chen", "Zhao", "Xu"], year: 2022, page: 7 }],
        },
        {
          text: "与该主张相反的结论只在摘要级证据里出现过，本轮不作为已核结论。",
          citations: [{ authors: ["Chen"], year: 2022, page: 3 }],
        },
      ],
      evidence: [
        {
          quote: "…在 12 组对比中，本文方法的路径长度平均缩短 8.4%，且碰撞率不高于基线。",
          citation: { authors: ["Chen", "Zhao", "Xu"], year: 2022, page: 7 },
        },
      ],
      gaps: [commonGap],
    });
  }
  return Object.freeze({
    ok: true,
    conclusions: [
      {
        text: "这是一个原型回答：内容为静态假数据，用于验证卡片流、引用徽标、缺口灰块与阶段行。",
        citations: [{ authors: ["Demo"], year: 2024, page: 1 }],
      },
    ],
    evidence: [],
    gaps: [commonGap],
  });
}
