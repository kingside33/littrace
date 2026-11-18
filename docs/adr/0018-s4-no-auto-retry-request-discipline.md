# [S4] 对本地服务的一切请求显式 errorDelayMax: 0 + timeout: 10000,禁自动重试

- 日期:2026-11-18
- 状态:已接受

## 背景

`Zotero.HTTP.request` 默认 `timeout` 30000ms,并对 429/5xx 自动重试、预算
`_errorDelayMax = 7500`(源码 `chrome/content/zotero/xpcom/http.js:8、211、1578-1640`,
实现期已下载核对)。本地服务是确定性端点,重试不会把本地错误变成功,但 7.5s 退避预算会
挂起轮询 promise 卡住 UI;插件的失败呈现契约是「错误 + 重试按钮」,重试必须由用户显式
触发。

## 决策

`plugin/src/serviceClientCore.mjs` 的 `REQUEST_DISCIPLINE` 与 `buildRequest` 是唯一
请求构造点,每个请求都带 `errorDelayMax: 0` 与 `timeout: 10000`;客户端内部无任何重试
逻辑,失败一律抛 `ServiceClientError` 给调用方;轮询助手按固定间隔顺序发 GET,同样不带
重试。

## 备选

- **依赖 Zotero 默认值**。不选:默认 7.5s 重试预算对确定性本地错误无意义,且与显式
  重试按钮的交互冲突。
- **封装层做一次性自动重试**。不选:静默重试会把真实故障掩盖成偶发慢请求,与服务离线
  必须明确禁用的纪律冲突。
- **只改 timeout 不改 errorDelayMax**。不选:只解决挂起时长,不解决重试语义。

## 后果

所有本地请求最多等 10s 且最多发一次,一次抖动即把错误交给 UI 由用户决定重试;代价是
瞬时故障不再自愈。该纪律由测试对每个记录到的请求逐条断言(`errorDelayMax === 0`、
`timeout === 10000`),新功能绕过 `buildRequest` 时测试立刻变红。
