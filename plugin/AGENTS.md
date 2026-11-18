# plugin/ 速查表(plugin 层)

仓库分层约定见根 AGENTS.md;本文件只写 Zotero 插件子目录的速查。

- 目录:`addon/` 是进 XPI 的静态资源(manifest / bootstrap / prefs / locale);`src/` 是打包为 `content/scripts/littrace.js` 的源码,入口 `src/index.ts`;`zotero-plugin.config.ts` 是构建配置;`typings/` 是骨架期手写的最小沙箱全局声明,A2/A3 需要完整 Zotero API 类型时统一引入 zotero-types(随之需要 `.npmrc` 的 `allow-git`,见模板先例),不逐文件混用。
- 构建:`cd plugin && npm install && npm run build`,产物在 `.scaffold/build/*.xpi`;`node_modules`、`.scaffold`、`.env` 一律不进 git(见本目录 .gitignore)。
- 真机运行:`npm start`(zotero-plugin serve)。binPath / profilePath 不写死在本目录任何文件里:用命令行参数 `--zotero.bin-path` / `--zotero.profile-path`、环境变量 `ZOTERO_PLUGIN_ZOTERO_BIN_PATH` / `ZOTERO_PLUGIN_PROFILE_PATH`,或本地 `.env`(复制 `.env.example`)覆盖。
- 测试纪律:能脱离 Zotero 跑的纯逻辑测试一律放仓库根 `tests/test_*.cjs`(只用 node 内置模块),并同步加进 `.github/workflows/ci.yml` 的 TRACKED_TESTS 与 `node --test` 两处显式清单;依赖 Zotero 进程的测试只属真机验收批次,不进 CI。
- manifest 字段出处:`addon/manifest.json` 是唯一事实源,全部字面量、不经模板 `__占位__` 替换;`version` 必须与 `package.json` 保持一致(`tests/test_plugin_manifest.cjs` 校验)。
- 占位 ID:pluginID 暂为 `littrace@example.invalid`,待 A0 定名后在 `addon/manifest.json` 与 `package.json` 的 `config.addonID` 同步替换,名称不含 "Zotero" 字样。
- 注册纪律:一切 Zotero 注册(列 / section / 菜单 / notifier / 深链桥)只挂 `src/registry.ts` 的 `registerAll`,并交出 disposer;禁用 / 卸载由 `src/hooks.ts` 经 `unregisterAll` 全量回收,不得另立注册入口。
