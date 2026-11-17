"""kbapp.workflows.states：父任务、阶段与子单元的统一状态语义（I01）。

状态词表（三者共用）：
- queued    已登记，等待执行
- running   正在执行
- stopping  已请求停止，在途单元收尾中
- complete  全部完成且结果已保存
- partial   部分完成；有结果保留，但有失败/部分完成/证据缺口
- blocked   无法继续：依赖失败/受阻，或等待审批、额度、外部条件；需明确续跑或处理
- failed    不可恢复失败（致命错误或全部单元失败），结果已尽量保存
- interrupted 被停止/进程退出；结果保留，可续跑

聚合规则（确定性，供 settle 与界面解释共用）：
- 有 running → running；有 queued → queued
- 全部 complete → complete
- 非完成项全部为 blocked → blocked（等待处理，不是失败）
- 非完成项全部为 failed 且无 blocked → failed
- 其余（complete 与 failed/partial/blocked 混合）→ partial
"""
from __future__ import annotations

STATES = ('queued', 'running', 'stopping', 'complete', 'partial', 'blocked', 'failed', 'interrupted')
TERMINAL = {'complete', 'partial', 'blocked', 'failed', 'interrupted'}
ACTIVE = {'queued', 'running', 'stopping'}

EXPLAIN = {
    'queued': '等待执行',
    'running': '正在执行',
    'stopping': '正在停止，在途单元收尾',
    'complete': '全部完成',
    'partial': '部分完成；结果已保存，仍有失败或证据缺口',
    'blocked': '受阻待处理：依赖失败或等待审批、额度与外部条件',
    'failed': '执行失败；已保存的结果保留，可按记录排查后续跑',
    'interrupted': '已停止或进程退出；结果保留，可续跑',
}


def explain(state):
    return EXPLAIN.get(state, state)


def aggregate(counts, running=0, stopped=False, halt_error=False):
    """按状态计数汇总；counts 为 {状态: 数量}。"""
    total = sum(counts.values())
    if stopped:
        return 'stopping' if running else ('failed' if halt_error else 'interrupted')
    if running or counts.get('running'):
        return 'running'
    if counts.get('queued'):
        return 'queued'
    if not total:
        return 'queued'
    if counts.get('complete', 0) == total:
        return 'complete'
    remaining = {state: count for state, count in counts.items() if state != 'complete' and count}
    if remaining and all(state == 'blocked' for state in remaining):
        return 'blocked'
    if remaining and all(state == 'failed' for state in remaining) and not counts.get('complete'):
        return 'failed'
    return 'partial'


def stage_status(counts):
    return aggregate(counts)


def run_status(counts, running_units=0, stopped=False, halt_error=False):
    return aggregate(counts, running_units, stopped, halt_error)
