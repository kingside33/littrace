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
