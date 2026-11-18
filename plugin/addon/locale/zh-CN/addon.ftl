# littrace 的 Fluent 文案。构建时本文件改名为 littrace-addon.ftl,
# 且每个 key 都会加 "littrace-" 前缀;插件代码里查 key 时不带前缀。
startup-begin = littrace 加载中
startup-finish = littrace 已就绪

# 阅读器「证据问答」面板(A3 原型)。全部内容为静态假数据。
reader-panel-title = 证据问答
reader-panel-demo-note = 原型:内容为静态假数据,不连接本地服务
reader-panel-placeholder = 就这篇文章提问
reader-panel-ask = 提问
reader-panel-example = 例如:这个方法在哪些实验条件下成立?
reader-panel-wait = 上一问还在进行,完成后再提交
reader-panel-empty = 还没有提问。输入一个问题,或选中正文后用「查证据」。
reader-panel-stage-searching = 检索证据
reader-panel-stage-verifying = 核验结论
reader-panel-gap = 缺口
reader-panel-gap-hint = 这一问没有可定位页码的原文证据,只保留问题本身。
reader-panel-evidence = 证据原文
reader-panel-failure = 本轮未完成
reader-panel-retry = 重试
reader-panel-toolbar-entry = 证据问答
reader-panel-close = 关闭证据问答
reader-selection-evidence = 查证据
reader-selection-lineage = 问谱系

# 四个 iframe 功能 tab 的标题(U4)。tab 注册表引用这些 key,标题本身只在此处维护。
tab-title-graph = 研究谱系
tab-title-search = 证据检索
tab-title-research = 研究调度台
tab-title-approvals = 全文审批

# 条目面板三 section(U2):证据 / 谱系关系 / 精读。
# l10nID 与 t() 都带构建加上的 littrace- 前缀;这里写不带前缀的 key。
section-evidence = 证据
section-lineage = 谱系关系
section-reading = 精读
section-evidence-tooltip = 证据:已索引的段落、图表与公式
section-lineage-tooltip = 谱系关系:已确认的方法关系
section-reading-tooltip = 精读:覆盖度与页分类
section-unindexed = 这篇文献还没有可检索的证据
section-open-search = 去检索
section-no-relations = 暂无关系
section-open-lineage = 查看谱系
section-unparsed = 尚未解析
section-error = 读取失败
section-retry = 重试
section-offline = 需要本地服务
section-offline-guide = littrace 本地服务没有运行,启动后自动恢复;本面板不会给出半成品数据。
section-parse = 解析此文献
section-parse-submitting = 正在提交…
section-parse-queued = 已加入解析队列
section-parse-unknown = 服务未确认任务,请稍后查看任务列表
section-direction-previous = 前作
section-direction-following = 后作
section-direction-unknown = 年份待核
section-lineage-pending = 待核
section-lineage-citations = 引用事实
section-reading-state-unparsed = 尚未精读
section-reading-state-partial = 部分精读
section-reading-state-complete = 精读完成
section-reading-state-stale = 版本变化,待重读
evidence-kind-paper_text = 段落
evidence-kind-figure = 图表
evidence-kind-formula = 公式
evidence-kind-metadata = 元数据
evidence-kind-external_abstract = 外部摘要
reading-class-NATIVE_TEXT = 原生文本
reading-class-SCANNED = 扫描页
reading-class-MIXED = 混合页
reading-class-LOW_QUALITY = 低质页
reading-class-FAILED = 解析失败
