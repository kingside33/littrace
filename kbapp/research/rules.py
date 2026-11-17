"""kbapp.research.rules：由职责边界组织的知识库能力。"""
from __future__ import annotations
import re
NAMES = {'cites': '引用', 'inherits': '方法继承', 'improves': '具体改进', 'combines': '方法融合', 'transfers': '应用迁移', 'compares': '对比', 'similar': '相似', 'parallel': '并行研究', 'contradicts': '结论冲突'}

def explicit_reference(quote, target, citation_records, require_action=True):
    """仅接受同一明确动作语句里的目标引用，不能用全文任意一处引用替代。"""
    indices = {str(e['ref_index']) for r in citation_records for e in r.get('evidence', []) if e.get('ref_index')}
    author = target.get('authors') or ([target['author']] if target.get('author') else [])
    if isinstance(author, str):
        author = [author]
    surnames = {x.split(',')[0].split()[-1].lower() for x in author if x and x.split(',')[0].split()}
    year = str(target.get('year') or '')
    action = 'we\\s+(?:extend|build|adopt|combine|integrate|improve|use|employ|implement|adapt)|(?:本文|本研究).{0,12}(?:改进|扩展|采用|融合)|based on|extends?|builds? upon|derived from'
    for sentence in re.split('(?<=[。!?;])\\s*|(?<=\\.)\\s+(?=[A-Z])', quote):
        if require_action and (not re.search(action, sentence, re.I)):
            continue
        cited = set()
        for group in re.findall('\\[([\\d\\s,;–—-]+)\\]', sentence):
            for part in re.split('[,;]', group):
                limits = re.split('[–—-]', part.strip())
                if len(limits) == 1 and limits[0].isdigit():
                    cited.add(limits[0])
                elif len(limits) == 2 and all((x.strip().isdigit() for x in limits)):
                    lo, hi = map(int, limits)
                    if 0 <= hi - lo <= 100:
                        cited.update(map(str, range(lo, hi + 1)))
        if indices & cited:
            return True
        if year and year in sentence and any((re.search('\\b' + re.escape(name) + '\\b', sentence, re.I) for name in surnames)):
            return True
    return False

def validate_ai_relation(item, sources, papers, citation_records=()):
    """强证据须同时具备匹配引用、逐字出处、明确动作与合理的引用方向。"""
    evidence = []
    item.pop('eligible_for_review', None)
    item.pop('review_context', None)
    if item.get('kind') not in NAMES or item.get('kind') == 'cites':
        raise ValueError('模型关系类型无效')
    for ev in item.get('evidence', []):
        src = sources.get(ev.get('source_id'))
        quote = ev.get('quote', '').strip()
        if src and quote and (quote not in src['text']):
            match = re.search('\\s+'.join((re.escape(part) for part in re.split('\\s+', quote))), src['text'])
            if match:
                quote = match.group(0)
        if src and src.get('paper_id') in {item.get('src'), item.get('dst')} and (not src.get('derived')) and (src.get('kind') in {'paper_text', 'metadata', 'external_abstract'}) and quote and (quote in src['text']):
            evidence.append({'source_id': src['id'], 'quote': quote, 'url': src.get('url'), 'page': src.get('page_start') or src.get('page'), 'kind': src['kind'], 'ocr_provenance': {k: v for k, v in (src.get('ocr_provenance') or {}).items() if k in {'model', 'page', 'pdf_hash'}}, 'text_hash': src.get('text_hash')})
            evidence[-1].update(paper_id=src.get('paper_id'), section=src.get('section'), document_id=src.get('document_id'), verification_status='located')
    if not evidence:
        raise ValueError('关系缺少可核对的逐字证据')
    if item.get('src') not in papers or item.get('dst') not in papers:
        raise ValueError('关系引用了不存在的节点')
    source_year = papers[item['src']].get('year')
    target_year = papers[item['dst']].get('year')
    if isinstance(source_year, int) and isinstance(target_year, int) and (source_year < target_year) and (item['kind'] in {'inherits', 'improves', 'combines', 'transfers'}):
        raise ValueError('关系方向与发表年份冲突，须核对前后作身份')
    verified_fulltext = any((e['kind'] == 'paper_text' and sources[e['source_id']].get('paper_id') == item['src'] for e in evidence))
    item.pop('citation_verified', None)
    pair = [r for r in citation_records if r.get('src') == item['src'] and r.get('dst') == item['dst'] and (r.get('kind') == 'cites') and (r.get('status') == 'confirmed')]
    explicit = any((e['kind'] == 'paper_text' and sources[e['source_id']].get('paper_id') == item['src'] and explicit_reference(e['quote'], papers[item['dst']], pair) for e in evidence)) if pair else False
    required = all((item.get(k) for k in ('explanation', 'previous_problem', 'change')))
    item['status'] = 'confirmed' if verified_fulltext and explicit and required else 'candidate'
    item['level'] = 'fulltext' if verified_fulltext else 'abstract'
    item['evidence'] = evidence
    item['bilateral_evidence'] = all((any((e.get('paper_id') == pid and e['kind'] == 'paper_text' for e in evidence)) for pid in (item['src'], item['dst'])))
    if item['status'] == 'candidate' and verified_fulltext and required and pair and (item['kind'] in {'inherits', 'improves', 'combines', 'transfers'}):
        contexts = []
        for ev in evidence:
            source = sources[ev['source_id']]
            if ev['kind'] != 'paper_text' or source.get('paper_id') != item['src']:
                continue
            pos = source['text'].find(ev['quote'])
            context = source['text'][max(0, pos - 400):pos + len(ev['quote']) + 250]
            if explicit_reference(context, papers[item['dst']], pair, require_action=False):
                contexts.append(dict(ev, quote=context))
        if contexts:
            item['eligible_for_review'] = True
            item['review_context'] = contexts
    return item
