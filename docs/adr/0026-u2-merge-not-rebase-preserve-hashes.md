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

PR 恢复 `MERGEABLE`,CI 在 `a6e4c49` 上全绿。代价是历史多一个 merge commit,以及
两处 FTL 与 `registry.ts` 需人工确认「两边都保留」而非一方覆盖;后者的漏检不会被
现有测试抓住,下一次同类冲突仍需人工按同一纪律处理。
