# [CI] PR 默认快线，重型全量由 full-ci 标签或 merge_group 触发

- 日期:2026-11-19
- 状态:已接受

## 背景

`.github/workflows/ci.yml` 原来在每个 PR 上跑完整矩阵:pytest 三条腿
(ubuntu 3.10/3.13、windows 3.13)、frontend-tests 两条腿、plugin-build
两条腿,另有 python-lint、dco、actionlint。Windows pytest 是其中最长的一条,
每个 PR 都要等它。仓库同时没有 required status checks,check 名字是 matrix
展开后的 12 个,不适合逐个登记。

参考 Tavotto 的分层:PR 默认只跑快线给快速反馈;重型全量由 `full-ci` 标签
或 `merge_group` 触发;一个聚合 gate 把两条车道收成一个稳定 context。普通
PR 上重型全跳过时 gate 判 deferred;`full-ci` / `merge_group` 上不允许
deferred。本仓库 owner 是个人账号,merge queue 暂不可用(见 ADR 0040),但
PR 阶段仍然需要提前发现平台缺陷,所以 `full-ci` 标签是队列之外的显式入口。

## 决策

- **快线**在每个 push、PR 和 merge_group 上跑:`python-lint`、`actionlint`、
  `dco`、`pytest`(ubuntu 3.13)、`frontend-tests`(ubuntu + windows)、
  `plugin-build`(ubuntu + windows)。
- **重型**只在 `merge_group` 或带 `full-ci` 标签的 PR 上跑:
  `pytest-platforms`(ubuntu 3.10 + windows 3.13) 与 `package-smoke`
  (sdist/wheel + twine check + 干净 venv 安装)。frontend/plugin 的两平台
  腿留在快线,避免把 21 个 node 测试清单复制成四份。
- `CI gate` 是唯一稳定的 CI context,判定逻辑在
  `scripts/ci/aggregate_gate.py`:
  - push:快线必须 success;dco 与重型 skipped 允许。
  - 普通 PR:快线 + dco 必须 success;重型全部 skipped → `deferred`
    (exit 0);重型半跑半跳 → 失败。
  - `full-ci` PR 与 merge_group:快线 + dco + 重型全部 success;任一
    skipped → 失败。
  - 任何 failure/cancelled/未知结果 → 失败;`needs` 缺 required job 或出现
    闭集外 job → 配置错误(exit 2)。
- 摘掉 `full-ci` 的 `unlabeled` 事件仍按 full-ci 判定:重型 job 因标签不在
  而 skipped,gate 判红,不能用删标签把重型失败洗成 deferred。
- 判定器优先从默认分支 checkout;只有引入它的那个 PR 走 bootstrap 回退
  (默认分支还没有脚本时用本 revision 的副本,并打 `::notice::`)。
- concurrency 按 head SHA 分组、`cancel-in-progress: false`:required check
  按 SHA 记录,被取消的 gate 无法翻绿;代价是新 push 不会取消旧 run。
- `CodeQL gate` 用同一判定器与同一 concurrency 规则,成为 CodeQL 的稳定
  context;`merge_group` 上不上传 SARIF。

## 备选

- **每个 PR 继续跑完整矩阵**。否决:没有快速反馈,Windows pytest 拖慢每个
  PR,`full-ci` 标签与 deferred 的收益为零。
- **path filter / docs-only 快车道**。否决:漏配 filter 会静默跳过,仓库小到
  不值得承担这个风险;`ci.yml` 原有注释也明确不加 path filter。
- **拆成 fast gate + integration gate 两个 required context**。否决:小仓库
  多一个 context 与一套 ruleset 配置,收益不足;一个 gate 已能区分 deferred
  与 required。
- **只在 merge_group 跑重型**。否决:队列当前不可用,PR 阶段无法提前发现
  平台/打包缺陷;`full-ci` 标签是队列可用前的唯一显式入口。

## 后果

普通 PR 只等快线,反馈更快;带 `full-ci` 的 PR 会多跑两条 pytest 腿与打包。
代价是多一个标签纪律:改动涉及跨平台行为、打包或 Python 版本下限时要记得
打标签;任何 label 增删都会重跑快线,因为 GitHub 不能按 label 名过滤
`pull_request` 事件。新增 `CI gate` / `CodeQL gate` 两个 job;required 登记
仍留给 #46,且必须先看到 main 上的 success。判定器首次合入存在一个 PR 的
bootstrap 空窗,之后从默认分支读取。`PyYAML` 已是运行时依赖,契约测试没有
新增依赖。实现见 PR #85。
