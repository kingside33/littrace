"""M0 A03 检索性能测量：关键词检索冷/暖、状态与图谱加载、DiskBM25 掩码对比。
只读运行；不触发模型调用（vectors=False）。用法: python measure_search.py <输出.json> <标签>
"""
import json
import os
from pathlib import Path
import statistics
import sys
import time

os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbapp.storage.store import Store  # noqa: E402
from kbapp.search.indexing import Search, DiskBM25  # noqa: E402
from kbapp.workflows.status import status as workbench_status  # noqa: E402
from kbapp.research.graph import chains, overview  # noqa: E402

QUERIES = [
    'Informed RRT*',
    '水下机器人 路径规划',
    '覆盖规划 构型调整',
    'energy-aware A* algorithm underwater',
    '采样 偏差 修正 RRT',
]


def timed(fn, repeat=5, warmup=1):
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return samples


def main(out_path, label):
    store = Store()
    result = {'label': label, 'updated': time.strftime('%Y-%m-%dT%H:%M:%S'), 'sections': {}}

    # 1) 关键词检索（含 Search.load 冷/暖）
    search = Search(store)
    cold_samples = {}
    for q in QUERIES[:2]:
        s = Search(store)  # 强制重新 load
        t0 = time.perf_counter()
        r = s.search(q, k=12, vectors=False)
        cold_samples[q] = {'ms': round((time.perf_counter() - t0) * 1000, 1), 'n_results': len(r['results'])}
    warm = {}
    for q in QUERIES:
        samples = timed(lambda q=q: search.search(q, k=12, vectors=False), repeat=7)
        r = search.search(q, k=12, vectors=False)
        warm[q] = {'p50_ms': round(statistics.median(samples), 1), 'max_ms': round(max(samples), 1),
                   'n_results': len(r['results']), 'mode': r['mode'], 'coverage': r['coverage']}
    result['sections']['keyword_cold'] = cold_samples
    result['sections']['keyword_warm'] = warm

    # 2) 状态摘要与图谱
    result['sections']['status_ms'] = timed(lambda: workbench_status(store), repeat=5, warmup=1)[2:]
    result['sections']['chains_ms'] = timed(lambda: chains(store), repeat=3, warmup=0)
    result['sections']['overview_ms'] = timed(lambda: overview(store), repeat=3, warmup=0)
    ch = chains(store)
    result['sections']['chains_size'] = {'branches': len(ch) if isinstance(ch, list) else 'n/a',
                                         'bytes': len(json.dumps(ch, ensure_ascii=False, default=str))}

    # 3) DiskBM25 掩码对比（F03 直接证据）
    root = store.active_dir()
    bm = DiskBM25(root / 'corpus-search.sqlite3')
    mask = set(range(102199))
    for q in QUERIES[:3]:
        from kbapp.search.ranking import tokenize
        terms = [t.replace('*', 'star') for t in tokenize(q)]
        no_mask = timed(lambda: bm.search(terms, topk=100, allowed=None), repeat=7)
        with_mask = timed(lambda: bm.search(terms, topk=100, allowed=mask), repeat=3, warmup=0)
        result['sections'].setdefault('diskbm25_mask_compare', {})[q] = {
            'no_mask_p50_ms': round(statistics.median(no_mask), 1),
            'with_full_mask_ms': round(statistics.median(with_mask) if with_mask else -1, 1),
        }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
