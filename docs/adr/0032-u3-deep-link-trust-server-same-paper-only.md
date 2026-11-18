# [U3] 跳页只信服务端深链与「同篇文献」的附件 key,跨文献不猜

- 日期:2026-11-18
- 状态:已接受

## 背景

服务端在证据的 `url` 里**已经**给出
`zotero://open-pdf/library/items/<attachment_key>?page=N`
(`kbapp/library/sources.py:202-204`,`paper_url()`)。

但并非每条引用都带这个链接:`/api/documents/…` 这类链接不带 attachment key。
若用「当前 PDF 的附件 key」去补一条跨文献引用的深链,**猜错的 key 会打开完全
另一篇 PDF**——用户以为在核对证据,实际在看另一篇文章。

## 决策

`citationDeepLink` 只在**两种**证据下产出深链
(`plugin/src/readerPanelCore.mjs:1338`):

1. `url` 本身已是 `zotero://open-pdf/`(页码以证据的 `page` 覆盖);
2. 引用所属 `paper_id` === 当前 PDF 的 `paper_id`,且已知当前附件 key,且页码存在。

`zotero://select` 一律不当作跳页(它只能选中条目,不能跳页);无深链的徽标渲染成
`span` 而非按钮,`href` 为 `null`。

## 备选

- **备选 A:对所有引用都用当前附件 key 拼 open-pdf 深链**。否决:会为跨文献引用
  打开**错误的 PDF**,是一个看起来成功、实际答非所问的失败。
- **备选 B:所有徽标都渲染成按钮,点击无反应时静默**。否决:这正是仓库定义为
  缺陷的「点了没反应」;不可点的东西不应长得像按钮。

## 后果

跨文献命中的徽标可能不可点,但卡片本身仍有页码可读——信息没有丢,只是不假装
能跳。可点性在视图模型里由 `href` 是否为 `null` 决定,可以直接测试断言。

桥接不可用时降级为**复制深链文本**并给可见提示(判定
`jumpPlan()`,`plugin/src/readerPanelCore.mjs:1369-1377`;执行侧
`activateJump()` → `copyLink()`,`plugin/src/readerPanel.ts:918-930`),不静默失败。
