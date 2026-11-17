"""kbapp.search.vector_cache：由职责边界组织的知识库能力。"""
import hashlib
import os
import numpy as np
from kbapp.core.io import read_json, write_json

def content_key(record_id, text):
    h = hashlib.sha1((text or '').encode('utf-8')).hexdigest()[:12]
    return f'{record_id}@{h}'

class VectorStore:

    def __init__(self, shard_dir, model, dims):
        self.dir = shard_dir
        self.model = model
        self.dims = int(dims)
        self.meta_path = os.path.join(shard_dir, 'meta.json')
        self.vectors = {}
        self._prepare()

    def _prepare(self):
        meta = read_json(self.meta_path)
        if meta and (meta.get('model') != self.model or int(meta.get('dims', 0)) != self.dims):
            self.clear()
        os.makedirs(self.dir, exist_ok=True)
        write_json(self.meta_path, {'model': self.model, 'dims': self.dims})
        self._load()

    def _load(self):
        try:
            names = os.listdir(self.dir)
        except OSError:
            return
        for fn in names:
            if not fn.endswith('.npz'):
                continue
            path = os.path.join(self.dir, fn)
            try:
                with np.load(path, allow_pickle=False) as z:
                    ids = [str(x) for x in z['ids'].tolist()]
                    vecs = z['vecs']
                if vecs.ndim != 2 or vecs.shape[1] < self.dims:
                    continue
                for i, cid in enumerate(ids):
                    self.vectors[cid] = vecs[i, :self.dims]
            except Exception:
                try:
                    os.remove(path)
                except OSError:
                    pass

    def has(self, key):
        return key in self.vectors

    def get(self, key):
        return self.vectors.get(key)

    def put_batch(self, keys, mat):
        if not keys:
            return
        os.makedirs(self.dir, exist_ok=True)
        h = hashlib.sha1('\x00'.join(keys).encode('utf-8')).hexdigest()[:16]
        path = os.path.join(self.dir, h + '.npz')
        tmp = path + '.tmp.npz'
        np.savez(tmp, ids=np.array(keys, dtype=str), vecs=mat.astype(np.float32))
        os.replace(tmp, path)
        for i, k in enumerate(keys):
            self.vectors[k] = mat[i, :self.dims]

    def clear(self):
        try:
            names = os.listdir(self.dir)
        except OSError:
            return
        for fn in names:
            if fn.endswith('.npz'):
                try:
                    os.remove(os.path.join(self.dir, fn))
                except OSError:
                    pass
        self.vectors = {}
