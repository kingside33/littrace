# [A2] 列注册并入已有的 registerWindowIndependent(),不新增第二个进程级入口

- 日期:2026-11-18
- 状态:已接受

## 背景

「谱系」自定义列最初基于一个尚未合入的分支开发,当时 `plugin/src/registry.ts` 只有
`registerAll(win)` 一条注册路径,于是新列被挂进一个新增的 `registerGlobalAll()`。等
rebase 到当前 main 时,#20(A3)与 #21(S4)已经先落地:`registry.ts` 里已有
`registerWindowIndependent()` 加 `globalsRegistered` 防重入,专门承接窗口无关注册
(阅读器事件)。两个函数在做同一件事,只是名字不同。

## 决策

删除新增的 `registerGlobalAll()`,把「谱系」列的注册并入 main 已有的
`registerWindowIndependent()`。列仍以 `registerGlobal("item-column:lineage", dispose)`
登记,仍由 `unregisterAll()` 统一回收;`registerAll(win)` 的注释里明确写出列与阅读器
事件都不在它那里注册。

## 备选

- **备选 A:新增独立的 `registerGlobalAll()`(原方案)**。不选:与 main 上已有的
  `registerWindowIndependent()` 形成两个并行的「只跑一次」入口,两者各有自己的防重入
  标志,后人无法判断新功能该挂哪个,且 `unregisterAll()` 需同时复位两处。
- **备选 B:把列塞进 `registerAll(win)`**。不选:那是按窗口执行的,列会被重复注册。

## 后果

进程级注册只有一个入口、一个 `globalsRegistered` 防重入标志;`registerAll` 的注释明确
写了列与阅读器事件都不在那里注册。`plugin/AGENTS.md` 与 `tests/test_plugin_column.cjs`
的源码锚定同步改为断言 `registerWindowIndependent` 内含 `createLineageColumn()` 与
`registerGlobal("item-column:lineage")`,并反向断言 `registry.ts` 不再出现
`registerGlobalAll`。测试总数随 main 上的 A3/S4 用例一起为 168。
