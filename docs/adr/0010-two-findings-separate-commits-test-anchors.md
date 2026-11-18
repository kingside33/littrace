# [A1] 两个发现各自独立提交,并各自带可定位的测试锚定

- 日期:2026-11-18
- 状态:已接受

## 背景

仓库纪律要求「能脱离 Zotero 跑的纯逻辑进 `tests/test_plugin_*.cjs`」。registry 的分组与
回收逻辑本质是纯数据操作,但原实现写在依赖 Zotero 全局的 TS 里;`hooks.ts` 的接线更是
只能靠读源码断言。两条评审发现分别落在 registry 层与钩子接线层,若混在一个提交里,
日后回溯「这个缺陷是怎么被锁住的」会分不清哪部分测试对应哪条发现。

## 决策

registryCore 用 `.mjs` 纯 ESM 实现(无 Zotero/DOM 全局),由
`tests/test_plugin_registry.cjs` 直接 import 跑真实分支;另用
`tests/test_plugin_hooks.cjs` 按函数体文本锁定生命周期接线,其中含「不得含
`unregisterAll(`」的反向断言 —— 锁住「钩子该调哪个函数」这条发现。两个文件均登记进
`ci.yml` 两处显式清单(守卫清单与运行清单)。两条发现各自一个提交。

## 备选

- **备选 A:在 hooks.ts 里直接内联分组逻辑**。不选:TS + Zotero 全局无法用 `node --test`
  覆盖,等于放弃测试锚定。
- **备选 B:测试只断言 registry 层,不断言钩子接线**。不选:两条发现中有一条正是「钩子
  调了哪个函数」;不锁接线,该缺陷可以被原样改回而测试全绿。

## 后果

`tsconfig` 的 `include: ["src"]`(`allowJs` 默认开)自动收录 `.mjs`,类型由同目录的
`registryCore.d.mts` 提供。需注意:若日后删掉 `d.mts`,`tsc --noEmit` 会退化为 `any`
而不报错 —— 已在测试注释与 `plugin/AGENTS.md` 内提示「改语义要同时改测试」。
