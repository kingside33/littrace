"""kbapp.workflows.tools.kb_evaluate：由职责边界组织的知识库能力。"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from kbapp.storage.store import Store
from kbapp.core.io import atomic, digest, now, read_json
from kbapp.search.indexing import Search
from kbapp.research.retrieval import evidence_search
from kbapp.integrations.cloud import complete
os.environ['OPENBLAS_NUM_THREADS'] = os.environ.get('KB_BLAS_THREADS', '1')
os.environ['OMP_NUM_THREADS'] = os.environ.get('KB_BLAS_THREADS', '1')


def compact(text):
    return re.sub('\\s+', '', unicodedata.normalize('NFKC', text))


def score(results, expected):
    if not expected:
        return None
    hits = []
    for ref in expected:
        matched = any((src.get('paper_id') == ref['paper_id'] and (src['id'] == ref['source_id'] or compact(ref['quote']) in compact(src.get('text', ''))) for src in results[:20]))
        hits.append(matched)
    return sum(hits) / len(hits)


def evaluation_identity(store, questions, mode, baseline_code=None):
    """固定代码、数据与非密钥配置；不以输出文件名或修改时间代替实验身份。"""
    root = Path(__file__).resolve().parents[2]
    dependencies = [__file__]
    for directory in ('search', 'research', 'integrations', 'library', 'core'):
        dependencies.extend(sorted((root / directory).glob('*.py')))
    code = {str(Path(path).relative_to(root)): digest(Path(path).read_text(encoding='utf-8')) for path in dependencies}
    cfg = store.settings()
    config = {key: cfg.get(key) for key in ('model', 'api_base', 'local_text_model', 'local_text_context', 'relation_extractor', 'cloud_enabled')}
    meta = read_json(store.active_dir() / 'corpus_meta.json', {})
    value = {'version': 'retrieval-evaluation-v2', 'questions': digest(questions), 'index_version': store.state('active_version'),
             'mode': mode, 'code': code, 'configuration': config, 'index_metadata': digest(json.dumps(meta, sort_keys=True)),
             'revisions': {key: store.state(key, 0) for key in ('source_revision', 'paper_revision', 'relation_revision')},
             'baseline_code': digest(Path(baseline_code).read_text(encoding='utf-8')) if baseline_code else None}
    if mode != 'baseline':
        from kbapp.integrations.model_identity import embedding_signature, signature
        from kbapp.core.config import OLLAMA
        value['embedding'] = embedding_signature()
        if mode == 'rerank':
            value['local_model'] = signature(cfg['local_text_model'], OLLAMA)
    return value


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--questions', type=Path, default=Path(__file__).resolve().parents[3] / 'tests/fixtures/research_questions.json', help='评测问题 JSON；仓库内默认文件是合成样例，请用 --questions 指向自己的数据集')
    p.add_argument('--mode', choices=['baseline', 'hybrid', 'rerank'], default='hybrid')
    p.add_argument('--baseline-code', type=Path)
    p.add_argument('--limit', type=int)
    p.add_argument('--output', type=Path)
    args = p.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        p.error('--limit 必须为正整数')
    store = Store()
    question_text = args.questions.read_text(encoding='utf-8')
    dataset = json.loads(question_text)
    cases = dataset['questions'][:args.limit]
    engine = Search(store)
    if args.baseline_code:
        spec = importlib.util.spec_from_file_location('kbapp._evaluation_baseline', args.baseline_code)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        engine = module.Search(store)
    rows = []
    target = args.output or store.paths.exports / 'evaluation' / ('retrieval-' + args.mode + '.json')
    prior = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
    identity = evaluation_identity(store, question_text, args.mode, args.baseline_code)
    signature = digest(json.dumps(identity, sort_keys=True, ensure_ascii=False))
    cached = {r['id']: r for r in prior.get('questions', [])} if prior.get('fingerprint') == signature else {}

    def publish():
        measured = [r['recall20'] for r in rows if r['recall20'] is not None]
        report = {'updated': now(), 'fingerprint': signature, 'identity': identity, 'index_version': store.state('active_version'),
                  'mode': args.mode, 'baseline_code': str(args.baseline_code) if args.baseline_code else None,
                  'source_review': dataset.get('source_review'), 'recall20': sum(measured) / len(measured) if measured else None,
                  'evaluated_positive': len(measured), 'answer_audit_pending': sum(r['recall20'] is None for r in rows),
                  'pipeline': 'query-expansion+hybrid+graph-neighbors+llm-rerank' if args.mode == 'rerank' else args.mode,
                  'questions': rows, 'acceptance': 'pending_human_review' if dataset.get('human_review_status') != 'approved' else 'measured'}
        atomic(target, report)

    for case in cases:
        if case['id'] in cached:
            rows.append(cached[case['id']])
        elif not case.get('expected_sources'):
            rows.append({'id': case['id'], 'query': case['query'], 'category': case['category'], 'recall20': None, 'status': 'requires_answer_audit', 'reason': '证据不足题须检查回答边界，不按召回命中计分'})
        else:
            filters = dict(case.get('filters', {}))
            started = time.perf_counter()
            if args.mode == 'rerank':
                found = evidence_search(store, case['query'], filters, complete)
            else:
                filters.update(vectors=args.mode == 'hybrid', wait_vectors=args.mode == 'hybrid')
                found = engine.search(case['query'], k=20, **filters)
            value = score(found['results'], case['expected_sources'])
            expected_papers = {r['paper_id'] for r in case['expected_sources']}
            retrieved = found['results'][:20]
            matching_ranks = [rank for rank, result in enumerate(retrieved, 1) if score([result], case['expected_sources']) > 0]
            rows.append({'id': case['id'], 'query': case['query'], 'category': case['category'], 'mode': found['mode'], 'recall20': value,
                         'paper_recall20': len(expected_papers & {r.get('paper_id') for r in retrieved}) / len(expected_papers),
                         'mrr20': 1 / matching_ranks[0] if matching_ranks else 0.0, 'returned': len(retrieved),
                         'duplicate_rate': 1 - len({(r.get('paper_id'), compact(r.get('text', ''))) for r in retrieved}) / len(retrieved) if retrieved else 0.0,
                         'elapsed_ms': round((time.perf_counter() - started) * 1000, 3), 'timings_ms': found.get('timings_ms', {}),
                         'process': found.get('process', []), 'vector_status': found.get('vector_status'),
                         'results': [{'id': r['id'], 'paper_id': r.get('paper_id')} for r in found['results']], 'warning': found.get('warning', '')})
        publish()
        print(case['id'], rows[-1]['recall20'], flush=True)
    publish()
    print('report', target)
