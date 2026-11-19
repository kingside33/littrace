# 架构决策记录(ADR)

本目录记录 littrace 的重要架构决策:机制选型、契约、流程与验收方法。

## 编号纪律

- 一个决策一个文件,命名 `NNNN-<slug>.md`,`NNNN` 从 0001 起递增,不复用、
  不跳号占位。
- **定稿不改写**:ADR 合并后不再修改正文;推翻或修订时新开一个 ADR,并在
  正文里引用被取代的旧编号,由新 ADR 标注旧 ADR 的状态。
- DecisionRecord 在实现 PR 里上报(标题 / 背景 / 决策 / 备选 / 后果),由
  每批次的 ADR PR 落盘到本目录并更新下面的索引表。

## 索引

| 编号 | 标题 | 一句话 |
| --- | --- | --- |
| [0001](0001-test-skip-platform-capability.md) | [C1] 平台能力缺失用 skip 表达 | 生产代码本就限定 Windows 的能力,在测试层如实用 `skipif` 标注,不改生产代码 |
| [0002](0002-path-assertion-compare-resolved-path.md) | [C1] 路径断言比较 resolve() 后的 Path | 同一文件的短名/长名差异应在比较前规范化,而不是比字符串拼写 |
| [0003](0003-agents-no-hardcoded-node-test-list.md) | [AGENTS] 不硬编码 node 测试清单 | 验证入口引用 ci.yml 两处显式清单,清单漂移由守卫检查挡住 |
| [0004](0004-agents-venv-activated-python.md) | [AGENTS] 验证入口用 venv 后的 python | 跨平台差异只在解释器路径,用一条注释说清而不是并列两套命令 |
| [0005](0005-adr-index-three-column-skeleton.md) | [AGENTS] ADR 目录以三列空索引表起步 | 先立编号纪律,不造示例 ADR、不预填索引行 |
| [0006](0006-version-endpoint-public-get.md) | [S3] 版本协商用公开 GET /api/version | 与 /api/session 同级不新增鉴权,一次 GET 完成存在性与版本判断 |
| [0007](0007-service-version-single-source.md) | [S3] service_version 以 pyproject 为唯一出处 | 元数据优先 + 同源兜底,不设代码内版本常量以免双出处漂移 |
| [0008](0008-version-test-asserts-pyproject-fallback.md) | [S3] 版本测试断言 pyproject 兜底值 | 免安装模式下同样成立,两种受支持运行模式都被覆盖 |
| [0009](0009-registry-keyed-by-window.md) | [A1] 注册簿按窗口分键 | 同一 id 每窗口各注册一次,纯逻辑抽到 registryCore.mjs 供 node 测试 |
| [0010](0010-two-findings-separate-commits-test-anchors.md) | [A1] 两个发现独立提交各带测试锚定 | registry 层跑真实逻辑,钩子接线按函数体文本锁定,均登记 ci.yml |
| [0011](0011-a2-column-registration-in-register-window-independent.md) | [A2] 列注册并入已有的 registerWindowIndependent() | 进程级注册只保留一个入口与一个防重入标志,不新增 registerGlobalAll |
| [0012](0012-a2-badge-number-is-confirmed-count.md) | [A2] 徽标旁的数字恒为已确认关系数 | 数字与三态徽标解耦,状态只由字形表达、明细由 title 给出 |
| [0013](0013-a2-non-item-rows-no-badge.md) | [A2] 非条目行不给徽标、不派发定位载荷 | 按 _objectType 判定条目,回收站里的收藏夹与保存搜索不算条目 |
| [0014](0014-a3-paint-stream-replace-children.md) | [A3] 卡片流重画整体替换子节点列表 | 卡片按 id 复用,进度行与空态提示每轮重画、旧的自然消失 |
| [0015](0015-a3-prune-detached-mounts.md) | [A3] 用 isConnected 兜底回收已关闭阅读器的面板 | 不依赖未定案的 reader 关闭事件名,打开面板或禁用时清扫 |
| [0016](0016-a3-settle-clear-timer-first.md) | [A3] settle() 先清飞行中标记再构造结果 | 异常转成可重试失败卡片,面板不会卡在 busy |
| [0017](0017-a3-refuse-question-visible-notice.md) | [A3] 拒绝新提问时给出可见提示 | 输入不被静默吞掉,提交按钮置灰并说明原因 |
| [0018](0018-s4-no-auto-retry-request-discipline.md) | [S4] 请求显式 errorDelayMax: 0 + timeout: 10000 | 本地确定性端点不自动重试,重试只由用户显式触发 |
| [0019](0019-s4-transport-non-2xx-as-response.md) | [S4] 传输层把非 2xx 当响应返回 | 只有无响应才是 status 0,错误分类成为可测试的纯函数 |
| [0020](0020-s4-idless-submit-is-outcome.md) | [S4] 无任务 ID 的提交是结论而非错误 | submitTask 返回带 outcome 的判别式结论,不再把成功报成失败 |
| [0021](0021-s4-requirements-missing-vs-null.md) | [S4] requirements 区分「未提供」与「显式 null」 | 缺键取默认、null 关闭边界、其他类型抛错 |
| [0022](0022-u1-v2-paper-readonly-refresh-references.md) | [U1] v2 文献详情默认不排队,refresh_references 显式 opt-in | 只读端点不得写 reference_jobs,v1 契约保持不变 |
| [0023](0023-u1-bounded-column-request-queue.md) | [U1] 列请求经固定上限 6 的有界队列发出 | 排序逐行取数不得对单进程服务抛出全库并发 |
| [0024](0024-u1-repaint-strategy-sort-vs-refresh.md) | [U1] 重绘按当前排序列选择重排或刷新 | 只在本列为排序字段时全量 re-sort,避免已有行永不重排 |
| [0025](0025-u1-merge-main-to-restore-pr-ci.md) | [U1] 合入 main 解除冲突以恢复 PR CI | 冲突时 CI 静默不跑,不能当作已跑门禁 |
| [0026](0026-u2-merge-not-rebase-preserve-hashes.md) | [U2] 用 merge 而非 rebase 合入已前进的 main | 保住所引用修复提交的哈希,评审可回溯 |
| [0027](0027-u2-review-findings-test-anchors-verified-red.md) | [U2] 每条评审发现配可脱机锚定并验证会红 | 特权上下文不写假单测,用文本锁定 + 行为断言 |
| [0028](0028-u4-locate-identity-requires-library-id.md) | [U4] 谱系定位用 `z:<libraryID>:<key>`,缺库 ID 则拒绝 | 裸 Zotero key 解析不到,猜默认库会错定位 |
| [0029](0029-u4-host-liveness-pure-verdict-dialog-dual-listeners.md) | [U4] 宿主存活判定抽成纯函数,dialog 双监听 | 未知信号按存活处理,pagehide + unload 双保险 |
| [0030](0030-u3-answer-via-async-agent-run-polling.md) | [U3] 问答走异步 agent-run + 轮询 | 同步接口必然超 10 秒且无中间事件,异步两段式才能给出单调进度 |
| [0031](0031-u3-missing-cloud-key-guide-card-no-retry.md) | [U3] 云 key 缺失只给引导卡片、不重试 | 不创建注定失败的任务,引导卡片的 retry 恒为 null |
| [0032](0032-u3-deep-link-trust-server-same-paper-only.md) | [U3] 跳页只信服务端深链与同篇附件 key | 跨文献猜附件 key 会打开另一篇 PDF,不可点就不渲染成按钮 |
| [0033](0033-u3-lineage-reuse-graph-endpoint.md) | [U3] 「问谱系」复用 GET /api/graph,缺标识即拒绝 | 空 paper 返回全库节点,像答案实非答案,因此直接拒绝 |
| [0034](0034-u3-gap-hint-by-cause.md) | [U3] 缺口卡片按成因给提示 | warning 与截断不是「无证据」,共用文案会否定上方的证据 |
| [0035](0035-u5-client-origin-pure-verdict.md) | [U5] 端口变更以纯函数判定是否重建客户端 | 任何 origin 差异即重建,令牌随旧客户端丢弃 |
| [0036](0036-u5-last-sync-by-state-not-timestamp.md) | [U5] 上次同步按 export_sync 状态判定 | 三次写入都刷新 updated,只看时间戳会把失败读成成功 |
| [0039](0039-ci-fast-lane-full-ci-label.md) | [CI] PR 默认快线,重型全量由 full-ci 标签或 merge_group 触发 | 普通 PR 只跑快线,full-ci/队列才跑重型,聚合 gate 判 deferred |
| [0040](0040-merge-queue-gate-before-queue.md) | [CI] 合并队列按「Gate 先于队列」启用 | 个人账号仓库暂不可用,先落 merge_group 触发与 gate,转组织后再启用 |
