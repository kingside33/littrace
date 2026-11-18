# [A2] 徽标旁的数字恒为已确认关系数,与三态徽标解耦

- 日期:2026-11-18
- 状态:已接受

## 背景

评审发现 PR #23 行内评论 4610674118 指出 `lineage.ts:70` 显示的是「与状态一致」的
计数,而 issue #22 需求 3 写的是「三态徽标 + 已确认关系数」。触发例:fixture
`{confirmed: 3, pending: 1}` 状态为 pending 时渲染成 `○ 1`,用户会把 1 读成已确认
关系数 —— 数字的含义随状态漂移,列里最先被读到的两个元素互相矛盾。

## 决策

采用评审建议 (a):`renderCell` 旁的数字恒为已确认关系数。纯函数由
`primaryCount(counts, state)` 改为 `relationCount(counts)`,即
`normalizeCounts(counts).confirmed`,与徽标状态解耦;状态只由字形表达,完整明细由
`title` 给出。

## 备选

- **备选 A:保持状态计数,把 issue #22 需求 3 改写成「状态计数」**。不选:需求原文
  就是「三态徽标 + 已确认关系数」,改需求去迁就实现会让 issue 与导出/排序语义脱节。
- **备选 B:单元里同时显示两个数字**。不选:窄列放不下,且 `title` 已给出完整明细。

## 后果

pending 条目现在渲染为 `○ 3`(状态 = 待核、数字 = 已确认 3);`title` 仍为
「已确认 N 条 · 待核 N 条 · 缺口 N 条」。`relationCount(counts)` 成为纯函数签名的一部分
(`tests` 与 `.d.mts` 同步)。200 条 fixture 断言 `relationCount(counts)` 必须等于
`parseCellText(cellText(counts)).confirmed`,cell 与导出文本不会再分叉。
