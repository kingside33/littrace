# Windows 共享锁文件上的瞬时 PermissionError 按竞争重试,不作为错误上抛

- 日期:2026-11-22
- 状态:已接受

## 背景

#97 定位到并发评审锁等待路径上,`update_lock` 的锁文件打开与写入段
(`kbapp/core/locks.py` 原 11–15 行:`path.open('a+b')` 与 `f.flush()`)不在
`except OSError → ValueError` 保护段内。Windows 对新建共享文件的瞬时共享冲突
(实时扫描/过滤驱动短暂持有句柄)在该段抛 `PermissionError(13)`;
`request_lock`(`kbapp/research/review.py`)只捕获 `ValueError`,异常裸逃逸并
毁掉一次本可成功的评审。

仓库在 `kbapp/core/io.py` 已有同类约定(`read_json`/`jsonl`/`open_retry` 对
`PermissionError` 限次重试、`write_text` 对 `os.replace` 限次重试),唯独锁文件
的 open/write 漏了这层。

实现见 #123;ADR 落盘跟踪 issue:#122。

## 决策

1. `update_lock` 的锁文件打开改走 `open_retry`(12 次限次、递增退避),真权限
   问题重试耗尽后原样抛 `PermissionError`,不翻译、不吞掉。
2. 解锁(`LK_UNLCK`/`flock LOCK_UN`)失败按 `OSError` 吞掉并注释说明——字节锁
   随句柄关闭释放(Windows LockFile 与 POSIX flock 语义),锁归属不变,等待方
   0.1s 重试必然拿到锁;吞掉的只是「毁掉已完成工作」的逃逸路径。
3. `request_lock` 获取循环捕获 `(ValueError, PermissionError)` 一律按「忙」
   重试,沿用既有 180 秒上限与 `should_stop` 检查,超限抛
   `ValueError('核验请求队列仍忙，可续跑')`。真权限问题在该路径表现为 180 秒
   后的「队列忙」:可接受,因为数据目录真正不可写时 `Store` 构造阶段
   (`self.paths.state.mkdir`)先失败,走不到评审路径。

## 备选

- **只在测试层放宽等待策略。** 不选:生产路径同样裸逃逸(issue 影响面第 2 条),
  测试绿不等于生产对。
- **在 `update_lock` 里把 `PermissionError` 直接翻译成
  `ValueError('已有更新任务运行中')`。** 不选:对 update 工作流是撒谎,真权限
  问题会被报成「已有任务运行中」,误导排障。
- **更换锁实现(如 O_TEMPORARY、单一全局锁)。** 不选:改动面远超本 issue,
  且现有 msvcrt/flock 语义本身没有错。

## 后果

- Windows 上并发评审的瞬时共享冲突被自动吸收:单次代价 ≤ 0.1s 重试,
  `open_retry` 最长约 6.6s。
- 真权限问题仍显式浮出:update 工作流即时抛 `PermissionError`,评审路径
  180s 后抛「队列忙」。
- POSIX 行为不变(`flock` 语义与吞解锁异常的推论相同)。
- 测试面新增 `tests/test_review_lock.py` 4 个离线用例;解锁用例按平台各自原生
  执行,不使用 skip。
