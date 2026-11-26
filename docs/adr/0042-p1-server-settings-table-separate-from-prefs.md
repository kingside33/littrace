# [P1] 服务侧设置字段表与 Zotero pref 表分离,键名由跨语言测试锚定

- 日期:2026-11-19
- 状态:已接受

## 背景

设置页现在有两类行:一类是 Zotero pref(`prefsCore.mjs` 的 `PREF_ENTRIES`),
一类是服务端 `settings.json`(`settingsCore.mjs` 的 `SETTINGS_FIELDS`)。

`PREF_ENTRIES` 的契约是「每个可见行在 `preferences.xhtml` 里都有声明式
`preference="<full key>"` 绑定」,由 `tests/test_plugin_prefs.cjs` 逐行核对。
服务侧字段永远做不到这一点,所以**不能**塞进同一张表。

更要紧的是漂移:`store.save_settings` 对不在 `DEFAULTS` 里的键直接抛
`ValueError`(`kbapp/storage/store.py:165`),经 `kbapp/web/service.py:75-77` 的 `ValueError` 处理器
变成 400。也就是说字段键打错一个字母,CI 全绿,用户点保存时才看到一句中文
报错。这两个语言之间没有任何编译期约束。

## 决策

独立一张 `SETTINGS_FIELDS` 表(`plugin/src/settings/settingsCore.mjs:63`),
每行声明 `key / type / section / sensitive / labelKey / descriptionKey`。
`section` 决定它由哪个组渲染与保存,`sensitive` 表示服务只回布尔。

三道跨语言锚,都由 `tests/test_plugin_settings.cjs` 承担:

- 表里每个 `key` 必须存在于 `kbapp/core/config.py` 的 `DEFAULTS`(正则扫描
  Python 字面量),失败信息点名字段;
- 服务侧键**不得**出现在 `PREF_ENTRIES` 或 `addon/prefs.js`;
- `PROBE_TIMEOUT_S` 与 `REQUEST_DISCIPLINE.timeout` 的大小关系(见 ADR 0043)。

## 备选

- **备选 A:把服务侧字段并进 `PREF_ENTRIES`,用 `ui: "server"` 区分**。否决:
  那张表的每条断言都建立在「有 `preference=` 绑定」上,掺进做不到的行等于
  让既有契约失去意义;而且 `addon/prefs.js` 的默认值比对也会跟着失效。
- **备选 B:不锚键名,靠代码评审**。否决:这正是本轮之前的实际状态,一个拼写
  错误要等到用户点保存才暴露。
- **备选 C:让服务端接受任意键**。否决:静默接受未知键等于把配置文件的拼写
  错误变成永久垃圾数据。

## 后果

新增一个服务侧设置项要同时改三处:表、两份 `.ftl`、服务端 `DEFAULTS`。测试会
挡住遗漏。

代价是这张表与 Python 那边的 `DEFAULTS` 是一份人工维护的重复——锚只能保证
「表里的键都存在」,不能保证「`DEFAULTS` 里的键都在表里」(那是有意为之:服务端
另有许多不面向设置页的键,例如 `daily_time`、`network_depth`)。
