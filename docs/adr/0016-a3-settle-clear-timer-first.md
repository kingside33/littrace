# [A3 修复] settle() 先清飞行中标记再构造结果,异常转为可重试失败卡片

- 日期:2026-11-18
- 状态:已接受

## 背景

`settle()` 原本先 `demoOutcome()` / `answer()` / `fail()`、后置 `mount.timer = null`。
静态数据下构造不出会抛的输入所以不可达,但接真实数据后若 `answer()` 抛错(例如结论为空
会走 `normalizeConclusion` 的 throw),timer 永远非 null,`run()` / `retryCard()` 的
`canAsk()` 守卫会让此后每次提问与重试都静默失效。

## 决策

`settle()` 先 `mount.timer = null`,再构造结果;把 `demoOutcome()` / `answer()` /
`fail()` 包进 try/catch,抛出时 `report` 后转成一张可重试的失败卡片,保证面板不会卡在
busy。

## 备选

- **备选 A:只把 `mount.timer = null` 提前,不加 try/catch**。不选:提前清标记只保证
  后续提问不被永久挡住,但当次那个异常会让面板停留在 busy 状态(进度行永存),用户看不到
  任何错误。
- **备选 B:异常只 `logError` 不做状态变更**。不选:同样会让进度行挂在 busy 状态,违反
  「失败显错误 + 重试」的面板契约。

## 后果

`answer()` 抛错时用户看到的是一张可重试的失败卡片,而不是卡死的进度条;后续提问也不再被
永久拦住。代价是 `settle()` 里多了一层嵌套 try —— 内层 `fail()` 本身也可能抛(例如
状态不一致),所以还有一个 nested catch 只做上报。
