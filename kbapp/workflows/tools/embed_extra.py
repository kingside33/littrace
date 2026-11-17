"""kbapp.workflows.tools.embed_extra：由职责边界组织的知识库能力。"""
import argparse
import hashlib
import json
import os
import pickle
import sys
import time
import numpy as np
import kbapp.core.config as C
from kbapp.core.io import read_json, read_jsonl, write_json
from kbapp.integrations.embedding import check, embed_texts, truncate_normalize
from kbapp.search.vector_cache import VectorStore, content_key
KINDS = {'figures': {'source': C.figures_jsonl, 'instruction': C.FIG_QUERY_INSTRUCTION, 'text': lambda r: ' '.join((x for x in (r.get('caption', ''), r.get('section', ''), r.get('context', ''), r.get('label', '')) if x))}, 'formulas': {'source': C.formulas_jsonl, 'instruction': C.FORMULA_QUERY_INSTRUCTION, 'text': lambda r: ' '.join((x for x in (f"Equation {r.get('label')}." if r.get('label') else '', r.get('section', ''), r.get('context', ''), r.get('latex', ''), r.get('raw_text', '')) if x))}}

def text_hash(text):
    return hashlib.sha1((text or '').encode('utf-8')).hexdigest()[:12]

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--kind', required=True, choices=sorted(KINDS))
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--index', default=None)
    ap.add_argument('--model', default=C.EMBED_MODEL)
    ap.add_argument('--dims', type=int, default=C.EMBED_DIMS)
    ap.add_argument('--batch', type=int, default=32)
    ap.add_argument('--full', action='store_true')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    if args.check:
        d = check(args.model)
        print(f'[OK] model={args.model} dims={d}')
        sys.exit(0 if d else 1)
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    vec_dir = os.path.join(index, 'vec')
    os.makedirs(vec_dir, exist_ok=True)
    cfg = KINDS[args.kind]
    source = cfg['source'](base)
    records = read_jsonl(source)
    if not records:
        print(f'[skip] {source} 为空，先跑对应的抽取脚本')
        return
    texts = [cfg['text'](r).strip() for r in records]
    for i, t in enumerate(texts):
        if not t:
            texts[i] = records[i].get('id', 'record')
    keys = [content_key(r['id'], t) for r, t in zip(records, texts)]
    hashes = {r['id']: text_hash(t) for r, t in zip(records, texts)}
    npy_path = os.path.join(vec_dir, args.kind + '.npy')
    rows_path = os.path.join(vec_dir, args.kind + '_rows.json')
    hash_path = os.path.join(vec_dir, args.kind + '_rows_hash.json')
    meta_path = os.path.join(vec_dir, args.kind + '_meta.jsonl')
    idx_meta_path = os.path.join(vec_dir, args.kind + '_index_meta.json')
    native = check(args.model)
    if not native:
        sys.exit(2)
    if args.dims > native:
        sys.exit(f'--dims {args.dims} 超过模型原生维度 {native}')
    old_rows, old_mat, old_hash = ({}, None, {})
    prev_meta = read_json(idx_meta_path, {}) or {}
    compatible = prev_meta.get('model') == args.model and int(prev_meta.get('dims', 0)) == args.dims
    if not args.full and compatible and os.path.exists(npy_path) and os.path.exists(rows_path):
        try:
            old_rows = read_json(rows_path, {}) or {}
            old_mat = np.load(npy_path, mmap_mode='r')
            old_hash = read_json(hash_path, {}) or {}
            if old_mat.shape[1] < args.dims:
                old_rows, old_mat, old_hash = ({}, None, {})
        except Exception as ex:
            print(f'[info] 旧索引不可用（{ex}），改为全量')
            old_rows, old_mat, old_hash = ({}, None, {})
    bootstrap_hash = bool(old_rows) and (not old_hash)
    mat = np.zeros((len(records), args.dims), dtype=np.float32)
    to_embed = []
    for i, r in enumerate(records):
        rid = r['id']
        row = old_rows.get(rid)
        if row is not None and old_mat is not None and (row < old_mat.shape[0]) and (bootstrap_hash or old_hash.get(rid) == hashes[rid]):
            mat[i] = old_mat[row, :args.dims]
        else:
            to_embed.append(i)
    store = VectorStore(os.path.join(vec_dir, 'shards', args.kind), args.model, args.dims)
    if args.full:
        store.clear()
    still = []
    for i in to_embed:
        v = store.get(keys[i])
        if v is not None:
            mat[i] = v
        else:
            still.append(i)
    shard_reused = len(to_embed) - len(still)
    to_embed = still
    reused = len(records) - len(to_embed)
    print(f'[{args.kind}] 共 {len(records)} 条：复用 {reused}（分片 {shard_reused}），待嵌入 {len(to_embed)}', flush=True)
    from kbapp.core.progress import Progress
    bar = Progress(max(1, len(to_embed)), f'{args.kind} 嵌入 ', enabled=len(to_embed) > 0)
    t0 = time.time()
    for start in range(0, len(to_embed), args.batch):
        idxs = to_embed[start:start + args.batch]
        batch_texts = [texts[i] for i in idxs]
        batch_keys = [keys[i] for i in idxs]
        for attempt in range(3):
            try:
                embs = embed_texts(args.model, batch_texts)
                break
            except Exception as ex:
                if attempt == 2:
                    print(f'\n[FAIL] batch @{start}: {ex}')
                    print('本批之前的向量已写成分片，重跑会从断点继续')
                    sys.exit(3)
                time.sleep(2)
        arr = truncate_normalize(np.array(embs, dtype=np.float32), args.dims)
        store.put_batch(batch_keys, arr)
        for k, i in enumerate(idxs):
            mat[i] = arr[k]
        bar.update(len(idxs), note=records[idxs[0]]['id'])
    bar.close(f'嵌入 {len(to_embed)} 条')
    if old_mat is not None:
        try:
            if hasattr(old_mat, '_mmap') and old_mat._mmap is not None:
                old_mat._mmap.close()
        except Exception:
            pass
        old_mat = None
        import gc
        gc.collect()
    tmp = npy_path + '.tmp.npy'
    np.save(tmp, mat)
    os.replace(tmp, npy_path)
    rows = {r['id']: i for i, r in enumerate(records)}
    write_json(rows_path, rows)
    write_json(hash_path, hashes)
    with open(meta_path, 'w', encoding='utf-8', newline='\n') as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    from kbapp.search.ranking import BM25, tokenize
    bm = BM25([tokenize(t) for t in texts])
    with open(os.path.join(vec_dir, args.kind + '_bm25.pkl'), 'wb') as fh:
        pickle.dump(bm, fh)
    meta = {'kind': args.kind, 'model': args.model, 'dims': args.dims, 'records': len(records), 'embedded_now': len(to_embed), 'reused': reused, 'shard_reused': shard_reused, 'seconds': round(time.time() - t0, 1), 'query_instruction': cfg['instruction']}
    write_json(idx_meta_path, meta)
    store.clear()
    print(json.dumps(meta, ensure_ascii=False, indent=2))
