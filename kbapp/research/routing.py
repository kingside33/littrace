"""kbapp.research.routing：云端复核路由条件与本地拒绝抽样审计（G07/G08）。

第一阶段仍要求自动发布的正式方法关系通过云端复核；路由原因用于记录
“为什么进入云端”，并为后续按比例减少云端调用提供可测量依据。
"""
from __future__ import annotations
import json
from kbapp.core.io import digest

METHOD_KINDS = {'inherits', 'improves', 'combines', 'transfers'}


def route_reasons(claim, local_state, relation=False, context_tokens=None, budget=None):
    """返回进入云端复核的原因列表；空列表表示仅抽样审计。"""
    reasons = []
    if relation and claim.get('kind') in METHOD_KINDS:
        reasons.append('核心方法关系')
    if local_state in {'contradicted', 'conflict', 'execution_error'}:
        reasons.append('本地判断分歧或执行失败')
    if claim.get('numbers'):
        reasons.append('复杂数字与条件')
    if context_tokens and budget and context_tokens > budget * 0.8:
        reasons.append('长距离证据')
    if not reasons:
        reasons.append('抽样审计')
    return reasons


def _local_origin(row, payload):
    origin = payload.get('origin')
    if origin is not None:
        return origin == 'local'
    key = row['cache_key']
    # Legacy keys were either readable prefixes or sha256('local:' + fingerprint).
    return key.startswith('local:') or key == digest('local:' + row['fingerprint'])


def _audit_candidate(row, payload, rate):
    if not isinstance(payload, dict) or row['status'] != 'complete' or not _local_origin(row, payload):
        return None
    verdict = payload.get('verdict') or {}
    if not isinstance(verdict, dict) or verdict.get('supported') is not False or verdict.get('execution_error'):
        return None
    if verdict.get('decision') not in {None, 'insufficient', 'contradicted'}:
        return None
    bucket = int(digest(row['subject'])[:4], 16) % 100
    if bucket >= rate * 100:
        return None
    return dict(subject=row['subject'], local_reason=verdict.get('reason', ''), bucket=bucket,
                cache_key=row['cache_key'], fingerprint=row['fingerprint'])


def sample_for_audit(store, rate=0.1, limit=20):
    """Sample only positively attributable, completed local rejections."""
    if not 0 < rate <= 1:
        raise ValueError('抽样比例应在 0 到 1 之间')
    sampled = []
    with store.db() as db:
        rows = db.execute('SELECT cache_key,subject,fingerprint,status,payload FROM verification_attempts ORDER BY subject,cache_key').fetchall()
    for row in rows:
        try:
            candidate = _audit_candidate(row, json.loads(row['payload']), rate)
        except (ValueError, TypeError):
            continue
        if candidate:
            sampled.append(candidate)
    return sampled[:max(0, limit)]


def enqueue_local_audit(store, key, subject, fingerprint, payload, rate=0.1):
    """Persist a pending ResearchArtifact, not a cloud call or publication decision."""
    row = dict(cache_key=key, subject=subject, fingerprint=fingerprint, status='complete')
    candidate = _audit_candidate(row, payload, rate)
    if not candidate:
        return None
    from kbapp.research.evidence import artifact, latest
    # One queue item per immutable attempt; don't reset completed audits on cache hits.
    prior = latest(store, 'LocalReviewAudit', key)
    if prior:
        return prior
    return artifact(store, 'LocalReviewAudit', key, fingerprint,
                    dict(candidate, origin='local', review_subject=subject, claim=payload.get('claim'),
                         contexts=payload.get('contexts'), relation=payload.get('relation', False),
                         local_verdict=payload.get('verdict'), audit_rate=rate), 'pending')
