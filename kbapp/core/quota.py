"""kbapp.core.quota：授权额度的常量与纯决策。

本模块是额度数字与拒绝语义的唯一出处：后续切片只导入这里的常量与函数，
不另写字面量。它是纯逻辑——无 I/O、无模块级可变状态、不写库、不抛业务
异常以外的错误；执行配额（拒绝任务、429、准入事务）不属于本模块。
"""
from __future__ import annotations
from kbapp.core.io import TZ, digest

PAPERS_PER_RUN = 20
REFERENCES_PER_PAPER = 10
VERIFIER_UNITS_PER_RUN = 20
ANALYSIS_TASKS_PER_DAY = 3
LIMIT_KEYS = ('papers_per_run', 'references_per_paper',
              'verifier_units_per_run', 'analysis_tasks_per_day')
REJECTING_QUOTAS = ('papers_per_run', 'verifier_units_per_run',
                    'analysis_tasks_per_day')
EVENT_KINDS = ('analysis_task',)

_RUN_LIMITS = {'papers_per_run': PAPERS_PER_RUN,
               'verifier_units_per_run': VERIFIER_UNITS_PER_RUN}


def limits(tier):
    """返回该 tier 的额度表；每次都是新 dict，调用方可随意修改。

    `pro` 的四个值为 None，语义是“不设上限”；其他任何值（含未知值）一律
    按 free 返回，fail-closed 到免费额度。
    """
    if tier == 'pro':
        return {key: None for key in LIMIT_KEYS}
    return {'papers_per_run': PAPERS_PER_RUN,
            'references_per_paper': REFERENCES_PER_PAPER,
            'verifier_units_per_run': VERIFIER_UNITS_PER_RUN,
            'analysis_tasks_per_day': ANALYSIS_TASKS_PER_DAY}


def exceeds(quota_id, tier, *, requested=None, used_today=0):
    """纯判定：该请求是否超出额度。不抛业务异常、不写库。

    `pro` 恒为 False；`analysis_tasks_per_day` 按当日已用数量判定；
    `references_per_paper` 是展开预算/报告字段，不是拒绝型配额，传进来
    属于调用方用错，明确抛 ValueError；未知键同样抛 ValueError。
    """
    if tier == 'pro':
        return False
    if quota_id in _RUN_LIMITS:
        if requested is None:
            raise ValueError(f'{quota_id} 需要 requested 才能判定')
        return requested > _RUN_LIMITS[quota_id]
    if quota_id == 'analysis_tasks_per_day':
        return used_today >= ANALYSIS_TASKS_PER_DAY
    if quota_id == 'references_per_paper':
        raise ValueError('references_per_paper 是展开预算/报告字段，不是拒绝型配额')
    raise ValueError(f'未知配额键：{quota_id}')


def day_of(moment):
    """把带时区的 datetime 转成北京自然日 YYYY-MM-DD。

    naive datetime 抛 ValueError：不做本机时区猜测，避免同一事件在不同机器
    落到不同的日子。
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('day_of 需要带时区的 datetime')
    return moment.astimezone(TZ).date().isoformat()


def event_id(day, kind, subject):
    """配额事件的确定性 id：只由 day/kind/subject 决定，进程间稳定。"""
    return digest(f'{day}|{kind}|{subject}')
