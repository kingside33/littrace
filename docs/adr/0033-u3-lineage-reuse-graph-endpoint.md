# [U3] 「问谱系」走既有 GET /api/graph,无文献标识时拒绝而不是放开范围

- 日期:2026-11-18
- 状态:已接受

## 背景

阅读器划词的「问谱系」需要一个**只读**的谱系端点。既有
`GET /api/graph` 在给定 `paper` 时返回该文献的邻域节点与已分级关系边
(`kbapp/research/graph.py:57-85`);**不带** `paper` 则返回全库节点。

后者是本决策的关键:一个空 `paper` 请求不会报错,它会返回一大堆看起来像「答案」
的节点,实际上是整个库的一份快照——与用户问的那篇文献没有关系。

## 决策

复用 `GET /api/graph?paper=…`(`candidates=false`),**不新增服务端端点**
(`lineageRequest()`,`plugin/src/readerPanelCore.mjs:858`)。

没有当前文献标识时 `lineageRequest` 返回 `blocked`,面板给可见说明,而不是把范围
放开成全库。

方向由插件显式写出(`本篇 → 《X》` / `《X》 → 本篇`),因为服务端以边的 `src`
为主语(`kbapp/research/relations.py:165` 的提示词要求「src必须是主论文id」)。
文献标识只认**父条目**:服务端不入库附件
(`NON_PAPER_TYPES = {'attachment', 'note', 'annotation'}`,
`kbapp/integrations/zotero.py:7,57`)。

## 备选

- **备选 A:新增一个专用谱系端点**。否决:那是服务端改动,要新契约与额外测试,
  属于另一个 PR 的范围;既有端点已经提供了这里需要的全部信息。
- **备选 B:用空 `paper` 查全库**。否决:返回的节点看起来像「答案」,其实是
  另一回事——这是最坏的一类失败,不是报错而是**像成功**。

## 后果

零服务端改动。代价是响应体含本面板不需要的节点字段(`theme`、`total` 等),多传
一些字节。

`blocked` 是可测试的结论:测试断言 `kind === "blocked"` 而不必渲染 UI。
