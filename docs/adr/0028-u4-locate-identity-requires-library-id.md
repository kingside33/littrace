# [U4] 谱系定位用 z:<libraryID>:<key> 文献标识,缺失 libraryID 则拒绝而非猜测

- 日期:2026-11-18
- 状态:已接受

## 背景

A2 列派发的 payload 里 `itemKey` 是 **Zotero 条目 key**(如 `ABCD1234`),不是
littrace 文献标识。评审用仓库自带 Store 实跑证实:裸 key 必然落到「文献不存在」或
0 节点。

原因在解析链:`Store.resolve` 只查 DOI 与 `aliases` 表,未命中就原样返回
(`kbapp/storage/store.py:155-167`),而裸 Zotero key 不在任何 alias 里,于是
`paper_view` 抛「文献不存在」(`kbapp/web/library_api.py:16`),`graph_page` 也解析
不到任何节点。littrace 自己的文献标识形态是 `z:<libraryID>:<zoteroKey>`
(`kbapp/library/sources.py:34-40`)。

## 决策

`locateRequestOf`(`plugin/src/tabs/tabsCore.mjs:373`)用 `libraryID` + `itemKey` 拼
出 `z:<libraryID>:<zoteroKey>`;`libraryID` 缺失或非整数一律拒绝,并给出可记录的原因
(`locateRefusalOf`,同文件 `:339`),不猜测默认库。

## 备选

- **备选 A:`libraryID` 缺失时默认成 1**(与服务 `identity()` 的
  `int(p.get('library_id', 1))` 一致)。否决:群组库下会静默打开另一篇文献,是用户
  可见的错定位。
- **备选 B:直接拿原始 `itemKey` 当 paper**(即评审前的实现)。否决:`Store.resolve`
  解析不到,已由评审实跑证实 404 / 空图。

## 后果

定位在真实数据下可解析;只要 A2 的 payload 带 `libraryID`,所有条目都走此路径。缺失
`libraryID` 时不再打开 tab 而是记日志——这意味着假如真机上 A2 没传出 `libraryID`,
用户会看到控制台记录而不是空图。验收时需确认 A2 的 payload 字段(已入待办)。

拒绝路径的三种原因都有断言(`tests/test_plugin_tabs.cjs:292-299`),包括「有 key
无 libraryID」「有 libraryID 无 key」「载荷不是对象」。
