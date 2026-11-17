"""kbapp.research.verified：由职责边界组织的知识库能力。"""
import unicodedata
import re
from kbapp.core.io import jsonl
SEEDS = [{'src': 'Karaman2011Sampling', 'dst': 'LaValle1998Rapidly', 'kind': 'inherits', 'markers': ['main contribution', 'RRT'], 'explanation': '从 RRT 的可行路径增量搜索推进到具有渐近最优性保证的 RRT*。', 'previous_problem': '传统 RRT 的解代价一般不保证渐近收敛到最优值。', 'change': '文章引入 RRG 并通过树化构造 RRT*，采用邻域择父与重连，使解代价在文中条件下渐近趋于最优。', 'conditions': '依据文中采样、邻域连接和可行性假设；渐近保证不等于有限采样时的最优解。'}, {'src': 'Gammell2014Informed', 'dst': 'Karaman2011Sampling', 'kind': 'improves', 'markers': ['simple modification to RRT'], 'explanation': 'Informed RRT* 继承 RRT* 的搜索框架，将首解后的采样集中到可能改进当前解的区域。', 'previous_problem': 'RRT* 在首解后继续向整个规划域扩展，单次起终点查询中的许多采样无法改进当前解。', 'change': '用可采纳的路径长度启发式定义椭球知情集并直接采样，在保留概率完备性和渐近最优性的同时改善收敛。', 'conditions': '文中直接采样构造针对路径长度目标；首解前仍按 RRT* 搜索。'}, {'src': 'Gammell2015Batch', 'dst': 'Gammell2014Informed', 'kind': 'inherits', 'markers': ['batches of one sample', 'Informed RRT'], 'explanation': 'BIT* 将逐样本的知情采样推广为批次采样与启发式排序的隐式随机几何图搜索。', 'previous_problem': '逐个样本的树扩展不能充分利用图搜索中对候选边进行启发式排序的能力。', 'change': '批次扩充样本并按潜在路径代价排序搜索；文章说明单样本批次时可看作 Informed RRT* 的一个版本。', 'conditions': '这是一种一般化关系；BIT* 还结合了图搜索思想，并非单一线性替代。'}, {'src': 'Chen2026VK', 'dst': 'Karaman2011Sampling', 'kind': 'improves', 'markers': ['integrate the three coupling models into the RRT'], 'explanation': 'VK-RRT* 将 RRT* 扩展到水下速度相关的能耗、曲率与安全距离耦合约束。', 'previous_problem': '几何路径长度或固定安全间距难以表示水下航速变化导致的能耗、转弯能力和制动距离变化。', 'change': '在节点扩展阶段加入速度—曲率与速度—安全距离约束，在择父代价中引入速度—能耗模型。', 'conditions': '面向文中三维海流环境及其运动学、能耗与制动模型；原 RRT* 的理论保证需按新增机制重新核查。'}]

def compact(text):
    return re.sub('\\s+', ' ', unicodedata.normalize('NFKC', text)).lower()

def audit_legacy_seeds(store):
    """旧版预置线索没有双侧复核；保留历史，人工决策完全不变。"""
    import json
    from kbapp.core.io import now
    with store.db() as db:
        for row in db.execute("SELECT id,payload FROM relations WHERE manual=0 AND status='confirmed'").fetchall():
            r = json.loads(row['payload'])
            if r.get('reviewed_by') != 'Codex 原文复核，2026-10-31':
                continue
            if r.get('review_trace') or r.get('independent_review'):
                continue
            db.execute('INSERT INTO relation_history(relation_id,payload,at) VALUES(?,?,?)', (row['id'], row['payload'], now()))
            r.update(status='candidate', semantic_recheck_failed=True, reviewed_by='历史预置线索，待双侧原文与模型复核')
            db.execute("UPDATE relations SET status='candidate',payload=?,updated=? WHERE id=?", (json.dumps(r, ensure_ascii=False), now(), row['id']))

def seed_verified(store, index):
    papers = store.papers()
    by_key = {p.get('citekey'): p for p in papers.values()}
    corpus = jsonl(index / 'corpus.jsonl')
    count = 0
    for definition in SEEDS:
        src = by_key.get(definition['src'])
        dst = by_key.get(definition['dst'])
        if not src or not dst:
            continue
        candidates = [r for r in corpus if r.get('paper_id') == src['id'] and r['kind'] == 'paper_text']
        source = next((r for r in candidates if all((compact(m) in compact(r['text']) for m in definition['markers']))), None)
        if not source:
            continue
        relation = {k: v for k, v in definition.items() if k != 'markers'}
        relation.update(src=src['id'], dst=dst['id'], status='candidate', level='fulltext', reviewed_by='预置线索，待统一证据核验', semantic_recheck_failed=True, evidence=[{'source_id': source['id'], 'quote': source['text'], 'url': source['url'], 'page': source.get('page_start'), 'kind': 'paper_text'}])
        store.relation(relation)
        count += 1
    return count

def mark_stale(store, index):
    source_ids = {r['id'] for r in jsonl(index / 'corpus.jsonl')}
    from kbapp.library.recognition import active_sources
    source_ids.update((r['id'] for r in active_sources(store)))
    from kbapp.library.documents import sources
    source_ids.update((r['id'] for r in sources(store)))
    with store.db() as db:
        rows = db.execute("SELECT * FROM relations WHERE level='fulltext' OR manual=1").fetchall()
    import json
    for row in rows:
        r = json.loads(row['payload'])
        refs = [e.get('source_id') for e in r.get('evidence', []) if e.get('source_id')]
        if refs and any((x not in source_ids for x in refs)):
            r['evidence_stale'] = True
            from kbapp.research.dependencies import record as record_invalidation
            record_invalidation(store, 'relation', row['id'], '原文来源已变化或不可用，证据过期', refs)
            if row['manual']:
                with store.db() as db:
                    db.execute('UPDATE relations SET payload=? WHERE id=?', (json.dumps(r, ensure_ascii=False), row['id']))
            else:
                r['status'] = 'candidate'
                if not r.get('explanation', '').startswith('原证据版本已变化'):
                    r['explanation'] = '原证据版本已变化，需重新核对。' + r.get('explanation', '')
                store.relation(r)
