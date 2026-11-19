# [CI] 合并队列按「Gate 先于队列」启用，个人账号仓库暂不可用

- 日期:2026-11-19
- 状态:已接受

## 背景

GitHub 的 merge queue 只对 organization 拥有的仓库提供。littrace 的 owner
是个人账号 `kingside33`(`owner.id` 以 `U_` 开头);对 `merge_queue` ruleset
规则的探测返回 422。因此当前无法真正启用队列。

但队列的 CI 语义可以先落地:`merge_group` 触发、`CI gate` / `CodeQL gate`
在组合提交上要求重型 success、`dco` 的 `not_applicable` 分支、按 head SHA
分组且永不取消的 concurrency。这些代码路径现在就能被 actionlint 与契约测试
锁定,转 organization 后只需改 ruleset,不再改 workflow 形状。

## 决策

启用顺序不可交换:

1. `CI gate` / `CodeQL gate` 先在默认分支的 push run 上产出 success。
2. 登记 required contexts(`CI gate`、`CodeQL gate`),`strict=true`,不加
   bypass actor。
3. 仓库转入 organization。
4. 在同一次 ruleset 变更里加 `merge_queue` 并关闭 strict;不能分两次,否则
   中间态会让所有 PR 等一个不存在的队列或永远落后 main。
5. 用一个低风险 PR 做 canary:确认 `merge_group` run 出现、两个 Gate 在
   组合提交上 success、PR 被队列合入。

队列参数:`merge_method=SQUASH`、`grouping_strategy=ALLGREEN`、
`max_entries_to_build=4`、`max_entries_to_merge=1`、
`min_entries_to_merge=1`、`min_entries_to_merge_wait_minutes=0`、
`check_response_timeout_minutes=180`。

`merge_group` 上的 CodeQL 只产结论、不上传 SARIF:组合提交是临时 ref,
上传会制造重复告警。

## 备选

- **转 organization 之前不写 `merge_group` 触发**。否决:代码路径无法被
  actionlint/契约测试锁定,转组织时还要改 workflow,风险和评审面更大。
- **个人账号直接启用队列**。不可行:平台不提供,API 返回 422。
- **先登记 required、后写 Gate**。否决:required context 只能登记已经在
  默认分支产出过结论的名字;反了会让每个 PR 永久 pending,而 ruleset 没有
  bypass actor。

## 后果

转移前 `merge_group` run 不会出现,这段代码是惰性的;required checks 仍可
先按 #46 登记,普通 PR 的 `CI gate` 仍是 deferred 绿。转组织后按上面的顺序
启用,并由 #84 记录线上 ruleset id、启用日期与 canary PR。队列参数写在本
ADR 与 #84,不写死在 workflow 里;workflow 只负责在 `merge_group` 上跑
重型并让 gate 要求它。实现见 PR #85。
