# [S3] service_version 以 pyproject [project].version 为唯一出处

- 日期:2026-11-18
- 状态:已接受

## 背景

版本号若同时存在于 `pyproject.toml` 与代码常量,发版时几乎必然漂移,而漂移只在线上
才被发现。仓库还明确支持「未执行 `pip install -e .` 时直接跑测试」(`pyproject.toml`
的 `pythonpath = ["."]`,注释写明该用途)—— 这个模式下没有包元数据可用,任何只读元数据
的实现都会在该模式下抛异常。

## 决策

`service_version()` 优先 `importlib.metadata.version('littrace')`;捕获
`PackageNotFoundError` 时兜底解析同一个 `pyproject.toml` 的 `[project].version`。
两种路径都返回 `0.1.0`,不引入第二份版本常量,并在 docstring 写明唯一出处就是
`pyproject.toml`。兜底解析不到时抛 `RuntimeError` 而非静默返回空串。

## 备选

- **备选 A:代码内设 `SERVICE_VERSION` 常量**。不选:双出处,与要解决的问题本身同构。
- **备选 B:只读包元数据**。不选:免安装测试与源码直跑时会直接抛
  `PackageNotFoundError`,而仓库明确声明支持这两种模式。
- **备选 C:只解析 pyproject**。不选:已安装 wheel 的环境可能没有该文件,生产部署会挂。

组合方案(元数据优先 + 同源兜底)是唯一同时满足已安装与未安装两种受支持模式的选项。

## 后果

单一出处消除了版本漂移。代价是两条读取路径都必须被测试覆盖 —— 只测其中一条,另一条
回归时 CI 仍会绿。因此补了免安装模式的兜底用例,并经变异测试验证:删掉兜底分支该用例
即失败。
