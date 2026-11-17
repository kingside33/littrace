"""kbapp.research.viewmodels：由职责边界组织的知识库能力。"""
METHOD_KINDS = {'inherits', 'improves', 'combines', 'transfers'}

def relation_grade(edge):
    if edge.get('kind') == 'cites' and edge.get('status') == 'confirmed':
        return 'citation'
    if edge.get('evidence_stale'):
        return 'title'
    if (edge.get('manual') or edge.get('level') == 'human') and edge.get('status') == 'confirmed':
        return 'fulltext'
    if edge.get('pipeline_legacy'):
        return 'title'
    if edge.get('status') == 'confirmed' and edge.get('level') == 'fulltext' and edge.get('reviewed_by') and edge.get('evidence'):
        return 'fulltext'
    if edge.get('level') == 'abstract' and edge.get('assessment_verified') and edge.get('evidence'):
        return 'abstract'
    return 'title'

def edge_direction(edge, papers=None):
    """F07：统一前作/后作方向；旧 src/dst 保持原样，同年或年份缺失时标记待核。"""
    papers = papers or {}
    src_year = (papers.get(edge.get('src')) or {}).get('year') if isinstance(papers, dict) else None
    dst_year = (papers.get(edge.get('dst')) or {}).get('year') if isinstance(papers, dict) else None
    if not isinstance(src_year, int) or not isinstance(dst_year, int):
        return 'unknown_year'
    if dst_year > src_year:
        return 'forward'
    if dst_year < src_year:
        return 'backward'
    return 'same_year_pending'


def relation_claims(edge):
    """B08：把方法关系、具体改变、效果数字与适用条件拆成可独立判断的主张。"""
    established = edge.get('status') == 'confirmed' and not edge.get('evidence_stale')
    claims = []
    if edge.get('kind') in METHOD_KINDS:
        claims.append({'type': 'method_relation', 'label': '方法关系', 'text': edge.get('explanation') or '',
                       'status': 'verified' if established else 'pending'})
    if edge.get('change'):
        claims.append({'type': 'change', 'label': '具体改变', 'text': str(edge['change']),
                       'status': 'verified' if established else 'pending'})
    numbers = edge.get('numbers')
    if numbers:
        text = '；'.join(str(n.get('metric') if isinstance(n, dict) else n) for n in (numbers if isinstance(numbers, list) else [numbers]))
        claims.append({'type': 'numbers', 'label': '效果数字', 'text': text,
                       'status': 'pending' if (edge.get('numbers_pending') or not established) else 'verified'})
    if edge.get('conditions'):
        claims.append({'type': 'conditions', 'label': '适用条件', 'text': str(edge['conditions']),
                       'status': 'pending' if (edge.get('numbers_pending') or not established) else 'verified'})
    return claims

def verification_origin(edge):
    """B07：区分人工确认、模型复核与历史预置线索。"""
    if edge.get('manual') or edge.get('level') == 'human':
        return 'human'
    if edge.get('reviewed_by') or edge.get('assessment_verified'):
        return 'model'
    if edge.get('pipeline_legacy'):
        return 'legacy'
    return None

def display_relation(edge):
    manual = bool(edge.get('manual') or edge.get('level') == 'human')
    grade = relation_grade(edge)
    origin = verification_origin(edge)
    stale = bool(edge.get('evidence_stale'))
    if grade == 'citation':
        label = '引用事实'
    elif grade == 'fulltext' and manual:
        label = '原文已核 · 人工确认'
    elif grade == 'fulltext' and origin == 'model':
        label = '原文已核 · 模型复核'
    elif grade == 'abstract':
        label = '摘要支持'
    elif stale and manual:
        label = '人工确认 · 证据已过期，待复核'
    elif stale and origin == 'model':
        label = '模型复核 · 证据已过期，待复核'
    else:
        label = '题目线索，待核'
    return dict(edge, manual=manual, evidence_grade=grade, evidence_label=label, verification_origin=origin, claims=relation_claims(edge))

def scoped_nodes(nodes, edges, direction='', project=False, search='', method=''):
    anchors = {p['id'] for p in nodes if (not direction or direction in (p.get('directions') or [])) and (not project or p.get('project')) and (not method or method in (p.get('methods') or [])) and (not search or search.lower() in (p.get('title', '') + ' ' + p.get('citekey', '') + ' ' + str(p.get('authors') or '')).lower())}
    keep = set(anchors)
    reasons = {pid: '符合当前筛选' for pid in anchors}
    method_edges = [r for r in edges if r['kind'] in METHOD_KINDS and r['status'] != 'rejected']
    while True:
        previous = len(keep)
        for r in method_edges:
            if r['src'] in keep or r['dst'] in keep:
                for pid in (r['src'], r['dst']):
                    if pid not in keep:
                        reasons[pid] = '保留方法链的前驱、后继或桥接节点'
                    keep.add(pid)
        if previous == len(keep):
            break
    for r in edges:
        if r['kind'] == 'cites' and (r['src'] in anchors or r['dst'] in anchors):
            for pid in (r['src'], r['dst']):
                keep.add(pid)
                reasons.setdefault(pid, '当前筛选文献的直接引用邻居')
    return [dict(p, selection_reason=reasons[p['id']]) for p in nodes if p['id'] in keep]
