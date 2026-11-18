# [A3 修复] 用 isConnected 兜底回收已关闭阅读器的面板,不依赖未定案的关闭事件名

- 日期:2026-11-18
- 状态:已接受

## 背景

`mounts` 只在两处删除:面板自己的关闭按钮与插件禁用。`READER_EVENTS` 只注册了
`renderTextSelectionPopup` 与 `renderToolbar`,没有任何 reader 关闭事件的处理,所以
关掉阅读器标签页后 Map 里留下对已销毁 Document 的强引用,连带整棵面板 DOM 与卡片状态
一起无法回收。

## 决策

新增 `pruneDetachedMounts()`,以 `mount.root.isConnected` 判断宿主是否还挂着,
`openPanel()` 与 `disposeReaderPanels()` 入口各扫一次,删掉条目并取消 pending timer。
刻意选取不依赖任何 reader 事件名的判据,因为承载机制还未真机定案。

## 备选

- **备选 A:注册 reader 的关闭事件,在那里 `disposeMount(doc)`**。不选:承载机制尚未
  真机定案,关闭事件的准确名称无从确认,现在写就是猜一个 API;猜错的话泄漏依旧。
- **备选 B:不处理**。不选:这是新代码引入的资源泄漏,且会一直留到原型之后的真实实现里。

## 后果

关闭一个阅读器标签页后,下一次 `openPanel()` 或插件禁用时会回收其 Document 与面板
DOM,Map 不再随会话增长。代价是回收时机稍晚(不是关页即回收,而是下一次打开面板或禁用
时),且没有对已关闭文档做任何清理的路径仍要等到这个触发点。真机定案出关闭事件名后可以
叠一个事件驱动的回收,这层兜底仍然成立。
