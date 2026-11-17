"""kbapp.workflows.tools.embed：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import sys
import time
import numpy as np
import kbapp.core.config as C
from kbapp.core.io import read_json, write_json
from kbapp.search.vector_cache import VectorStore, content_key
from kbapp.integrations.embedding import QUERY_INSTRUCTION, check, chunk_hash, embed_texts, truncate_normalize

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--index', default=None)
    ap.add_argument('--model', default=C.EMBED_MODEL)
    ap.add_argument('--dims', type=int, default=C.EMBED_DIMS)
    ap.add_argument('--batch', type=int, default=32)
    ap.add_argument('--full', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)
    if args.check:
        d = check(args.model)
        print(f'[OK] model={args.model} dims={d}')
        sys.exit(0 if d else 1)
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    vec_dir = os.path.join(index, 'vec')
    os.makedirs(vec_dir, exist_ok=True)
    chunks = [json.loads(line) for line in open(os.path.join(index, 'chunks.jsonl'), encoding='utf-8') if line.strip()]
    if not chunks:
        sys.exit('chunks.jsonl 为空，先运行 chunk.py')
    native = check(args.model)
    if not native:
        sys.exit(2)
    if args.dims > native:
        sys.exit(f'--dims {args.dims} 超过模型原生维度 {native}')
    npy_path = os.path.join(vec_dir, 'chunks.npy')
    rows_path = os.path.join(vec_dir, 'rows.json')
    hash_path = os.path.join(vec_dir, 'rows_hash.json')
    old_rows, old_mat, old_hash = ({}, None, {})
    if not args.full and os.path.exists(npy_path) and os.path.exists(rows_path):
        try:
            old_rows = json.load(open(rows_path, encoding='utf-8'))
            old_mat = np.load(npy_path, mmap_mode='r')
            old_hash = read_json(hash_path, {}) or {}
            if old_mat.shape[1] < args.dims:
                print('[info] 旧索引维度不足，改为全量')
                old_rows, old_mat, old_hash = ({}, None, {})
        except Exception as ex:
            print(f'[info] 旧索引不可用（{ex}），改为全量')
            old_rows, old_mat, old_hash = ({}, None, {})
    if not args.full and old_mat is None and os.path.exists(npy_path):
        meta_path = os.path.join(vec_dir, 'chunks_meta.jsonl')
        if os.path.exists(meta_path):
            try:
                old_ids = [json.loads(line)['chunk_id'] for line in open(meta_path, encoding='utf-8') if line.strip()]
                arr = np.load(npy_path, mmap_mode='r')
                if arr.shape[0] == len(old_ids) and arr.shape[1] >= args.dims:
                    old_rows = {cid: i for i, cid in enumerate(old_ids)}
                    old_mat = arr
                    print(f'[bootstrap] 由 chunks_meta.jsonl 恢复 {len(old_rows)} 条行映射')
            except Exception as ex:
                print(f'[info] bootstrap 失败：{ex}')
    bootstrap_hash = bool(old_rows) and (not old_hash)
    n = len(chunks)
    mat = np.zeros((n, args.dims), dtype=np.float32)
    to_embed = []
    for i, c in enumerate(chunks):
        cid = c['chunk_id']
        h = chunk_hash(c)
        r = old_rows.get(cid)
        if r is not None and old_mat is not None and (r < old_mat.shape[0]) and (bootstrap_hash or old_hash.get(cid) == h):
            mat[i] = old_mat[r, :args.dims]
        else:
            to_embed.append(i)
    store = VectorStore(os.path.join(vec_dir, 'shards', 'text'), args.model, args.dims)
    if args.full:
        store.clear()
    still = []
    for i in to_embed:
        v = store.get(content_key(chunks[i]['chunk_id'], chunks[i]['text']))
        if v is not None:
            mat[i] = v
        else:
            still.append(i)
    shard_reused = len(to_embed) - len(still)
    to_embed = still
    reused = n - len(to_embed)
    mode = 'incremental' if old_rows or shard_reused else 'full'
    print(f'[{mode}] 共 {n} 块：复用 {reused}（其中分片 {shard_reused}），待嵌入 {len(to_embed)}', flush=True)
    from kbapp.core.progress import Progress
    bar = Progress(max(1, len(to_embed)), '嵌入 ', enabled=not args.quiet and len(to_embed) > 0)
    t0 = time.time()
    for start in range(0, len(to_embed), args.batch):
        idxs = to_embed[start:start + args.batch]
        keys = [content_key(chunks[i]['chunk_id'], chunks[i]['text']) for i in idxs]
        texts = [chunks[i]['text'] for i in idxs]
        for attempt in range(3):
            try:
                embs = embed_texts(args.model, texts)
                break
            except Exception as ex:
                if attempt == 2:
                    print(f'\n[FAIL] batch @{start}: {ex}')
                    print('本批之前的向量已写成分片，重跑会从断点继续')
                    sys.exit(3)
                time.sleep(2)
        arr = truncate_normalize(np.array(embs, dtype=np.float32), args.dims)
        store.put_batch(keys, arr)
        for k, i in enumerate(idxs):
            mat[i] = arr[k]
        bar.update(len(idxs), note=chunks[idxs[0]]['citekey'])
    bar.close(f'嵌入 {len(to_embed)} 块')
    if old_mat is not None:
        try:
            if hasattr(old_mat, '_mmap') and old_mat._mmap is not None:
                old_mat._mmap.close()
        except Exception:
            pass
        old_mat = None
        import gc
        gc.collect()
    tmp_path = npy_path + '.tmp.npy'
    np.save(tmp_path, mat)
    os.replace(tmp_path, npy_path)
    rows = {c['chunk_id']: i for i, c in enumerate(chunks)}
    write_json(rows_path, rows)
    write_json(hash_path, {c['chunk_id']: chunk_hash(c) for c in chunks})
    with open(os.path.join(vec_dir, 'chunks_meta.jsonl'), 'w', encoding='utf-8') as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + '\n')
    store.clear()
    keys = [c['citekey'] for c in chunks]
    uniq = sorted(set(keys))
    kpos = {k: i for i, k in enumerate(uniq)}
    sums = np.zeros((len(uniq), args.dims), dtype=np.float32)
    cnt = np.zeros(len(uniq), dtype=np.int32)
    for i, k in enumerate(keys):
        sums[kpos[k]] += mat[i]
        cnt[kpos[k]] += 1
    cnt[cnt == 0] = 1
    paper = sums / cnt[:, None]
    norms = np.linalg.norm(paper, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    paper = (paper / norms).astype(np.float32)
    np.save(os.path.join(vec_dir, 'papers.npy'), paper)
    with open(os.path.join(vec_dir, 'papers_meta.json'), 'w', encoding='utf-8') as fh:
        json.dump(uniq, fh, ensure_ascii=False)
    meta = {'model': args.model, 'dims': args.dims, 'chunker_version': C.CHUNKER_VERSION, 'chunk_params': {'target_tokens': C.CHUNK_TARGET_TOKENS, 'overlap_tokens': C.CHUNK_OVERLAP_TOKENS}, 'mode': mode, 'chunks': n, 'embedded_now': len(to_embed), 'reused': reused, 'papers': len(uniq), 'seconds': round(time.time() - t0, 1), 'query_instruction': QUERY_INSTRUCTION}
    with open(os.path.join(vec_dir, 'index_meta.json'), 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
