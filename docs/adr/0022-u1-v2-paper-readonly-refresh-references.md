# [U1] 「只读」不能只靠表述:给 GET /api/v2/paper 加默认关闭的 refresh_references,保留 v1 契约

- 日期:2026-11-18
- 状态:已接受

## 背景

`GET /api/v2/paper` 与 v1 共用 `paper_view`(`kbapp/web/library_api.py:9`),后者在
末尾调用 `references.view(store, pid, auto=refresh_references)`
(`kbapp/web/library_api.py:29`)。`auto=True` 时 `view` 内部走 `enqueue()`,写入
`reference_jobs`;服务默认开启 background jobs 时,worker 会进而发起外部 OpenAlex
请求并回写服务库。

于是「只读」只停留在文档表述上:条目列按行读取该端点,浏览一次列表就等于为整库排队
外部抓取。而 v1「详情页打开即排队」是已发布契约,并有现存测试钉住
(`tests/test_references.py:146` 断言 `GET /api/paper` 后 job 为 `queued`),不能直接
把 `auto` 关掉。

## 决策

`paper_view` 增加 `refresh_references` 参数(默认 `True`,保持 v1 语义);
`GET /api/v2/paper` 新增同名查询参数并默认 `False`
(`kbapp/web/v2_api.py:54`)。v2 默认不写任何服务端任务、不触发外部请求;显式传
`refresh_references=true` 才恢复 v1 行为。条目列只调 v2 默认路径。

## 备选

- **备选 A:另开一个无副作用的新端点**。不选:服务端多一套接口与契约,而列需要的
  `relations` / `understanding` 已经在本端点里,重复维护成本远大于一个布尔参数。
- **备选 B:直接改掉 v1 的行为**。不选:会破坏已发布契约并推翻现有测试,超出本 PR
  范围。

## 后果

v2 与 v1 在这一点上语义不同,已写入两处 docstring 与 PR 正文;任何想要 v1 行为的
调用方必须显式 opt-in,不会静默退化。新增 pytest 用例同时钉住两条路径:默认调用后
`reference_jobs` 不变且 `references.job.status` 为 `cached`,opt-in 后为 `queued`
(`tests/test_api_v2.py:125`)。
