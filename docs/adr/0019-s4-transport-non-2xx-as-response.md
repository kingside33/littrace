# [S4] 传输层把非 2xx 当响应返回,只有无响应才是 status 0

- 日期:2026-11-18
- 状态:已接受

## 背景

`Zotero.HTTP.request` 对非 2xx 以 `UnexpectedStatusException` 拒绝、对无响应以
`TimeoutException` / `BrowserOfflineException` 拒绝,两类都走 reject。若核心直接面对
这个形态,403 失效重取、5xx 呈现、三态判定的分类逻辑就无法脱离 Zotero 进程做纯函数
断言。

## 决策

`plugin/src/service-client.ts` 的 `zoteroTransport` 在边界翻译:非 2xx 的异常转成
`{status, text}` 的正常返回,只有 status 无法取得时返回 `{status: 0}` 表示不可达;
`responseType` 保持默认 `text`(避免 `responseType: 'json'` 下 Firefox 读 `responseText`
抛 `InvalidStateError`),JSON 只在核心的一处解析。

## 备选

- **让核心捕获 Zotero 异常类型**。不选:把 Zotero 的异常类泄漏进纯逻辑,测试必须伪造
  Zotero 全局。
- **用 `responseType: 'json'` 让 XHR 自己解析**。不选:`responseText` 在该模式下不可读,
  且会在核心之外多出一个 JSON 解析点。

## 后果

三态判定与全部错误分类成为可 `node --test` 测试的纯函数;代价是适配层必须准确区分
「有响应但非 2xx」与「无响应」,该区分只在真机验收中可最终确认(已列入待办)。
