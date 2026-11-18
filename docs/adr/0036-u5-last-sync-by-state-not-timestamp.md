# [U5] 上次同步按 export_sync 状态而不是时间戳判定

- 日期:2026-11-18
- 状态:已接受

## 背景

评审 PR #36 的 finding(`plugin/src/status/statusCore.mjs:304`)。

`sync_exports()` 一轮里**写三次** `export_sync`:`running` → `synced` 或 `failed`
(`kbapp/workflows/publication.py:44-55`),三次都刷新 `updated`。只读 `updated`
会把一次**失败**的同步读成成功,显示成绿色的「上次同步 <时刻>」;而该行原先固定
`tone: ok`,也不进「失败项」清单——两个误导叠加。

## 决策

新增 `lastSync(status)` 返回 `{ state, at, error }`
(`plugin/src/status/statusCore.mjs:331`),按状态词判定:

- 只有 `state === synced` 给 `ok`;
- `failed` 给 `warn`,**进「失败项」并显示 `error`**;
- `running` 给 `unknown`(它确实在跑,但「上次同步」不是它);
- `update_finished_at` / `index_published_at` 由构造必然是成功,仍按 `synced`。

状态词与语言包的映射在 `SYNC_KEYS`(`statusCore.mjs:306`),行模型多
`noteKey` / `detail` 两个字段(`statusCore.mjs:422-423,505-507`)。

## 备选

- **备选 A:`failed` 时直接用服务返回的 `error` 文本替换 `value`**。否决:会把
  **失败时刻**藏起来——用户需要知道「什么时候失败的那一次」,这恰恰是判断
  「现在的库新不新」的依据。
- **备选 B:把 `running` 也当成功(它确实在跑)**。否决:`running` 的 `updated`
  是**本次开始**时刻,以绿色「上次同步」呈现就是评审指出的那个误读:用户会以为
  数据已经是最新的。

## 后果

失败的同步不再伪装成功,并且会出现在「失败项」里——失败从「看不见」变成
「可行动」。

代价是该行需要两种信息源(时间戳 + 状态词 + 可选错误),行模型多两个字段,
两份 `.ftl` 各多 4 个状态词
(`plugin/addon/locale/{zh-CN,en-US}/addon.ftl` 的 `status-diag-sync-*`)。
