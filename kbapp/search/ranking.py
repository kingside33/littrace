"""kbapp.search.ranking：由职责边界组织的知识库能力。"""
import json
import math
import os
import pickle
import re
from collections import Counter, defaultdict
import kbapp.core.config as C
CJK = re.compile('[\\u4e00-\\u9fff]')
LATIN = re.compile('[a-zA-Z][a-zA-Z0-9\\-]{1,}')
STOP = set('the a an of on in for and or to with by from as at is are was were be been this that these those we our\n    it its their there which using used based method methods approach paper results show shows proposed'.split())
MODALITIES = {'text': {'source': 'chunks.jsonl', 'vec': 'chunks.npy', 'rows': 'rows.json', 'bm25': 'bm25.pkl', 'instruction': C.QUERY_INSTRUCTION, 'text': lambda r: r.get('text', '')}, 'figures': {'source': 'figures.jsonl', 'vec': 'figures.npy', 'rows': 'figures_rows.json', 'bm25': 'figures_bm25.pkl', 'instruction': C.FIG_QUERY_INSTRUCTION, 'text': lambda r: ' '.join((x for x in (r.get('caption', ''), r.get('section', ''), r.get('context', ''), r.get('label', '')) if x))}, 'formulas': {'source': 'formulas.jsonl', 'vec': 'formulas.npy', 'rows': 'formulas_rows.json', 'bm25': 'formulas_bm25.pkl', 'instruction': C.FORMULA_QUERY_INSTRUCTION, 'text': lambda r: ' '.join((x for x in (f"Equation {r.get('label')}." if r.get('label') else '', r.get('section', ''), r.get('context', ''), r.get('latex', ''), r.get('raw_text', '')) if x))}}

def tokenize(text):
    toks = []
    for m in LATIN.finditer(text):
        w = m.group(0).lower()
        if w not in STOP and len(w) > 1:
            toks.append(w)
    chars = CJK.findall(text)
    for i in range(len(chars) - 1):
        toks.append(chars[i] + chars[i + 1])
    if len(chars) == 1:
        toks.append(chars[0])
    return toks

class BM25:

    def __init__(self, docs_tokens, k1=1.2, b=0.75):
        self.k1, self.b = (k1, b)
        self.N = len(docs_tokens)
        self.doc_len = [len(t) for t in docs_tokens]
        self.avgdl = sum(self.doc_len) / max(1, self.N)
        self.tf = []
        self.df = Counter()
        for toks in docs_tokens:
            c = Counter(toks)
            self.tf.append(c)
            for t in c:
                self.df[t] += 1
        self.idf = {t: math.log(1 + (self.N - d + 0.5) / (d + 0.5)) for t, d in self.df.items()}

    def search(self, query_tokens, topk=50, phrases=None, texts=None, allowed=None):
        scores = defaultdict(float)
        for t in set(query_tokens):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, c in enumerate(self.tf):
                if allowed is not None and i not in allowed:
                    continue
                f = c.get(t)
                if not f:
                    continue
                dl = self.doc_len[i] or 1
                scores[i] += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        if phrases and texts:
            for i in scores:
                bonus = 0.0
                low = texts[i].lower()
                for ph in phrases:
                    if ph in low:
                        bonus += 3.0 * len(ph)
                if bonus:
                    scores[i] += bonus
        return sorted(scores.items(), key=lambda kv: -kv[1])[:topk]

def load_records(index, modality):
    p = os.path.join(index, MODALITIES[modality]['source'])
    if not os.path.exists(p):
        return []
    with open(p, encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]

def build_bm25(index, modality, records, texts):
    cfg = MODALITIES[modality]
    cache = os.path.join(index, 'vec', cfg['bm25'])
    src = os.path.join(index, cfg['source'])
    if os.path.exists(cache) and os.path.exists(src):
        if os.path.getmtime(cache) >= os.path.getmtime(src):
            try:
                with open(cache, 'rb') as fh:
                    return pickle.load(fh)
            except Exception:
                pass
    bm = BM25([tokenize(t) for t in texts])
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    try:
        with open(cache, 'wb') as fh:
            pickle.dump(bm, fh)
    except Exception:
        pass
    return bm

