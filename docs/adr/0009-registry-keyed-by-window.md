# [A1] 注册簿按窗口分键,纯逻辑抽到 registryCore.mjs

- 日期:2026-11-18
- 状态:已接受

## 背景

两条行内评审指向同一根因:登记簿只有全局一维键。Zotero 支持多主窗口,启动时
`registerAll` 会按每个已存在的窗口各调一次,而 `unregisterAll` 不接收窗口 —— 第二个
窗口注册同一个 id 时要么撞键、要么被当成重复,用户看到的是「某个窗口里功能静默缺失,
且没有任何提示」。

## 决策

登记簿改为按 `(window, id)` 分键:同一 id 在每个窗口各注册一次,重复只在**同一窗口内**
报错;新增 `registerGlobal` 承接与窗口无关的注册;`claimWindow` 使 `registerAll` 对同一
窗口幂等。实现抽到纯 ESM 的 `plugin/src/registryCore.mjs`,不依赖 Zotero/DOM 全局,
以便 `node --test` 直接跑真实分支。

## 备选

- **备选 A:id 用 `${winId}:${id}` 字符串复合键**。不选:需要给 Window 编号或依赖 window
  对象身份字符串化,窗口复用(关闭后重开)时容易撞键,且字符串拼接让「按窗口回收」变成
  字符串解析。
- **备选 B:register 遇到重复只记 warn 并跳过**。不选:会静默地让第二个窗口缺功能 ——
  正是原发现里「用户看不到任何功能也没有提示」的问题,把可诊断的失败换成了安静的降级。

## 后果

后续功能的注册签名从 `register(id, dispose)` 变为 `register(win, id, dispose)`;notifier、
深链桥等窗口无关注册改走 `registerGlobal`,只由 shutdown 回收。`plugin/AGENTS.md` 与
`plugin/src/registry.ts` 的注释已同步改写,新功能照签名接即可。
