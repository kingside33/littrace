"""kbapp.research.reading：由职责边界组织的知识库能力。"""
import difflib
import json
import re
from kbapp.core.io import atomic, digest, now, jsonl
from kbapp.research.evidence import artifact, latest, valid_claim
from kbapp.core.text import file_hash, readable_text
from kbapp.research.review import review_claims
from kbapp.library.sources import paper_url
from kbapp.core.method_fields import METHOD_FIELDS
from kbapp.core.markers import END, START
FIELDS = {'summary': '结论', 'problem': '研究问题', 'formalization': '问题形式化与公式', 'algorithm': '核心算法与设计选择', 'experiments': '实验条件与结果', 'critique': '批判评估与可复现性', 'project_relation': '课题关联', 'quotes': '原文摘录', 'figures': '关键图表'}
PROMPT_VERSION = 'full-reading-v2'
FIELDS.update(METHOD_FIELDS)
PROMPT_VERSION = 'full-reading-v3-method-fields'

def keep_categories(claims, original):
    """整合仅改变表达时沿用分段产物的类别；分类不作为新的科学结论。"""
    from difflib import SequenceMatcher
    if not isinstance(claims, list):
        return list(original)
    result = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        if claim.get('category') not in FIELDS:
            ids = {r.get('source_id') for r in claim.get('evidence', []) if isinstance(r, dict)}
            matches = [c for c in original if c.get('category') in FIELDS and ids & {r.get('source_id') for r in c.get('evidence', [])}]
            if matches:
                claim = dict(claim, category=max(matches, key=lambda c: SequenceMatcher(None, claim.get('text', ''), c.get('text', '')).ratio())['category'])
        result.append(claim)
    return result