def embed_query(text, model, instruction):
    import urllib.request
    payload = json.dumps({'model': model, 'input': [instruction + text]}).encode('utf-8')
    req = urllib.request.Request(C.OLLAMA + '/api/embed', data=payload, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode('utf-8'))
    import numpy as np
    return np.array(d['embeddings'][0], dtype=np.float32)

def vector_search(index, modality, qvec, dims, topk=50, allowed=None):
    import numpy as np
    cfg = MODALITIES[modality]
    path = os.path.join(index, 'vec', cfg['vec'])
    if not os.path.exists(path):
        return []
    mat = np.load(path, mmap_mode='r')
    q = qvec[:dims]
    q = q / (np.linalg.norm(q) or 1.0)
    sub = np.asarray(mat[:, :dims], dtype=np.float32)
    scores = sub @ q
    if allowed is not None:
        mask = np.zeros(len(scores), dtype=bool)
        if allowed:
            mask[list(allowed)] = True
        scores = np.where(mask, scores, -1000000000.0)
    order = np.argpartition(-scores, min(topk, len(scores) - 1))[:topk]
    order = order[np.argsort(-scores[order])]
    return [(int(i), float(scores[i])) for i in order if scores[i] > -100000000.0]

def rrf_multi(rank_lists, k=60):
    agg = defaultdict(float)
    for mod, lst in rank_lists:
        for rank, (idx, _) in enumerate(lst):
            agg[mod, idx] += 1.0 / (k + rank + 1)
    return sorted(agg.items(), key=lambda kv: -kv[1])

def snippet(text, query, width=240):
    terms = [t for t in tokenize(query) if len(t) > 1][:12]
    pos = -1
    for t in terms:
        p = text.lower().find(t)
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        return text[:width].replace('\n', ' ') + ('…' if len(text) > width else '')
    s = max(0, pos - width // 3)
    frag = text[s:s + width].replace('\n', ' ')
    return ('…' if s > 0 else '') + frag + '…'

def parse_year_filter(s):
    if not s:
        return None
    if '-' in s:
        a, b = s.split('-', 1)
        try:
            return (int(a), int(b))
        except ValueError:
            return None
    vals = {int(x) for x in re.split('[,，]', s) if x.strip().isdigit()}
    return vals or None

def allowed_indices(records, papers_meta, args):
    year_f = parse_year_filter(args.year)
    if not (year_f or args.direction or args.section or args.priority):
        return None
    allowed = set()
    for i, r in enumerate(records):
        ck = r.get('citekey')
        meta = papers_meta.get(ck) or {}
        if year_f:
            y = meta.get('year') or r.get('year')
            if not y:
                continue
            if isinstance(year_f, tuple):
                if not year_f[0] <= y <= year_f[1]:
                    continue
            elif y not in year_f:
                continue
        if args.direction:
            d = meta.get('primary_direction') or ''
            if not d.startswith(args.direction):
                continue
        if args.section:
            if not (r.get('section') or '').lower().startswith(args.section.lower()):
                continue
        if args.priority:
            tf = meta.get('tagfields') or {}
            if args.priority not in (tf.get('priority') or []):
                continue
        allowed.add(i)
    return allowed

def load_papers_meta(index):
    p = os.path.join(index, 'papers_all.jsonl')
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding='utf-8') as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                out[r['citekey']] = r
    return out

def format_text(r, score, query):
    return {'modality': 'text', 'citekey': r['citekey'], 'section': r.get('section'), 'pages': f"{r.get('page_start')}-{r.get('page_end')}", 'score': round(score, 5), 'snippet': snippet(r.get('text', ''), query), 'chunk_id': r.get('chunk_id')}

def format_figure(r, score):
    return {'modality': 'figures', 'citekey': r['citekey'], 'id': r.get('id'), 'kind': r.get('kind'), 'label': r.get('label'), 'page': r.get('page'), 'section': r.get('section'), 'caption': r.get('caption'), 'file': r.get('file'), 'score': round(score, 5)}

def format_formula(r, score, query):
    return {'modality': 'formulas', 'citekey': r['citekey'], 'id': r.get('id'), 'label': r.get('label'), 'page': r.get('page'), 'section': r.get('section'), 'latex': r.get('latex', ''), 'raw_text': r.get('raw_text', ''), 'context': snippet(r.get('context', ''), query), 'file': r.get('file'), 'score': round(score, 5)}
