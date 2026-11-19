# [P2] 插件设置页的云端配置走服务侧 settings.json,密钥不落 Zotero pref

- 日期:2026-11-19
- 状态:已接受

## 背景

此前配置云端模型只能跳出 Zotero,打开 `http://127.0.0.1:8765/#settings` 的
Web 工作台(`plugin/src/readerPanel.ts` 的 `openSettings()` 就是这么跳的)。
这是插件里唯一还依赖外部界面的核心操作,与「所有操作入口位于 Zotero 内部」
的定位冲突。

两条约束决定了实现形态:

- Zotero pref(`extensions.zotero.littrace.*`)是**明文**落 `prefs.js`,而
  `SECURITY.md` 把「密钥经 API/日志/诊断泄漏」列为 in-scope;
- 服务端 `store.settings(public=True)` 把密钥换成布尔
  (`kbapp/storage/store.py:150-151`),界面本来就拿不到原文,只能显示
  「已保存 / 未保存」。

## 决策

插件设置页经 `GET/POST /api/settings` 读写服务端 `settings.json`;密钥由服务
以 DPAPI 加密保存,插件 prefs **不存任何密钥**
(`plugin/src/settings/settingsCore.mjs:63` 的 `SETTINGS_FIELDS`)。

配套的四条具体规则:

- 新控件**不带** `preference=` 属性——它们不是 Zotero pref,声明式绑定在这里
  只会让人以为值存在本机;
- 密钥框留空等于「保持原密钥」:`savePayloadFrom` 在密钥为空时**省略该键**
  (`settingsCore.mjs:265`);
- 清除密钥是**独立载荷** `{api_key:"", cloud_enabled:false}`
  (`settingsCore.mjs:323`)。必须同体关掉 `cloud_enabled`,否则
  `store.py:196` 以「启用前需填写 API、模型与密钥」拒绝,密钥原地不动;
- 非 Windows 上只禁用密钥框与清除按钮,其余字段照常可用——`protect()`
  对空值提前返回(`kbapp/core/secrets.py:8-9`),只有**非空**密钥才会抛错。

## 备选

- **备选 A:密钥存进 Zotero pref**。否决:明文落盘,与 `SECURITY.md` 的
  DPAPI 口径冲突;而且公开视图只有布尔,prefs 里再存一份必然出现「界面说
  已保存、服务说没有」的分歧。
- **备选 B:prefs 与服务双写**。否决:同一密钥两个出处,谁是真源说不清,
  改一边另一边静默过期。
- **备选 C:插件只读展示、继续跳转 Web 工作台写**。否决:用户要离开 Zotero
  才能启用云端能力,这正是本批要消掉的缺口。

## 后果

设置页从此有两条数据通道(Zotero pref 与服务端 `settings.json`),必须靠
测试把两者隔开:服务侧字段不得进 `PREF_ENTRIES` 或 `addon/prefs.js`。

本条取代早先规划文档里「模型与费用设置只在 Web 端写」的结论。代价是
mac/Linux 上无法从插件配置密钥——这与 `docs/support-matrix.json` 已记录的
「DPAPI 仅 Windows」是同一个边界,不是新限制。
