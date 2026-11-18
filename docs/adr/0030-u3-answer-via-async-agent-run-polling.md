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

面板与工作台共用同一条链路与同一套状态词表(`kbapp/workflows/states.py:22-24`
的 `STATES` / `TERMINAL` / `ACTIVE`),服务端状态词一变两边同时变,不会出现两套
措辞。

代价是每次提问多一次 `GET /api/status` 预检(见 ADR 0031)与若干次轮询;轮询
必须可取消,取消挂在面板 `dispose` 上——已用 `epoch` + `flight.setCancel(() =>
poller.cancel())` 实现(`plugin/src/readerPanel.ts:533-540,610-627`),面板关闭后
迟到的轮询结果因 epoch 过期而不绘制。
