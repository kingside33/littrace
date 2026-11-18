# [S4] requirements 区分「未提供」与「显式 null」

- 日期:2026-11-18
- 状态:已接受

## 背景

评审 finding 2(PR #21 行内评论 4609787762,`serviceClientCore.mjs:202`):`assessHealth`
用 `??` 取默认值,把显式传入的 `minServiceVersion: null` 当成「未提供」并填回默认下限
`0.1.0`,而类型声明与 JSDoc 都允许 `string | null`,导致「不设下限」这个配置项无法
表达。

## 决策

用 `"key" in given` 判断:缺键取默认,显式 `null` 关闭该边界;`apiVersion` 不可关闭,
min/max 的非 string 非 null 取值一律抛 `TypeError`。

## 备选

- **删掉 `| null`,让「关掉检查」只有一种写法(评审建议的另一分支)**。不选:默认配置
  本来就以 `null` 表示「无上限」,删掉会让默认值自身不可表达。
- **保留 `??`,并在文档里写明 null 等同未提供**。不选:类型声明允许 `null` 却在运行时
  静默忽略,正是要修的那类漏洞。

## 后果

配置语义变为「缺键取默认 / `null` 关闭 / 其他类型抛错」三态;代价是 `apiVersion` 不可
关闭(有意收窄),想放开 min/max 边界必须显式写 `null`。该语义由判定表里的四个新用例与
六个 `TypeError` 断言锁定(`tests/test_plugin_service_client.cjs`)。
