# [U3] 问答走异步 agent-run + 轮询,而非同步 POST /api/ask

- 日期:2026-11-18
- 状态:已接受

## 背景

阅读器面板要把一次提问渲染成**一行阶段进度条**,并且必须在 S4 统一客户端的
10 秒请求超时纪律内工作(`plugin/src/serviceClientCore.mjs:64`)。

同步的 `POST /api/ask` 走 `qa.answer` 全流程(`kbapp/research/qa.py:53` 起,最多
三轮检索与校验),耗时必然超过 10 秒;而且它在返回前不提供任何中间事件,进度条
只能靠本地计时器「编」出一段假的推进。

## 决策

用**异步任务 + 轮询**两段式:

- `POST /api/agent-runs`,`kind="answer"`,拿到 run id
  (`kbapp/web/research_api.py:24-27`);
- `GET /api/agent-runs/{id}` 轮询终态(`kbapp/web/research_api.py:29-32`)。

单次请求都在超时纪律内。**run 顶层**的 `processed` / `total`(由 `get_run` 计算
并加到 run 对象上,`kbapp/workflows/agents.py:44,46`)与 **`run.result` 里的
`role`**(`agents.py:47-50` 只把 `summary` 并到顶层,`role` 留在 `result`)映射成
一行**单调不减**的进度条(`plugin/src/readerPanelCore.mjs:934-937` 对顶层
`snapshot.total` / `snapshot.processed` 做 clamp,`:921-922` 读
`snapshot.result.role`);到终态后再取 `run.result` 映射成答案卡片。

## 备选

- **备选 A:沿用同步 `POST /api/ask`**。否决:要么破坏 10 秒超时纪律(把这条
  纪律开一个例外,后续每个长请求都会要求同样的例外),要么频繁误报超时;而且
  进度条无论如何都只能造假,一行假进度比没有进度更糟。
- **备选 B:直接读服务端的 `agent_runs` 表**。否决:那是跨进程读服务库,绕开了
  HTTP 契约这一层,插件从此与服务端 schema 耦合,违背「只经 HTTP 契约说话」的
  分层。

## 后果

面板与工作台共用同一条链路与同一套状态**词**,但词表是**字面量副本,不是共享
常量**:权威定义在 `kbapp/workflows/states.py:22-24`,插件侧两处硬编码副本
(`serviceClientCore.mjs:35-41` 的 `TERMINAL_STATUSES`、
`readerPanelCore.mjs:750-757` 的 `RUN_ACTIVE_STATES` / `RUN_TERMINAL_STATES`),
工作台另有一份 `kbapp/web/assets/modules/graph.js:307`。

**没有从 `states.py` 生成这些常量的步骤,也没有绑定两边的守卫测试**:
`tests/test_plugin_service_client.cjs:361-366` 断言的是插件那份字面量自身,
`python kb.py doctor architecture` 只查 Python import 图、不覆盖此处。因此
**改 `states.py` 必须同步改插件两处**——漏改的失败模式是静默的:`startPolling`
(`serviceClientCore.mjs:396-398`)循环无轮询上限,新状态不在
`TERMINAL_STATUSES` 里就永远等不到终态,面板按 `POLL_INTERVAL_MS=1200`
(`readerPanel.ts:108`)一直轮询、答案卡片不出现,`stageForRun` 又因新状态不在
`RUN_ACTIVE_STATES` 里返回 `null`(`readerPanelCore.mjs:918`)、进度行定格,而
现有测试仍全绿。

代价是每次提问多一次 `GET /api/status` 预检(见 ADR 0031)与若干次轮询;轮询
必须可取消,取消挂在面板 `dispose` 上——已用 `epoch` + `flight.setCancel(() =>
poller.cancel())` 实现(`plugin/src/readerPanel.ts:533-540,610-627`),面板关闭后
迟到的轮询结果因 epoch 过期而不绘制。
