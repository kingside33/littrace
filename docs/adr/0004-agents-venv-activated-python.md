# [AGENTS] 验证入口采用 venv 激活后的 python 单一写法,跨平台解释器路径由注释说明

- 日期:2026-11-18
- 状态:已接受

## 背景

`AGENTS.md` 初稿的验证入口硬编码了 Windows 专属的 `.venv/Scripts/python.exe`。评审指出
这与 `ci.yml` 的 `python -m pytest`(矩阵含 `ubuntu-24.04`)以及 `CONTRIBUTING.md` 里
macOS/Linux 用 `.venv/bin/python` 的写法都矛盾:非 Windows 会话逐字执行,会在跑任何
测试之前就失败在「找不到解释器」上,而失败原因与测试本身无关。

## 决策

改为 venv 激活后的统一定义 `python -m pytest ...` 与 `python kb.py doctor architecture`,
并在代码块之后用注释注明:未激活 venv 时,Windows 用 `.venv\Scripts\python.exe`,
macOS/Linux 用 `.venv/bin/python`。

## 备选

- **备选 A:并列 Windows 与 macOS/Linux 两套完整命令**。不选:两条同义命令会各自漂移,
  改了一条忘另一条是必然;跨平台差异只有解释器路径这一点,用一条注释说清就够。
- **备选 B:保留 Windows 专属路径,让其他平台自行适配**。不选:文档声称的是仓库的验证
  入口,而 CI 本身就跑两个平台,不能只对主力开发平台成立。

## 后果

文档命令与 `ci.yml` 同款,跨平台可用。未激活 venv 的读者需要回读注释去选显式解释器 ——
这一步成本远低于在错误解释器上排查一小时的「测试为什么没跑」。
