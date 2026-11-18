# [S4] 无任务 ID 的提交是结论而非错误

- 日期:2026-11-18
- 状态:已接受

## 背景

评审 finding 1(PR #21 行内评论 4609785391,`serviceClientCore.mjs:281`):
`kbapp/workflows/jobs.py:68` 成功只返回 `{'state': 'started'}`,已在运行时返回
`{'state': 'running', 'message': ...}`(jobs.py:42/46/48/53),从不带 id。把无 id 一律
当 `retryable: false` 的 contract 错误,会让已经在后台跑起来的任务显示「提交失败」。

## 决策

`submitTask` 改为返回 `{outcome, taskId, state, message, data}`:`queued`(带可轮询
id)/ `started` / `running`(保留服务端 message)/ `unknown`(2xx 但无结论,不宣称成功
也不盲目重试);词表收敛为 `IDLESS_SUBMIT_STATES = ['started', 'running']`。

## 备选

- **保留 `taskIdFromResponse`,在文档里限定只用于带 id 的端点**。不选:`POST /api/jobs`
  是插件最常用的任务提交入口,job 已在后台跑起来却报错是更严重的误导。
- **把无 id 当成功但不区分 state**。不选:`started` 与 `running` 的 UI 文案不同,调用方
  需要能区分。

## 后果

`submitTask` 的返回契约从单个 taskId 变为带 `outcome` 的判别式结论,调用方必须分支处理
而非直接读 `taskId`;代价是新增一个类型 `SubmitResult` 与 `unknown` 分支,换来的是不再把
已成功的提交报告为失败。
