# [U2] 用 merge 而非 rebase 合入已前进的 main,保住所引用的修复提交哈希

- 日期:2026-11-18
- 状态:已接受

## 背景

推送 7 个修复提交后 PR 变为 `CONFLICTING`:main 已前进到 `18fcd99`(U4 四 tab
与宿主桥接层 #28),与 U2 改了同一批文件。GitHub 在无法生成合并提交时不为
`pull_request` 事件创建运行,所以 `49afc3d` 上没有 CI——同样是被动静默,不是红灯。

## 决策

合入 `origin/main`,产生 merge commit `a6e4c49`。三处冲突均按「两边都保留」解决:
`plugin/src/registry.ts` 同时导入 `createItemSections` / `disposeItemSections` 与
`createTabHost`;两份 `addon.ftl` 同时保留 U2 的 `section-*` 与 U4 的 `tab-title-*`,
两组键互不重叠。

## 备选

- **备选 A:rebase 到 origin/main**。不选:提交会被改写,评审线程回复里已引用的
  短哈希全部失效,且需要 force 重推分支。
- **备选 B:不管冲突,等维护者手工处理**。不选:PR 会一直 `CONFLICTING`,CI 永不
  运行,等于交付不完整。

## 后果

PR 恢复 `MERGEABLE`,CI 在 `a6e4c49` 上全绿。代价是历史多一个 merge commit。

三处冲突的「一方覆盖」并非无人看管,而是会被现有锚点判红:取无 `createTabHost` 的
一侧则 `tests/test_plugin_tabs.cjs:357-367` 的源码锚点失败;取无 `createItemSections()`
的一侧则 `tests/test_plugin_sections.cjs:679-689` 的函数体锚点失败;两份 FTL 保留 U2
一侧则 4 个 `tab-title-*` 缺失、`tests/test_plugin_tabs.cjs:81-96` 失败,保留 U4 一侧
则 `section-*` 缺失、`tests/test_plugin_sections.cjs:443-450` 失败。

无法自动校验的是「两边都保留了、但保留的内容被改错」这类语义错误——锚点只确认符号
与键存在,不确认行为正确;这部分仍需人工按同一纪律处理。
