# [U6] 所有写路径提交前统一解析 paper_id,不提交插件侧的 `z:<lib>:<key>`

- 日期:2026-11-18
- 状态:已接受

## 背景

插件只能拿到 Zotero 的 `(libraryID, itemKey)`,因此它按
`kbapp/library/sources.py` 的 identity 规则拼出 `z:<libraryID>:<key>`
(`plugin/src/menus/menuCore.mjs:358`)。但服务端的两个写入口都只用
`store.resolve()` 解析:

- `/api/agent-runs` 在 `create_run()` 里对每个 id 调 `store.resolve()`,再把结果
  与 `store.papers()` 求差,有任一不在库里就抛「论文范围包含不存在的节点」
  (`kbapp/workflows/agents.py:83-86`);
- `/api/documents/process` 用 `store.papers().get(store.resolve(...))`,解析不到
  就落到「请选择库内文献」(`kbapp/web/document_api.py:130-136`)。

而 `store.resolve()` 先按 DOI 归一化、再查 `aliases` 表
(`kbapp/storage/store.py:155-167`)。`z:` 形式**只有在文献 id 本身就是这个字符串**
时才会命中别名;历史遗留条目保留 `doi:` 之类 id 时,`z:` 形式解析回自身,整条
请求 400。更糟的是错误归因:400 被上层报成「服务未确认」,用户既不知道要先把
文献入库,也不知道该动哪一篇。

## 决策

在 Zotero 边缘(`plugin/src/menus/menus.ts:410-431`)新增 `resolvePaperId()`:以
`GET /api/v2/paper/sections?paper=<id>` 的 `paper_id` 作为解析结果,它内部走的
正是 `kbapp/research/panel.py:93` 的 `resolve()`,即「id → 库内文献」的权威解析
(含 `zotero_key + library_id` 兜底)。

对比与解析两条写命令共用这一个两跳前置:

- **对比**(`runCompareRun`,`menus.ts:433-487`):逐篇解析,任一篇失败即整次
  拒绝,提交体用解析后的 id(`compareRunBody()` 负责去重与 2–4 守卫,
  `menuCore.mjs:483`)。不提交部分可用的集合,否则 400 会盖掉本可用的那几篇。
- **解析**(`runParse`,`menus.ts:507-541`):单篇解析失败直接返回,提交体用解析
  后的 `paperId`。

失败按状态分流:404 表示「未入库」,给可执行的动作(先入库);其它状态是服务
自己的错误,把 `detail` 原样透出,不再叫「未确认」。纯函数
`resolutionOutcome()`(`menuCore.mjs:523`)进 node 测试并登记 ci.yml。

## 备选

- **备选 A:继续直接提交 `z:<lib>:<key>`**。否决:对 id 为 `doi:` 的历史条目与
  未入库文献都是 400,且错误无法归因——这正是本条要修的问题。
- **备选 B:只把 400 的 `detail` 透出、不在提交前解析**。否决:用户拿到的是
  「不存在的节点」,拿不到可执行动作(应先入库);而且整次对比会因为其中一篇
  未入库而全灭。
- **备选 C:在服务端为 `z:` 形式补上别名映射**。否决:这属于服务契约改动,超出
  U6 的范围,且需要另立 issue 单独设计与回归;插件侧解析不需要改任何一个已发布
  的端点。

## 后果

两条写命令各多一次只读 GET。换来的是:错误说明可执行(未入库 vs 服务错误分得
开),以及不会因为单篇未入库就丢掉整次对比。代价是提交前多一轮串行请求;真机
需确认服务在线时 `/api/v2/paper/sections` 的响应时延可接受,尤其是对比集满 4 篇
时的一次性前置开销。