def understanding_fingerprint(store, paper):
    from kbapp.library.recognition import pdf_path
    path = pdf_path(store, paper)
    cached_sources = [(p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in (store.paths.cache / 'recognized_sources').glob(digest(paper['id'])[:16] + '-*.json')]
    return digest(json.dumps({'pdf': file_hash(path) if path and path.is_file() else 'missing', 'metadata': {k: paper.get(k) for k in ('title', 'abstract', 'project', 'directions')}, 'recognized_sources': sorted(cached_sources), 'prompt': PROMPT_VERSION, 'models': {k: store.settings()[k] for k in ('local_text_model', 'local_text_context', 'model')}}, sort_keys=True, ensure_ascii=False))

def read_paper(store, paper, should_stop=lambda: False, progress=lambda *a: None):
    import pymupdf
    from kbapp.library.chunk import page_text
    from kbapp.core.chunking import split_long
    from kbapp.integrations.local_model import complete_local
    from kbapp.integrations.models import release_local
    from kbapp.integrations.cloud import complete
    from kbapp.library.recognition import pdf_path, recognize_sources
    from kbapp.integrations.scheduling import model_slot
    fingerprint = understanding_fingerprint(store, paper)
    prior = latest(store, 'PaperUnderstanding', paper['id'])
    if prior and prior['input_fingerprint'] == fingerprint and prior.get('coverage', {}).get('complete'):
        sync_reading(store, paper, prior)
        return prior
    path = pdf_path(store, paper)
    sources = {}
    sections = []
    gaps = []
    claims = []
    pages = []
    if not path or not path.is_file():
        result = artifact(store, 'PaperUnderstanding', paper['id'], fingerprint, {'paper_id': paper['id'], 'derived': True, 'coverage': {'complete': False, 'total_pages': 0, 'read_pages': [], 'missing_pages': []}, 'claims': [], 'gaps': ['原文不可用；只具备元数据或摘要，未完成全文精读']}, 'partial')
        return result
    pdf_hash = file_hash(path)
    figure_sources = [s for s in jsonl(store.active_dir() / 'corpus.jsonl') if s.get('paper_id') == paper['id'] and s.get('kind') == 'figure']
    sources.update({s['id']: s for s in figure_sources})
    from kbapp.library.recognition import active_sources
    recognized_pages = {}
    for src in active_sources(store):
        if src.get('paper_id') == paper['id']:
            recognized_pages.setdefault(src.get('page_start'), []).append(src)
    with pymupdf.open(path) as doc:
        count = len(doc)
        if not count:
            gaps.append('PDF没有可读取页面')
        for number, page in enumerate(doc, 1):
            if should_stop():
                raise ValueError('任务已停止')
            progress('精读者', f"{paper.get('citekey') or paper['title']} · p.{number}/{count}")
            page_claims = []
            try:
                raw = page_text(page)
                text = raw if isinstance(raw, str) else '\n'.join((str(x) for x in raw))
                if number in recognized_pages:
                    text = '\n'.join((s['text'] for s in recognized_pages[number]))
                elif not readable_text(text):
                    from kbapp.library.page_ocr import blank_page
                    if blank_page(page):
                        pages.append(number)
                        sections.append({'page': number, 'section': '空白页', 'status': 'read_blank'})
                        continue
                    release_local(store)
                    with model_slot(store.root, should_stop=should_stop):
                        recognized = recognize_sources(store, paper, [{'paper_id': paper['id'], 'kind': 'paper_text', 'page_start': number, 'page_end': number}], lambda msg: progress('精读者', msg), should_stop)
                    text = '\n'.join((r['text'] for r in recognized))
                    if not readable_text(text):
                        raise ValueError('页面抽取及识别均未得到完整文字')
                page_ok = True
                for index, piece in enumerate(split_long(text, 500)):
                    if should_stop():
                        raise ValueError('任务已停止')
                    source = artifact(store, 'RawEvidence', paper['id'] + f':{number}:{index}', digest(pdf_hash + piece), {'paper_id': paper['id'], 'kind': 'paper_text', 'origin': 'paper', 'text': piece, 'page_start': number, 'page_end': number, 'pdf_hash': pdf_hash, 'title': paper['title'], 'url': paper_url(paper, number), 'derived': False})
                    sources[source['id']] = source
                    chunk_fp = digest(fingerprint + source['id'])
                    chunk = latest(store, 'ReadingSection', paper['id'] + ':' + source['id'])
                    if not chunk or chunk['input_fingerprint'] != chunk_fp:
                        result = complete_local(store, '逐段精读论文，提取问题、方法、假设、公式、实验、局限、课题关联、摘录、关键图表。只依据当前片段；没有内容不补写。返回 {section:章节名,claims:[{category:summary/problem/object/assumptions/state_space/constraints/objective/formulas/method/algorithm_steps/improvement/experiments/metrics/conclusions/contributions/formalization/algorithm/critique/project_relation/quotes/figures,text:中文结论,evidence:[{source_id,quote:逐字原文}],numbers:[{value,metric,unit,baseline,conditions,source_id}]}],gaps:[字符串]}。每段最多2条结论，每条只引用一小句原文。数字必须完整绑定实验条件，没有报告写未报告；理论参数的基线和实验条件写不适用，不估算。\n' + json.dumps({'source_id': source['id'], 'text': piece, 'project': paper.get('project'), 'figures_on_page': [{'source_id': f['id'], 'caption': (f.get('caption') or '')[:500]} for f in figure_sources if f.get('page') == number][:2] if index == 0 else []}, ensure_ascii=False), 'understanding_extract', max_tokens=1000)
                        parsed = []
                        for claim in result.get('claims', []):
                            parsed.append(valid_claim(claim, sources, paper['id']))
                        if not isinstance(result.get('claims'), list):
                            raise ValueError('分段提取未给出完整结构')
                        chunk = artifact(store, 'ReadingSection', paper['id'] + ':' + source['id'], chunk_fp, {'paper_id': paper['id'], 'section': result.get('section', '章节待确认'), 'claims': parsed, 'gaps': result.get('gaps', []), 'page': number, 'source_id': source['id']}, 'extracted')
                    sections.append({'page': number, 'section': chunk['section'], 'source_id': source['id'], 'status': 'read'})
                    page_claims.extend(chunk['claims'])
                claims.extend(page_claims)
                if page_ok:
                    pages.append(number)
            except Exception as exc:
                if should_stop():
                    raise
                claims.extend(page_claims)
                gaps.append(f'p.{number}：{str(exc)[:180]}')
                sections.append({'page': number, 'status': 'failed', 'reason': str(exc)[:180]})
    verified = []
    rejected = []
    for start in range(0, len(claims), 8):
        if should_stop():
            raise ValueError('任务已停止')
        progress('写作者', f'整合原文结论 {start}/{len(claims)}')
        block = claims[start:start + 8]
        written = complete(store, '根据逐节材料合并重复结论，保留category、text、evidence和numbers结构。category只能使用输入已有的英文类别，不得改为中文名称。所有摘录逐字保留，不补充没有证据的事实；课题迁移判断明确标为待验证。返回 {claims:[同结构]}。\n' + json.dumps({'claims': block}, ensure_ascii=False), 'reading_write', max_tokens=3000)
        current = keep_categories(written.get('claims', []), block)
        if not isinstance(current, list) or any((not isinstance(c, dict) or c.get('category') not in FIELDS for c in current)):
            current = block
            gaps.append('整合结构未完整保留，已按原分段材料核验')
        progress('核验者', '逐项核验；疑难结论统一补证最多两轮')
        accepted, failed = review_claims(store, current, sources, complete, complete_local, should_stop)
        verified.extend(accepted)
        rejected.extend(failed)
    release_local(store)
    unique = {digest(c['text']): c for c in verified}
    covered_categories = {c.get('category') for c in unique.values()}
    gaps.extend(('未形成已核内容：' + label for field, label in FIELDS.items() if field not in covered_categories))
    complete_reading = bool(count and len(pages) == count and (not rejected) and all((k in covered_categories for k in ('problem', 'algorithm', 'experiments', 'critique'))))
    if file_hash(path) != pdf_hash:
        complete_reading = False
        gaps.append('精读期间PDF已变化；本轮产物保留为部分理解，须重新读取')
    result = artifact(store, 'PaperUnderstanding', paper['id'], understanding_fingerprint(store, paper), {'paper_id': paper['id'], 'pdf_hash': pdf_hash, 'derived': True, 'sections': sections, 'coverage': {'total_pages': count, 'read_pages': pages, 'missing_pages': [n for n in range(1, count + 1) if n not in pages], 'complete': complete_reading}, 'claims': list(unique.values()), 'rejected': rejected, 'gaps': gaps, 'figures': [{k: f.get(k) for k in ('id', 'label', 'caption', 'page', 'url')} for f in figure_sources if any((r['source_id'] == f['id'] for c in unique.values() for r in c['evidence']))], 'figure_inventory': [{k: f.get(k) for k in ('id', 'label', 'caption', 'page', 'url')} for f in figure_sources]}, 'verified' if complete_reading else 'partial')
    sync_reading(store, paper, result)
    return result

def render_reading(result):
    coverage = result['coverage']
    lines = ['## 自动全文理解（派生产物）', '', ('全文精读完成' if coverage.get('complete') else '部分理解') + f" · 已读 {len(coverage.get('read_pages', []))}/{coverage.get('total_pages', 0)} 页", '', '此区块由原始证据生成；问答须回查 PDF，不能将本笔记作为独立证据。', '']
    for category, label in FIELDS.items():
        lines.extend(['### ' + label, ''])
        if category == 'quotes':
            quotations = []
            seen = set()
            ordered = sorted(result.get('claims', []), key=lambda c: c.get('category') != 'quotes')
            for claim in ordered:
                for ref in claim['evidence']:
                    if ref['quote'] not in seen:
                        quotations.append(ref)
                        seen.add(ref['quote'])
            for ref in quotations[:3]:
                lines.append(f"> {ref['quote']} — [原文 p.{ref.get('page_start') or '?'}]({ref.get('url') or '#'}) <!-- evidence:{ref['source_id']} -->")
        else:
            for claim in [c for c in result.get('claims', []) if c.get('category') == category][:2]:
                links = ' '.join((f"[p.{r.get('page_start') or '?'}]({r.get('url') or '#'}) <!-- evidence:{r['source_id']} -->" for r in claim['evidence']))
                lines.append('- ' + claim['text'] + ' ' + links)
        lines.append('')
    if result.get('gaps'):
        lines.extend(['### 证据缺口', ''] + ['- ' + str(g) for g in result['gaps']] + [''])
    lines.append('<!-- derived-artifact:' + result['id'] + ' -->')
    return START + '\n' + '\n'.join(lines).rstrip() + '\n' + END

def sync_reading(store, paper, result):
    if not paper.get('card'):
        return {'status': 'no_card'}
    path = (store.base / paper['card']).resolve()
    vault = (store.base / '_知识库').resolve()
    if not path.is_relative_to(vault) or not path.is_file():
        return {'status': 'no_card'}
    from kbapp.integrations.scheduling import file_slot
    proposed = render_reading(result)
    with file_slot(store.root, 'workbench-note-sync.lock'):
        old = path.read_text(encoding='utf-8')
        match = re.search(re.escape(START) + '.*?' + re.escape(END), old, re.S)
        current = match.group(0) if match else ''
        with store.db() as db:
            row = db.execute('SELECT * FROM note_sync WHERE paper_id=?', (paper['id'],)).fetchone()
        base = row['base_text'] if row else ''
        conflict = current != proposed and (row and current != base or (not row and bool(current)))
        if conflict:
            status = 'conflict'
        else:
            content = old[:match.start()] + proposed + old[match.end():] if match else old.rstrip() + '\n\n' + proposed + '\n'
            if path.read_text(encoding='utf-8') != old:
                status = 'conflict'
            else:
                if content != old:
                    atomic(path, content)
                base = proposed
                status = 'synced'
        with store.db() as db:
            db.execute('INSERT OR REPLACE INTO note_sync VALUES(?,?,?,?,?)', (paper['id'], base, proposed, status, now()))
    return note_status(store, paper)

def note_status(store, paper):
    with store.db() as db:
        row = db.execute('SELECT * FROM note_sync WHERE paper_id=?', (paper['id'],)).fetchone()
    with store.db() as db:
        metadata = db.execute('SELECT * FROM note_sync WHERE paper_id=?', (paper['id'] + '::metadata',)).fetchone()
    if metadata and metadata['status'] == 'conflict':
        from kbapp.core.markers import AUTO_START, AUTO_END
        path = store.base / (paper.get('card') or '')
        old = path.read_text(encoding='utf-8') if path.is_file() else ''
        match = re.search(re.escape(AUTO_START) + '.*?' + re.escape(AUTO_END), old, re.S)
        current = match.group(0) if match else ''
        diff = '\n'.join(difflib.unified_diff(current.splitlines(), metadata['proposed'].splitlines(), fromfile='保留的文献入口', tofile='待合并入口', lineterm=''))
        if row and row['status'] == 'conflict':
            reading = re.search(re.escape(START) + '.*?' + re.escape(END), old, re.S)
            present = reading.group(0) if reading else ''
            diff += '\n\n' + '\n'.join(difflib.unified_diff(present.splitlines(), row['proposed'].splitlines(), fromfile='人工精读版本', tofile='待合并精读版本', lineterm=''))
        return {'status': 'conflict', 'block': 'metadata', 'proposed': metadata['proposed'], 'diff': diff}
    if not row:
        return {'status': 'not_generated'}
    path = store.base / (paper.get('card') or '')
    old = path.read_text(encoding='utf-8') if path.is_file() else ''
    match = re.search(re.escape(START) + '.*?' + re.escape(END), old, re.S)
    current = match.group(0) if match else ''
    return {'status': row['status'], 'proposed': row['proposed'] if row['status'] == 'conflict' else '', 'diff': '\n'.join(difflib.unified_diff(current.splitlines(), row['proposed'].splitlines(), fromfile='人工保留版本', tofile='待合并自动版本', lineterm='')) if row['status'] == 'conflict' else ''}
