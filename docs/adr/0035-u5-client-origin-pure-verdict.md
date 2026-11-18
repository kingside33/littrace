# [U5] 端口变更以纯函数判定是否重建共享客户端

- 日期:2026-11-18
- 状态:已接受

## 背景

评审 PR #36 的 finding(`plugin/src/service-client.ts:108`):共享客户端缓存
的不只是 URL,还包括**整个生命周期的会话令牌**——它在构造时就把 `baseUrl` 钉死
(`createClient()`,`plugin/src/service-client.ts:102-111`)。

而 `serviceClient()` 返回的单例在插件启动时就被 `createStatusDot` 建好。于是改端口
之后,**除 iframe tab 外全部打旧端口**(状态点、诊断、条目列),而诊断弹层显示的
却是新端口——这正是「actively misleads」的那种组合。

## 决策

把 origin 比较抽成**纯函数** `clientOriginVerdict(currentBaseUrl, desiredBaseUrl)`
放进 `prefsCore.mjs` 供 node 测试(`plugin/src/prefs/prefsCore.mjs:290`),
**任何差异即重建**;`serviceClient()` 每次调用都比对并重置
(`plugin/src/service-client.ts:127-140`)。

重建会丢掉缓存的令牌——这是正确的,令牌按服务进程签发
(`kbapp/web/service.py`),不同端口就是不同进程、不同令牌。

## 备选

- **备选 A:注册 `connection.port` 的 pref observer,回调里调
  `resetServiceClient()`**。否决:需要一套 observer 生命周期与清理,而且只在
  「设置页被打开」的路径上生效。判定函数在 `serviceClient()` 的**每次调用**上都
  成立,与谁改了 pref 无关——包括直接改 `about:config`。

## 后果

端口改动在**下一次请求**即生效,不需要重启 Zotero。

代价是 `serviceClient()` 每次调用多一次 pref 读取(纯内存读,可接受)。同端口
重启服务时令牌仍会失效,由现有 403 分支自动重取。

判定逻辑已被 `tests/test_plugin_prefs.cjs:291-319` 锁定:同 origin 复用、尾斜杠
视为差异、缺值一律重建,并对 `service-client.ts` 的调用点做源码锚定。**真机仍需
验证实际端口切换**——单测只证明判定函数与接线文本,不证明 Zotero 里的实际行为。
