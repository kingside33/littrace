# [U4] 宿主存活判定抽成纯函数,dialog 关闭监听双保险

- 日期:2026-11-18
- 状态:已接受

## 背景

独立窗口被用户关闭时不触发本宿主拥有的任何事件,已 opened 的条目就此残留。复用分支
若只改 `src` 并 `return true`,用户看到的是「点了没反应」,且该 tab 在此环境永久失效。

主路径的 `Zotero_Tabs.add({onClose})` 同样押在一个未定案的非公开选项名上;真机若
忽略它,主路径也没有关闭通知。

## 决策

复用前用纯函数 `reuseVerdict({hostClosed, iframeConnected})`
(`plugin/src/tabs/tabsCore.mjs:320`)判定存活;任一生效信号表明条目已失效,则先
`closeOpened()` 清条目再全新挂载。**未知信号按存活处理**,避免重复窗口;dialog 退路
同时挂 `pagehide` 与 `unload` 双监听回报关闭
(`plugin/src/tabs/tabs.ts:568-569`)。

## 备选

- **备选 A:只靠 `Zotero_Tabs.add({onClose})` 摘条目**。否决:该选项名是未定案的非
  公开机制,真机可能直接忽略,且对 dialog 退路完全无效。
- **备选 B:未知信号一律判定为失效、每次重开**。否决:会开出重复窗口,是比复用更差
  的失败——用户点一次得到两个窗口,且旧的仍可用。

## 后果

关窗口 / 关 tab 后条目即时回收,不再出现「点了没反应」。代价是两条挂载路径都要各自
实现 `signals()`,漏一处会被源码锚点测试挡住(`tests/test_plugin_tabs.cjs:478` 断言
`signalCount === 2`)。真机仍需确认 `pagehide` / `unload` 在 chrome dialog 里都会
触发——那是待办,不是已证事实。
