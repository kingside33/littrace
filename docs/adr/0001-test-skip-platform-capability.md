# [C1] 平台能力缺失用 skip 表达,而不是把被测能力改成跨平台实现

- 日期:2026-11-18
- 状态:已接受

## 背景

CI 的两条 Ubuntu 流水线上有 29 个用例僵死红灯。排查后根因不在用例本身:生产代码里
DPAPI 密钥保护本就只在 Windows 提供(`kbapp/core/secrets.py:10-11` 在非 Windows 上
显式 `raise RuntimeError`),运行时身份判断同样只在 Windows 实现
(`kbapp/integrations/models.py` 的 `_runtime_owned`,非 Windows 直接 `return False`)。
也就是说,这些用例断言的行为在 Linux 上按设计不存在,不是用例写错。

于是要在两条路之间选:在测试层如实表达「本平台不具备该能力」,还是改动生产代码让
能力变成跨平台,从而使用例变成平台无关。

## 决策

只在测试层加 `pytest.mark.skipif(os.name != 'nt', reason=...)`,理由文案点名具体生产
代码位置。类级用于 `tests/test_cloud_failures.py` 的 `CloudFailures` 整类;方法级用于
`tests/test_workbench.py` 的 17 个用例(该类另有 `DPAPI_REQUIRED` 与 `WINDOWS_ONLY`
两个标记,分别对应密钥保护与运行时身份判断两种缺失)。`kbapp/**` 一字未改。

## 备选

- **备选 A:在 `kbapp/core/secrets.py` 里提供一个非 Windows 的伪加密实现**,把用例改成
  平台无关。不选:Windows 上真实的 DPAPI 覆盖会被伪实现静默顶替,测试仍然绿但测的不再是
  生产路径;同时把被守护的行为边界改了 —— 非 Windows 上密钥明文落盘这种后果没人愿意认领。
- **备选 B:删掉这些用例或改成 `xfail`**。不选:骗过了 CI,但回归保护一并丢失,日后
  Windows 上这段逻辑真的坏掉不会有人发现。
- **备选 C:只跳过失败的断言而不是整个用例**。不选:会留下跑一半的用例,失败原因难以
  解释,且后半段断言的数据前置条件已不成立。

## 后果

Linux 上这 29 个用例明确 skip,`-rs` 输出里可读到跳过原因;Windows 上仍 455 个用例
全跑、0 skipped,守护强度不变。代价是 Linux 侧这些路径的真实覆盖为零 —— 要恢复覆盖,
需要先有 platform adapter,已作为后续排期事项而非本次修补内容。
