"""kbapp.research.local_verification：由职责边界组织的知识库能力。"""
from __future__ import annotations
import json
import re
from kbapp.core.chunking import est_tokens
from kbapp.core.io import digest
from kbapp.integrations.local_model import SYSTEM, complete_local

def verify_relations(store, paper, papers, candidate_ids, pair_refs, sources, progress):
    """根据识别文本逐块核验候选前作；返回明确的本地核验结论。"""
    cfg = store.settings()
    results = []
    requests = []
    own = [s for s in sources if s.get('paper_id') == paper['id'] and s['kind'] == 'paper_text']
    if not own:
        own = [s for s in sources if s.get('paper_id') == paper['id']][:1]
    for n, source in enumerate(own, 1):

        def target_rank(pid):
            cited = any((re.search('\\[[^\\]]*\\b' + re.escape(i) + '\\b[^\\]]*\\]', source['text']) for i in pair_refs.get(pid, [])))
            return (not cited, candidate_ids.index(pid))
        ids = sorted(candidate_ids, key=target_rank)[:8]

        def make_prompt(ids):
            def public(p):
                return {k: p.get(k) for k in ('id', 'title', 'year')}
            return '核验主论文与候选前作之间是否存在可指认的具体方法关系。输入已由GLM-OCR识别，不要重新做OCR或改写原文。只核验本段能支持的内容，不要补全缺失细节；引用本身、相似性、共用平台或研究动机不能证明继承。最多2条；src为主论文id，dst为候选id，kind仅可为inherits/improves/combines/transfers/compares/similar/parallel/contradicts。须分别引用双方原文，缺少前作证据时请求全文。explanation、previous_problem、change、conditions用简短中文；quote为原文完整句，最长200字符。返回 {"relations":[{"src":"","dst":"","kind":"","explanation":"","previous_problem":"","change":"","conditions":"","evidence":[{"source_id":"","quote":""}],"local_review_verdict":{"supported":true,"method_relation":true,"unsupported_details":[],"reason":"中文核验理由"}}],"fulltext_requests":[{"paper_id":"","reason":"","question":""}]}。无依据返回空列表。\n' + json.dumps({'paper': public(paper), 'candidates': [public(papers[x]) for x in ids if x in papers], 'citation_reference_indices': {x: pair_refs.get(x, []) for x in ids}, 'sources': [{k: s.get(k) for k in ('id', 'paper_id', 'kind', 'text', 'section')} for s in [source] + [s for s in sources if s.get('paper_id') in ids and s.get('kind') == 'paper_text'][:2]]}, ensure_ascii=False)
        prompt = make_prompt(ids)
        while ids and est_tokens(SYSTEM + prompt) + 1536 + 256 > cfg['local_text_context']:
            ids.pop()
            prompt = make_prompt(ids)
        if not ids:
            raise ValueError('当前上下文无法容纳完整原文块和候选元数据，请提高上下文后续跑')
        progress(f"{cfg['local_text_model']} 方法关系核验 {n}/{len(own)}：" + paper['title'][:50])
        part = complete_local(store, prompt, 'relation_verification:' + paper['id'] + ':' + source['id'])
        for relation in part.get('relations', [])[:2]:
            if relation.get('src') == paper['id'] and relation.get('dst') in ids:
                results.append(relation)
        requests.extend(part.get('fulltext_requests', [])[:2])
    unique = {digest(json.dumps(r, ensure_ascii=False, sort_keys=True)): r for r in results}
    return {'relations': list(unique.values()), 'fulltext_requests': requests}
