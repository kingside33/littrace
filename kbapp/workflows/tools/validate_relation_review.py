"""kbapp.workflows.tools.validate_relation_review：由职责边界组织的知识库能力。"""
from kbapp.core.paths import Paths
import os
import json

def main(argv=None):
    paths = Paths.from_base()
    stage = paths.runtime / 'validation/integrity'
    os.environ['KB_BASE'] = str(stage)
    os.environ['KB_DATA_DIR'] = str(stage / '_index')
    from kbapp.storage.store import Store
    from kbapp.core.io import atomic
    from kbapp.research.review import review_relation
    from kbapp.integrations.cloud import complete
    from kbapp.integrations.local_model import complete_local
    from kbapp.integrations.scheduling import model_slot
    from kbapp.research.evidence import evidence_ref
    store = Store(stage, data_dir=stage / '_index')
    rows = json.loads((store.paths.archive / 'legacy/real-relation-sources.json').read_text(encoding='utf-8'))
    if len(rows) != 2:
        raise ValueError('缺少真实双方原文，不执行关系验收')
    sources = {r['id']: r for r in rows}
    papers = store.papers()
    item = {'src': rows[0]['paper_id'], 'dst': rows[1]['paper_id'], 'kind': 'improves', 'explanation': 'Informed RRT* 在 RRT* 基础上提出修改。', 'previous_problem': '前作提出渐近最优的 RRT*。', 'change': '后作明确把自身算法称为对 RRT* 的简单修改。', 'conditions': '这里只核验论文明确声明的方法继承，不推断有限时间最优性。', 'evidence': [{'source_id': r['id'], 'quote': r['text']} for r in rows]}
    out = paths.exports / 'evaluation/integrity-upgrade/relation-review.json'
    report = {'source_ids': list(sources), 'cases': []}
    for label, case in [('method_improvement', item), ('insufficient_single_side', dict(item, evidence=item['evidence'][:1]))]:
        with model_slot(paths.data, timeout=600):
            verdict = review_relation(store, case, sources, papers[item['dst']], [], complete, complete_local)
        report['cases'].append({'case': label, 'verification': verdict})
        atomic(out, report)
    try:
        evidence_ref({'source_id': rows[0]['id'], 'quote': 'This invented sentence does not occur in the paper.'}, sources)
    except ValueError:
        report['fabricated_quote_blocked'] = True
    with model_slot(paths.data, timeout=600):
        report['repeat'] = review_relation(store, item, sources, papers[item['dst']], [], complete, complete_local)
    atomic(out, report)
    print(json.dumps(report, ensure_ascii=True))
