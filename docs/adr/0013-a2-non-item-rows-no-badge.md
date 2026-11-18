# [A2] 非条目行不给徽标、不派发定位载荷

- 日期:2026-11-18
- 状态:已接受

## 背景

评审发现 PR #23 行内评论 4610676769 指出 `itemKeyOf` 的注释写「非条目行返回 ""」,但
`Zotero.Collection`(collection.js:39)与 `Zotero.Search`(search.js:44)都有 `.key`。
回收站视图里的已删除收藏夹 / 保存搜索会被当成条目渲染出假徽标,点击还会把非条目的 key
派发给 U4(谱系页的定位载荷)。

## 决策

在纯逻辑层新增 `isItemRef(ref)`:优先按 Zotero 自身的 `_objectType` 判定
(item.js:99 为 `'item'`),该字段未暴露时回退为「`isRegularItem` 与 `getField` 都是
函数」—— `Zotero.Collection` 与 `Zotero.Search` 两者皆无。`itemKeyOf` 先过
`isItemRef`,fake data provider 对非条目行返回零计数,`itemRefOf` 只返回真条目。

## 备选

- **备选 A:只在 Zotero 面用 `item instanceof Zotero.Item` 过滤**。不选:纯函数不可测,
  且 `itemKeyOf` 仍会被误用于非条目。
- **备选 B:只过滤 data provider、不管点击**。不选:回收站收藏夹仍会派发无效载荷。

## 后果

回收站里的收藏夹 / 保存搜索不再出现假徽标或假计数,点击也不会派发
`littrace:lineage-locate`;`itemKeyOf` 的注释改为「非条目返回空串」并说明回收站收藏夹
也有 `.key`。新增用例覆盖 collection / search / header / spacer、无 `_objectType` 的
两条回退分支,以及真条目不受影响;item refs 用例补了收藏夹行返回 `null` 的断言。
