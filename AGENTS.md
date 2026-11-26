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
- **评审纪律。** 每个 PR 由独立评审员做一次完整评审;独立评审由与实现无共享
  上下文、未参与实现的会话承担,可以是 AI 会话(含自动化编排调用的子代理
  会话);**评审发现一律以 PR
  行内评论(line comments)发表**,不散落在 PR 正文或会话记录里。实现员逐条
  修复、在对应线程回复处理说明并 resolve;合并前所有评审线程必须 resolved,
  由集成管理员复核。
- **评审会话规程。** 评审会话(与实现无共享上下文的独立会话)只评审、不改
  码:不 commit、不 push、不 resolve 评审线程。评审前先核对本地 worktree
  head 与 `gh pr view --json headRefOid` 一致,不一致立即停。发现以行内评论
  发表,带 `[P0]`/`[P1]`/`[P2]` 前缀与 path:line 证据,行号落在 diff 右侧
  新增行内;真实位置在未改行或不在 diff 内的发现(context-only),指向最近的
  diff 行并写明真实位置。裁决只有两种:有发现发 `--request-changes`,零发现发
  `--approve`(仍列出查过什么);评审正文只写计数与结论,绝不写发现内容,
  并实测 `gh pr checks`。评审账号是 PR 作者时 GitHub 拒绝这两种类型,降级为
  `--comment` 提交同样结论并注明原因(完整规程见 CONTRIBUTING.md「Independent
  review pass」)。
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

**新功能,或对既有功能的任何修改,一律撰写或修订 ADR:对应实现 PR 必须携带
DecisionRecord,不按「决策是否重要」裁量。** 机制选型、契约、流程、验收方法
等决策以 DecisionRecord 形式上报(标题 / 背景 / 决策 / 备选 / 后果),由每
批次的 ADR PR 写入 `docs/adr/NNNN-<slug>.md`:编号递增、定稿不改写、修订开
新 ADR 引用旧号;索引在 `docs/adr/README.md`。改动落入已有 ADR 覆盖的领域,
动手前先读那几份。

## 协作流程

每个功能一个 issue(正文自包含,读者不需要看任何仓库外材料)→ 从
origin/main 切分支、在独立 worktree(`.worktrees/<分支名>`)工作 → 门禁
真实跑过(看到退出码 0 才算过)→ 提 PR(正文第一段 `Closes #N`)→ 行内
评论评审、逐条解决 → squash 合并 → issue 自动关闭。评审、实现、集成由
不同会话承担(评审可以是 AI 会话,含自动化编排调用的子代理会话),实现不
评审自己的代码。

### 发现报错,先提 issue,再提 PR

**任何报错、缺陷、验收失败项,一律先提 issue,再提对应的修复 PR。** 这条
没有例外,适用于四种场景:开发中撞到的错误、测试或门禁失败、真机验收里的
失败项、事后发现的回归。

顺序不可颠倒,理由是可追溯性:issue 记录「谁发现的、什么现象、什么证据、
影响面多大」,PR 只是它的修复载体。只在 PR 描述里口头提一句缺陷,等于把
发现记录埋进修复记录里,事后无法按缺陷检索。

```sh
gh issue create --title "<缺陷>:<一句话现象>" --body-file -   # 现象/复现/证据/影响面
# ...修复后:
gh pr create --base main --body-file -                       # 正文第一段 Closes #N
```

- **先查重**:`gh issue list --state all --search "<关键词>"` 已有同源
  issue 就复用,不重复开单。
- **一次缺陷一个 PR**:修复 PR 只含该缺陷的改动,不夹带顺手重构或其他
  功能的改动(与上面「PR 范围」纪律一致);确实需要连带改动,在 issue 里
  写明理由。
- **验收失败项同样适用**:真机验收发现 fail,先开缺陷 issue,再开修复 PR,
  两个编号都回填到验收记录里。
- **报告问题不算提 issue**:口头/会话里说「这里好像有问题」不构成记录,
  必须落到 GitHub issue 才算。

### 错题本:教训先记录,重复三次才升级

犯过的错与值得照抄的做法记在 `docs/lessons.md`,格式与升级规则见
CONTRIBUTING.md 的「Lessons(错题本)」。动手改代码与评审之前,先读与本次
改动相关的条目。

- **只在独立 PR 里写**:功能 PR 不改 `docs/lessons.md`,避免并行 PR 撞车,
  也避免夹带无关文件。
- **三个不同日期才升级**:同一条目在 3 个不同日期出现后,把规则写进本文件、
  CONTRIBUTING.md 或新 ADR,条目压缩成一行指针。
- **规则要有证据**:`证据` 只写 issue 或 PR 编号,与本仓库「先提 issue 再提 PR」
  的纪律一致。
- **每周有周报**:`.github/workflows/lessons-review.yml` 每周一把到期升级与
  60 天未复发的条目汇总成 `lessons-review` issue;它不阻塞 PR,但到期条目会
  一直挂在 issue 上,处理仍走独立的 `docs(lessons)` PR。

## 验证入口(与 ci.yml 一致)

```sh
ruff check                                                    # python-lint
python -m pytest -m "not network and not model"               # pytest 离线套件(与 ci.yml 同款)
node --test <ci.yml 显式清单>                                  # node 测试
python scripts/check_lessons.py                               # 错题本格式与隐私
python kb.py doctor architecture                              # 架构守卫
cd plugin && npm run build                                    # 插件构建(plugin/ 存在后)
```

- 命令假定虚拟环境已激活(环境搭建见 CONTRIBUTING.md)。未激活时用显式
  解释器:Windows `.venv\Scripts\python.exe`,macOS/Linux `.venv/bin/python`。

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
| 错题本与教训升级 | docs/lessons.md |
| 平台/版本支持口径 | docs/support-matrix.json |
| 架构决策 | docs/adr/README.md |
| 安全 | SECURITY.md |
