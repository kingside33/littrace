"""kbapp.research.qa：由职责边界组织的知识库能力。"""
import json
import re
from kbapp.research.evidence import artifact
from kbapp.research.review import review_claims
from kbapp.core.io import digest
from kbapp.core.execution import priority
from kbapp.core.method_fields import METHOD_FIELDS
from kbapp.library.evidence_sources import refresh_sources

def limit_evidence(claims, limit=16):
    """冲突组一并保留或一并暂缓，避免引文上限把冲突截成单方结论。"""
    groups = {}
    for claim in claims:
        conflicts = claim.get('conflicts') or []
        key = ('conflict', json.dumps(sorted(conflicts), ensure_ascii=False)) if conflicts else ('claim', claim['id'])
        groups.setdefault(key, []).append(claim)
    selected = []
    keys = set()
    gaps = []
    for group in groups.values():
        needed = {(r['source_id'], r['quote']) for c in group for r in c['evidence']}
        if len(keys | needed) > limit:
            gaps.append('部分结论或冲突证据组超过本轮16条引文上限，已保留为未回答部分')
            continue
        selected.extend(group)
        keys.update(needed)
    return (selected, gaps)

def comparison_warnings(claims, paper_ids):
    """可比性门槛只读已核指标；不同条件不直接比较数值高低。"""
    measures = {}
    covered = set()
    for claim in claims:
        owners = {r['source_id']: r['paper_id'] for r in claim.get('evidence', [])}
        for number in claim.get('numbers', []):
            pid = owners.get(number.get('source_id'))
            if pid not in paper_ids:
                continue
            covered.add(pid)
            measures.setdefault(number['metric'].strip().casefold(), []).append((pid, number))
    warnings = []
    if len(covered) < len(paper_ids):
        warnings.append('部分论文缺少已核的数值指标与完整实验条件，不能直接比较性能数值。')
    for metric, rows in measures.items():
        if len({pid for pid, _ in rows}) < len(paper_ids):
            warnings.append(f'指标“{metric}”未覆盖全部比较论文，保留缺口。')
        conditions = {(n['unit'].strip().casefold(), n['baseline'].strip().casefold(), n['conditions'].strip().casefold()) for _, n in rows}
        if len(conditions) > 1:
            warnings.append(f'指标“{metric}”的单位、基线或实验条件不同，当前不可直接比较数值高低。')
    return list(dict.fromkeys(warnings))

