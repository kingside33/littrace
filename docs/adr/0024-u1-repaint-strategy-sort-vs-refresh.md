# [U1] 数据落地后的重绘拆成「重排」与「重绘」两种策略,按当前排序列选择

- 日期:2026-11-18
- 状态:已接受

## 背景

数据落地后调用 `refreshAndMaintainSelection()` → `refresh()` → 行提供者的
`_refresh()`,它只对新增行调用 `_sort(addedItemIDs)`。而一次重绘不新增任何行,
`_sort([])` 会在 `itemTree.jsx` 的 `if (!skipped.length) return` 处提前返回,
已有行永不重排。

后果是:首次点列头时缓存全空,每行的键都是 `""`,`_compareField` 的默认分支对任意
两行都返回 0(行序不变),而表头箭头已经画上了——箭头与行顺序从此一直不一致,用户
必须再点一次才对齐。

## 决策

抽出纯函数 `repaintStrategy(view)`(`plugin/src/columns/columnDataCore.mjs:404`):
排序字段命中 `COLUMN_DATA_KEYS`(`lineage` / `reading`,同文件 `:381`)且 `view.sort`
可用 → `"sort"`(调用无参 `sort()`,它逐行重新比较,并经 `update` 监听器 invalidate
整棵树,一次完成重排 + 重绘);否则 `refreshAndMaintainSelection` 可用 → `"refresh"`;
两者皆无 → `"none"`。只在我们自己的列是当前排序字段时才做全量重排
(`plugin/src/columns/paperData.ts:59`)。

## 备选

- **备选 A:每次数据落地都全量 `sort()`**。不选:用户按标题排序时白付一次整树重排。
- **备选 B:先在首帧返回占位值再排序**。不选:占位文本会污染排序键与导出结果。
- **备选 C:持续轮询直到首屏全部落地**。不选:无必要地重复请求。

## 后果

首次排序后无需再点第二次;按非本列字段排序时完全不受影响。策略本身可脱离 DOM 测试
(`tests/test_plugin_column.cjs:1148`),而排序后的真实 DOM 行为仍属真机验收
(已列入 PR 待办)。
