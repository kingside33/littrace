"""kbapp.research.methods：方法实体分层与别名冲突（F06）。

方法实体分为：方法族（family，来自方向分类）、具体算法、变体与组件。
别名映射只用于定位候选，不产生继承结论；同一别名指向多个实体时报告冲突。
"""
from __future__ import annotations
import json

VARIANT_HINTS = ('informed', 'adaptive', 'improved', 'enhanced', 'variant', 'generalized', 'extended', 'batch')
COMPONENT_HINTS = ('sampling', 'receding horizon', 'potential field', 'heuristic', 'controller', 'module', 'component', '组件', '模块')


def _concepts(store):
    with store.db() as db:
        return [dict(r) for r in db.execute('SELECT id,name,aliases,payload,manual FROM method_concepts')]


def classify(store):
    """返回 {method_id: {name, family, kind, aliases}}；kind ∈ family/algorithm/variant/component。"""
    concepts = _concepts(store)
    names = {row['id']: row['name'] for row in concepts}
    result = {}
    for row in concepts:
        payload = json.loads(row['payload'] or '{}')
        name = row['name'] or ''
        lowered = name.lower()
        base_match = [other for mid, other in names.items() if mid != row['id'] and other and other.lower() in lowered and other.lower() != lowered]
        if any(hint in lowered for hint in VARIANT_HINTS) or base_match:
            kind = 'variant'
        elif any(hint in lowered for hint in COMPONENT_HINTS):
            kind = 'component'
        elif payload.get('family'):
            kind = 'algorithm'
        else:
            kind = 'family'
        result[row['id']] = {'name': name, 'family': payload.get('family') or '',
                             'kind': kind, 'aliases': json.loads(row['aliases'] or '[]'),
                             'manual': bool(row['manual'])}
    return result


def alias_conflicts(store):
    """同一别名指向多个方法实体时返回冲突列表（人工处理，不自动合并）。"""
    index = {}
    for row in _concepts(store):
        aliases = json.loads(row['aliases'] or '[]') or [row['name']]
        for alias in aliases:
            key = (alias or '').strip().casefold()
            if key:
                index.setdefault(key, set()).add(row['id'])
    return [{'alias': alias, 'methods': sorted(ids)} for alias, ids in sorted(index.items()) if len(ids) > 1]
