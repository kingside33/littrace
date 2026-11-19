# [S7] 探测端点复用 open_cloud、三态 verdict、预算严格小于客户端超时

- 日期:2026-11-19
- 状态:已接受

## 背景

用户要在**启用**云端调用之前确认「地址、模型、密钥」这套配置能不能用。
`POST /api/settings` 只做静态校验(字段非空、地址形态),证明不了端点可达、
密钥被接受;唯一能验证的方式是启用后跑一次真实分析,失败时只拿到一句笼统的
「云端调用未完成」。

新增 `POST /api/settings/test`(`kbapp/web/system_api.py:74`)做零成本探测:
`GET {api_base}/models`,不 reserve、不 settle、不写 `usage`、不写
`last_model_error`,也不要求 `cloud_enabled=true`。

## 决策

四条契约缺一不可:

- **同步 `def`,不是 `async def`**。单进程 uvicorn 下同步端点由 starlette 丢
  线程池,探测的阻塞只占一个线程,不卡插件每 30 秒一次的 `/api/version` 轮询;
  `async def` 包裹阻塞 IO 会卡死事件循环。`/api/ask` 是同一先例。
- **不接收请求体**,一律探测已保存的配置。`save_settings` 的 HTTPS/loopback
  限制是唯一的 URL 校验路径,从这里再收一个地址就多开一条把 Bearer 密钥发往
  任意地址的入口。
- **复用 `open_cloud`,不走裸 `urlopen`**。`cloud_proxy` 的用途就是让被墙的直连
  变可用;探测绕开它会出现最坏的「测试失败但实际可用」,把用户推向修改一个
  本来正确的配置。
- **预算 `PROBE_TIMEOUT_S = 5`(`cloud.py:26`)是一次请求的总预算**,严格小于
  插件客户端的 `REQUEST_DISCIPLINE.timeout = 10000`(`serviceClientCore.mjs`,
  ADR 0018)。两者相等时客户端先超时报「无法连接本地服务」,探测的全部价值
  归零。

结论用**三态** `verdict`(`ok` / `unsupported` / `failed`)而非布尔:2xx 只证明
「可达 + 鉴权被接受」,不能证明配置的模型可推理,也覆盖不了不实现 `/models`
的网关。布尔 `ok` 必然在其中一端说谎。

对外的 `code` 用显式映射表(`cloud.py:73` 的 `probe_code`),不直接透传
`failure_details` 的输出——后者是异常分类器,产出
`{response,http,dns,tls_certificate,tls,timeout,connection}`,与对外词表不是一套。

## 备选

- **裸 `urlopen`**:得出与真实调用相反的结论,否决。
- **布尔 `ok`**:无法区分「端点不提供 `/models`」与「端点不可用」,否决。
- **`async def`**:阻塞事件循环,否决。
- **代理腿与回退腿各给 5s**:最坏 2×5s=10.03s,正好撞上客户端超时,与它想
  避免的失效路径相同;改为整请求一个 deadline,回退腿只拿剩余时间。

## 后果

探测不能证明模型可推理——这条诚实边界写进了 `docs/support-matrix.json` 的
`cloud.note`。

§「复用 `open_cloud`」带来的一个硬约束:`RequestSendGuard.connect()` 曾无条件
`sock.settimeout(150)`,会覆盖调用方给 `opener.open(timeout=…)` 的预算,于是
「配了代理但代理半死」时探测挂 150 秒、5 秒承诺形同虚设。预算现在经
`attempt['read_timeout']` 传进守卫(`cloud.py:247`),不传时仍是 150,既有
`complete()` 行为不变。测试用直接驱动 `connect()` 的用例钉住,并做过变异验证。
