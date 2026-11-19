# [P3] 设置页单次快照读取,每组只写自己的 section

- 日期:2026-11-19
- 状态:已接受

## 背景

两个功能从不同方向落进了同一张设置页:云端 AI 组(P2)与连接组(P3),每组
各自带了一次读。叠在同一个 `onPaneLoad` 里,打开面板一共发 6 个请求,其中
`/api/settings` 与 `/api/version` **各重复一次**,且相隔毫秒:

    prefsPane.refresh → /api/version ; loadCloud → /api/settings
    loadConnection    → /api/settings(重复) + /api/version(重复)
                        + /api/status + /api/models

不只是浪费:两个写同一批行的人,可能在各自应答之间显示不一致的值——健康行
来自第一次探测,版本行来自第二次。

同一类问题出现在写侧:`savePayloadFrom` 原本遍历整张表,于是保存云端表单会把
一个空的 `zotero_dir` 一起发出去,**静默清掉另一组管理的目录**,而用户从任何
一个表单都看不出发生了什么。

## 决策

新建 `plugin/src/settings/paneRefresh.ts` 持有页面唯一的一次读:

- `collectPaneSnapshot()` 发一次 `/api/version`、一次 `/api/settings`,以及
  诊断的两次附加 GET(它们仍按兼容性门控);
- 两个组降级为**纯渲染器**(`renderCloud` / `renderConnection`),`mount*` 只
  注册按钮、不发起任何读;
- `showing` 重入监听只挂一处(`mountPane`),不按组各挂一个;
- 「刷新」按钮走同一条 `refreshPane`——它原先只重画健康行,用户启动服务后按下
  会看到「在线」紧挨着一个几秒前读取失败的表单。

`collectDiagnostics()` 增加可选参数 `{ health }`
(`plugin/src/status/diagnostics.ts:63`):调用方已经探测过时把 verdict 传进来,
不再探第二次。**rows 无条件构建**——`diagnosticRows` 是纯函数,能只凭 verdict
算出「版本过旧」/「不可用」;只有那两次附加 GET 才按兼容性门控。把行构造也
门控掉会让版本行在「版本过旧」时变成空白,而那正是它唯一有用的场景。

写侧:`savePayloadFrom(form, section)` 按 `section` 过滤
(`settingsCore.mjs:265`),两个 writer 各写自己那一组。

## 备选

- **每组自读,靠缓存去重**。否决:缓存要处理失效,而这里的问题根源是「页面
  没有单一时刻」,不是请求次数。
- **只发变更字段**。否决:需要 diff 基线,复杂度换不来收益;section 过滤已经
  解决了跨组覆盖。
- **把行构造也门控在 `available` 之内**(本轮修掉的实现)。否决:见上,「版本
  过旧」时该行会空白。

## 后果

新增分组必须接入 `paneRefresh`,不能自己读;新增服务侧字段必须声明
`section`,否则测试会红。

页面请求数从 6 降到 4,且健康行、版本行、两个表单、缓存行全部来自同一时刻。
代价是任一读失败时该行会留空而不是保留上一次的值——这比显示两个不同时刻的
结论要好,但也意味着「刷新」是用户修复可见性的手段。
