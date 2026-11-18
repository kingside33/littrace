# littrace — 仓库级规则

本文件是每次会话(AI 或人)动键盘之前都要读的那一份,只放协作纪律与仓库
不变量。各主题全文放在别处:贡献流程与红线在 CONTRIBUTING.md,架构决策在
`docs/adr/`,安全在 SECURITY.md。两份文件说法不一致时,以更严格的一方为准。

## 最重要的纪律:PR 范围

**一个 PR 只包含其对应 issue 的一个功能;严禁夹带无关文件与本机私有内容。**

提 PR 前必须自查文件清单,逐行确认:

```sh
git status --porcelain
git diff origin/main...HEAD --stat
```

严禁夹带:本机私有内容(本机 Zotero 数据与个人配置、规划文档
`docs/zotero-plugin-*.md`、实验脚本、临时产物)、`.worktrees/`、`.tmp-*`,
以及任何与本 issue 无关的文件。发现夹带,先移出提交,再请求评审。

## 不可破坏的不变量

- **绝不写回 Zotero。** `ro_connect` 以 `?mode=ro` 打开 `zotero.sqlite`,先
  拷贝进内存再读(`kbapp/integrations/zotero.py:11-21`)。Zotero 是外部只读
  输入:插件与服务对 Zotero 库只读,任何写路径、锁或 schema 改动一律退回
  (CONTRIBUTING.md「Things that will get a PR sent back」)。
- **AI 产出必须带测试锚定才可合入。** 纯逻辑进 `tests/test_*.cjs` 并加进
  ci.yml 的显式清单;服务端逻辑进 pytest 离线套件(`tests/test_*.py`,不依赖
  网络与模型)。特权上下文与真机行为不写假单测,进真机验收清单。
- **评审纪律。** 每个 PR 由独立评审员做一次完整评审;**评审发现一律以 PR
  行内评论(line comments)发表**,不散落在 PR 正文或会话记录里。实现员逐条
  修复、在对应线程回复处理说明并 resolve;合并前所有评审线程必须 resolved,
  由集成管理员复核。
- **提交纪律。** `git commit -s`(DCO 硬门禁,CI 读 `Signed-off-by` trailer);
  不加 Co-Authored-By 尾注;提交信息用 `type(scope): 中文标题`。squash 合并
  时确认 trailer 保留(CONTRIBUTING.md「Pull requests」节的「Merge method」小节)。
- **措辞纪律。** 插件壳(XPI 及其原生集成层)永久免费且 AGPL 开源;
  「编排层」一词专指 M3 商业编排层(收费物),不得出现在免费承诺里。
  免费功能的可用性依赖本地伴随服务:服务离线时入口明确禁用并给引导,
  绝不静默失败,绝不半功能状态。
- **许可证与口径。** AGPL-3.0-only。`docs/support-matrix.json` 是平台/版本
  支持口径的唯一出处,README 只引用不改写。
- **唯一入口与唯一落盘。** `kb.py` 是唯一命令入口,不加旁路;一切可写路径走
  `kbapp/core/paths.py` 落到数据目录,运行时不写仓库根、包目录或 Zotero 目录。

## 架构决策纪律

重要决策(机制选型、契约、流程、验收方法)在实现 PR 里以 DecisionRecord
形式上报(标题 / 背景 / 决策 / 备选 / 后果),由每批次的 ADR PR 写入
`docs/adr/NNNN-<slug>.md`:编号递增、定稿不改写、修订开新 ADR 引用旧号;
索引在 `docs/adr/README.md`。改动落入已有 ADR 覆盖的领域,动手前先读那几份。

## 协作流程

每个功能一个 issue(正文自包含,读者不需要看任何仓库外材料)→ 从
origin/main 切分支、在独立 worktree(`.worktrees/<分支名>`)工作 → 门禁
真实跑过(看到退出码 0 才算过)→ 提 PR(正文第一段 `Closes #N`)→ 行内
评论评审、逐条解决 → squash 合并 → issue 自动关闭。评审、实现、集成由
不同会话承担,实现不评审自己的代码。

## 验证入口(与 ci.yml 一致)

```sh
ruff check                                                    # python-lint
.venv/Scripts/python.exe -m pytest -m "not network and not model"  # pytest 离线套件
node --test <ci.yml 显式清单>                                  # node 测试
.venv/Scripts/python.exe kb.py doctor architecture            # 架构守卫
cd plugin && npm run build                                    # 插件构建(plugin/ 存在后)
```

- `node --test` 用 ci.yml 里的显式清单,不用目录 glob;新增 `tests/test_*.cjs`
  时同步 ci.yml 的两处(守卫清单与运行清单),守卫会挡住漏登记。
- 真机验收:见 `docs/adr/` 的验收方法 ADR 与对应真机验收 issue。门禁绿
  不代表真机绿。
- 在 PR 里说「全绿」之前,确认每条命令自己跑过并看到退出码 0。

## 会话卫生(多会话并行)

- 主工作区只做 fetch/pull 与只读检查:不切分支、不提交、不改工作树。
  分支工作一律进独立 worktree:
  `git worktree add .worktrees/<分支名> -b <分支> origin/main`。
- push 写明 refspec:`git push origin HEAD:<分支>`。
- 会话开始核对 `git config user.name` / `user.email`(身份错了全程零提示)。
- 结束进程只按自己启动的 PID;禁用 `pkill -f` 这类按模式匹配的杀法。

## 本机排除

`.gitignore` 只放对所有人适用的条目。个人私有内容用本机排除手段保持不入库
(如 `.git/info/exclude`,该文件在 `.git` 内,永远不会被提交):规划文档
`docs/zotero-plugin-*.md` 属于此类,已本机排除,严禁进提交。

## 索引

| 主题 | 文件 |
| --- | --- |
| 贡献流程、红线全文、DCO | CONTRIBUTING.md |
| 平台/版本支持口径 | docs/support-matrix.json |
| 架构决策 | docs/adr/README.md |
| 安全 | SECURITY.md |
