# [U1] 合入 main 解除冲突以恢复 pull_request CI,而不是把「无 CI」当作已跑门禁

- 日期:2026-11-18
- 状态:已接受

## 背景

main 上的 U2(#31)同样修改了 `plugin/src/registry.ts`,使本分支变为
`CONFLICTING`。GitHub 在无法生成 merge ref 时不会为 `pull_request` 事件创建运行,
所以 `4ed958d` 上没有 CI:实测
`repos/kingside33/littrace/actions/runs?head_sha=4ed958d` 返回 `total_count 0`。

失败模式是「静默不跑」而不是红——比红灯更危险,因为看板上一片安静,容易被当成通过。

## 决策

在 worktree 内 `git merge origin/main`,冲突按「两边都保留」解决:import 同时引入
`createReadingColumn` 与 `createTabHost` / `createItemSections`;
`registerWindowIndependent()` 依次注册两列与三条 section,各自交出 disposer。以
`-s` 提交合并 `1511210` 并推送。

## 备选

- **备选 A:rebase 到 origin/main**。不选:会重写 `4ed958d` / `f701549` 这两个已在
  评审线程回复里引用的修复提交哈希,让评审无法回溯。
- **备选 B:不带 CI 就宣布门禁通过**。不选:那等于把「没跑」当作「跑过」,与仓库
  纪律直接冲突。

## 后果

PR 转为 `MERGEABLE`,CI 重新触发并全绿(含 dco)。合并提交不影响两个修复提交的语义,
评审线程已补充说明。合入后本地实跑:244 个 node 用例与 467 个 pytest 用例全部通过。
代价是历史多一个 merge commit。

`registry.ts` 两处冲突的「一方覆盖」并非没有守卫:取无 `createReadingColumn` 的
main 一侧(`0fb54e0`)则 `tests/test_plugin_column.cjs:643-652` 的锚点失败;取无
`createItemSections` 的 U1 一侧(`4ed958d`)则
`tests/test_plugin_sections.cjs:679-689` 失败;取无 `createTabHost` 的一侧则
`tests/test_plugin_tabs.cjs:357-367` 失败。没有自动守卫的是「两边都在、但内容被
改错」这类语义错误,仍需人工核对。
