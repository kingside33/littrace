# [U1] 列请求经固定上限的有界队列发出(6),排队项被提前填好则跳过

- 日期:2026-11-18
- 状态:已接受

## 背景

条目列的 `dataProvider` 是同步取数的。而一次点列头会让整棵树逐行取值:
`itemTree.jsx` 的 `_handleColumnSort` → `sort()` → `rowProvider.sort()`,且
`_initSortState` 每次都清空 `_sortCache`,等于每一行恰好回源一次。

服务端是单进程 uvicorn(`kbapp/workflows/cli.py:109`),其它接口的请求超时是 10s。
不设上限时,一次排序就会对单进程抛出全库规模的并发,把不相关的接口挤进超时。

## 决策

在 `createPaperDataProviders`(`plugin/src/columns/columnDataCore.mjs:442`)内加请求
队列与常量 `DEFAULT_MAX_CONCURRENT_REQUESTS = 6`
(`plugin/src/columns/columnDataCore.mjs:69`):超出上限的载入排队,完成后由 `pump()`
继续;排队中的 key 若已被填入(或已失败)则直接跳过、不发请求
(`plugin/src/columns/columnDataCore.mjs:469`)。上限通过 `maxConcurrentRequests`
注入,以便测试用小值验证。

## 备选

- **备选 A:不设上限,给每个失败请求加重试**。不选:只会放大对服务的压力,把一次
  过载变成持续过载。
- **备选 B:限流到 1(完全串行)**。不选:大书库填充过慢,用户会以为列坏了。

## 后果

排序一次最多 6 个在途请求,其余在数秒内依次落地;每落地一个都触发一次节流后的重绘
(`plugin/src/columns/paperData.ts:59`)。峰值并发不超上限、排队项最终全部落地、以及
「已被填好则跳过」三条都有 node 用例钉住
(`tests/test_plugin_column.cjs:1048、1067、1110`)。