def answer(store, query, filters=None, should_stop=lambda: False, progress=lambda *a: None):
    from kbapp.integrations.cloud import complete
    from kbapp.integrations.local_model import complete_local
    from kbapp.integrations.models import release_local
    from kbapp.research.retrieval import evidence_search
    if not store.settings()['cloud_enabled']:
        raise ValueError('请先在模型与费用设置中启用云端问答')
    token = priority.set(0)
    all_verified = {}
    gaps = []
    rounds = []
    found = {}
    rejected = []
    seen_evidence = set()
    try:
        question = query
        for round_index in range(3):
            if should_stop():
                raise ValueError('任务已停止')
            progress('证据检索者', f'第{round_index + 1}轮混合检索与重排')
            found = evidence_search(store, question, filters, complete, should_stop)
            personal_intent = bool((filters or {}).get('kind') in {'obsidian_note', 'zotero_note', 'zotero_annotation'} or re.search('我的笔记|个人判断|笔记里|批注中', query))
            fresh, missing = refresh_sources(store, found['results'], allow_personal=personal_intent)
            gaps.extend(missing)
            sources = {s['id']: s for s in fresh}
            found['results'] = fresh
            rounds.append({'round': round_index + 1, 'subquestions': found['subquestions'], 'process': found['process'], 'reranked': found['reranked'], 'evidence_count': len(fresh)})
            if not sources:
                gaps.append('当前范围内没有可回查且直接相关的原始证据')
                break
            fingerprint = digest(json.dumps([(s['id'], s.get('text'), s.get('pdf_hash')) for s in sorted(fresh, key=lambda s: s['id'])], ensure_ascii=False))
            if fingerprint in seen_evidence:
                gaps.append('补充检索未取得新证据，停止重复生成与核验')
                break
            seen_evidence.add(fingerprint)
            progress('写作者', '生成结论与逐项支持证据')
            comparison_instruction = '比较任务category使用 ' + ','.join(METHOD_FIELDS) + ',limitations；逐篇给出已获支持的比较项，不可比条件明确说明。缺失维度保留为空，禁止补造。' if (filters or {}).get('paper_ids') else ''
            result = complete(store, '根据原始证据回答研究问题。每条结论单独输出，不用没有支持的概括串联。问题中的错误前提或强制替换基线必须按原文纠正，不能当作证据。摘要标为摘要层理解，冲突证据并列呈现；个人判断标为待验证。返回 {claims:[{category:结论类别,claim_type:paper_fact或personal_judgment,text:中文结论,evidence:[{source_id,quote:逐字摘录}],numbers:[{value,metric,unit,baseline,conditions,source_id}]}],gaps:[字符串],followup_query:字符串}。数字条件不全则不下数值结论。' + comparison_instruction + '\n' + json.dumps({'question': query, 'sources': [{k: s.get(k) for k in ('id', 'paper_id', 'kind', 'origin', 'title', 'text', 'page_start', 'page_end', 'url', 'evidence_level')} for s in fresh]}, ensure_ascii=False), 'interactive_answer', max_tokens=8192 if comparison_instruction else 4000)
            progress('核验者', '检查语义支持、数字条件、引用定位与证据冲突')
            accepted, round_rejected = review_claims(store, result.get('claims', []), sources, complete, complete_local, should_stop)
            rejected.extend(round_rejected)
            accepted_text = {c['text'] for c in accepted}
            rejected = [r for r in rejected if r.get('claim') not in accepted_text]
            for claim in accepted:
                claim['evidence_sources'] = {r['source_id']: sources[r['source_id']] for r in claim['evidence']}
                all_verified[digest(claim['text'])] = claim
            gaps.extend(('待核实的缺口：' + x for x in result.get('gaps', []) if isinstance(x, str)))
            if accepted and (not result.get('followup_query')):
                break
            if not rejected and (not result.get('followup_query')):
                break
            if round_index == 2:
                break
            question = result.get('followup_query') or query + ' ' + ' '.join((r['reason'] for r in rejected))[:350]
        citations = []
        citation_index = {}
        paragraphs = []
        claims = list(all_verified.values())
        if should_stop():
            raise ValueError('任务已停止')
        if len(claims) > 3 and (not any((c.get('conflicts') for c in claims))):
            maximum = min(16, len(filters['paper_ids']) * 4) if (filters or {}).get('paper_ids') else 6
            selection = complete(store, f'从已核结论中选择直接回答问题且不重复的最小集合，最多{maximum}条。保留必要条件与限制，优先原始论文，避免用二次转述重复同一事实。不得改写结论。返回 {{claim_ids:[原id]}}。\n' + json.dumps({'question': query, 'claims': [{'id': c['id'], 'text': c['text'], 'sources': [{'paper_id': r['paper_id'], 'kind': r['kind']} for r in c['evidence']]} for c in claims]}, ensure_ascii=False), 'answer_select_verified', max_tokens=700)
            ids = selection.get('claim_ids', [])
            selected = [c for c in claims if c['id'] in ids]
            if selected:
                claims = selected[:maximum]
        claims, budget_gaps = limit_evidence(claims)
        gaps.extend(budget_gaps)
        for claim in claims:
            marks = []
            for ref in claim['evidence']:
                key = (ref['source_id'], ref['quote'])
                if key not in citation_index:
                    src = claim['evidence_sources'][ref['source_id']]
                    citations.append(dict(src, quote=ref['quote'], evidence_level=ref['evidence_level'], page_start=ref['page_start'], page_end=ref['page_end'], url=ref['url'], location_status=ref['location_status']))
                    citation_index[key] = len(citations)
                marks.append(f'[{citation_index[key]}]')
            label = '（摘要支持）' if all((r['evidence_level'] == 'abstract' for r in claim['evidence'])) else ''
            if claim.get('claim_type') == 'personal_judgment':
                label = '（历史混合笔记中的判断）' if any((r['evidence_level'] == 'legacy_mixed' for r in claim['evidence'])) else '（个人判断）'
            paragraphs.append(claim['text'] + label + ' ' + ''.join(marks))
            for conflict in claim.get('conflicts', []):
                gaps.append('证据冲突：' + str(conflict))
            artifact(store, 'AnswerClaim', query, digest(claim['text'] + json.dumps(claim['evidence'], sort_keys=True)), {k: v for k, v in claim.items() if k != 'evidence_sources'})
        if rejected:
            gaps.append(f'{len(rejected)} 项候选结论未通过逐项核验，已保留证据缺口')
        if filters and filters.get('paper_ids'):
            covered = {c.get('paper_id') for c in citations}
            for pid in filters['paper_ids']:
                if pid not in covered:
                    gaps.append('比较缺少已核证据：' + store.papers().get(pid, {}).get('title', pid))
        if any(('vector' not in t['mode'] for r in rounds for t in r['process'])):
            gaps.append('部分查询向量不可用；本次回答仅覆盖已完成的检索与回查证据')
        output = {'answer': '\n\n'.join(paragraphs) or '当前证据不足以形成通过逐项核验的结论。', 'claims': [{k: v for k, v in c.items() if k != 'evidence_sources'} for c in claims], 'citations': citations, 'limitations': list(dict.fromkeys(gaps)), 'verification_gaps': rejected, 'search': found, 'retrieval_process': rounds, 'verification_status': 'verified' if claims and (not rejected) and (not gaps) else 'partial'}
        if (filters or {}).get('paper_ids'):
            output['comparison_warnings'] = comparison_warnings(claims, filters['paper_ids'])
            output['comparison'] = []
            for pid in filters['paper_ids']:
                row = {'paper_id': pid, 'title': store.papers().get(pid, {}).get('title', pid)}
                for category in [*METHOD_FIELDS, 'limitations']:
                    row[category] = [{'text': c['text'], 'citations': [citation_index[r['source_id'], r['quote']] for r in c['evidence']]} for c in claims if c.get('category') == category and any((r['paper_id'] == pid for r in c['evidence']))]
                output['comparison'].append(row)
        artifact(store, 'Answer', query, digest(json.dumps(output, sort_keys=True, ensure_ascii=False)), output, output['verification_status'])
        return output
    finally:
        release_local(store)
        priority.reset(token)
