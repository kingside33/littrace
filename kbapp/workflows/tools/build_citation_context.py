"""kbapp.workflows.tools.build_citation_context：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
import kbapp.core.config as C
from kbapp.core.io import read_jsonl, write_json, write_jsonl
from kbapp.core.progress import Progress
SECTION_TYPE = {'Abstract': 'background', 'Front': 'background', 'Introduction': 'background', 'Related': 'lineage', 'Method': 'method', 'Results': 'baseline', 'Discussion': 'context', 'Conclusion': 'context'}
TYPE_WEIGHT = {'method': 3.0, 'baseline': 2.0, 'lineage': 2.0, 'background': 1.0, 'context': 1.0}
HEADING_RE = re.compile('(?m)^[ \\t]*(?:[IVXLC]{1,5}[.．]|\\d{1,2}(?:\\.\\d{1,2})?[.．]?|[一二三四五六七八九十]{1,3}[、.．])?[ \\t]*(Abstract|Introduction|Related\\s+Work|Related\\s+Research|Literature\\s+Review|Background|Method(?:s|ology)?|Proposed[^\\n]{0,30}|Approach|Results?|Experiments?|Evaluation|Simulations?|Discussion|Conclusions?|Future\\s+Work|摘要|引言|绪论|前言|相关工作|研究现状|文献综述|背景|方法|算法|模型|问题描述|实验|仿真|结果|算例|讨论|结论|总结|展望)[ \\t]*(?:\\n|$)', re.I)
HEAD_MAP = [('^abstract$|^摘要$', 'Abstract'), ('^(introduction|background)$|^(引言|绪论|前言|背景)$', 'Introduction'), ('^related|^literature|^(相关工作|研究现状|文献综述)$', 'Related'), ('^method|^proposed|^approach|^(方法|算法|模型|问题描述)$', 'Method'), ('^results?$|^experiment|^evaluation|^simulation|^(实验|仿真|结果|算例)$', 'Results'), ('^discussion$|^讨论$', 'Discussion'), ('^conclusion|^future|^(结论|总结|展望)$', 'Conclusion')]
CUE_RULES = [('baseline', re.compile('\\b(compar(?:ed?|ing|ison)\\s+(?:with|to|against)|outperform\\w*|baselines?|state[-\\s]?of[-\\s]?the[-\\s]?art|better\\s+than|superior\\s+to|benchmark\\w*|than\\s+(?:the\\s+)?(?:method|approach)\\s+of|in\\s+contrast\\s+to)\\b', re.I)), ('method', re.compile('\\b(based\\s+on|build(?:s|ing)?\\s+(?:up)?on|extend(?:s|ed|ing)?|modif(?:y|ies|ied|ication)|improv(?:e|es|ed|ement)\\s+(?:up)?on|adopt\\w*|employ\\w*|appl(?:y|ies|ied)|utiliz\\w+|leverag\\w+|incorporat\\w+|inherit\\w*|we\\s+(?:use|used|follow|combine|integrate)|follow(?:s|ing)?\\s+the\\s+(?:framework|formulation|approach))\\b', re.I)), ('lineage', re.compile('\\b(similar(?:ly)?|another\\s+(?:line|class|family)|a\\s+family\\s+of|variant\\s+of|related\\s+(?:work|approaches)|alternative\\s+(?:to|approach)|along\\s+this\\s+line)\\b', re.I)), ('background', re.compile('\\b(motivat\\w*|challeng\\w*|survey|review|overview|have\\s+been\\s+(?:widely\\s+)?(?:studied|investigated|applied))\\b', re.I))]
NUM_CITE = re.compile('\\[(\\d{1,3}(?:\\s*[,，\\-–—]\\s*\\d{1,3})*)\\]')
YEAR_CITE = re.compile("([A-Z][A-Za-z\\-']{2,20})(?:\\s+et\\s+al\\.?)?(?:\\s+and\\s+[A-Z][A-Za-z\\-']{2,20})?\\s*[,(]\\s*(19|20)(\\d{2})[a-z]?\\s*\\)?")
DOI_RE = re.compile('10\\.\\d{4,9}/')
PAGES_RE = re.compile('\\b(?:19|20)\\d{2}\\b[^\\n]{0,12}?\\b\\d{2,5}\\s*[–\\-—]\\s*\\d{1,5}\\b')

def norm_surname(s):
    s = unicodedata.normalize('NFKD', s or '')
    s = ''.join((ch for ch in s if not unicodedata.combining(ch)))
    return re.sub('[^a-z]', '', s.lower())

def expand_nums(group):
    out = []
    for part in re.split('\\s*[,，]\\s*', group):
        if not part:
            continue
        m = re.match('^(\\d+)\\s*[\\-–—]\\s*(\\d+)$', part)
        if m:
            a, b = (int(m.group(1)), int(m.group(2)))
            if 0 < b - a <= 30:
                out.extend(range(a, b + 1))
                continue
            out.append(a)
        elif part.isdigit():
            out.append(int(part))
    return out

def looks_like_reflist(text):
    """参考文献表里也有 [12] 这种编号，必须排除，否则整段都在「引用」。"""
    if len(DOI_RE.findall(text)) >= 2:
        return True
    if len(PAGES_RE.findall(text)) >= 3:
        return True
    if text.count('pp.') >= 2 or text.count('et al.,') >= 3:
        return True
    return False

def map_heading(head):
    h = re.sub('\\s+', ' ', head.strip()).lower()
    for pat, sec in HEAD_MAP:
        if re.search(pat, h):
            return sec
    return None

def headings_in(text):
    out = []
    for m in HEADING_RE.finditer(text):
        sec = map_heading(m.group(1))
        if sec:
            out.append((m.start(), sec))
    return out

def section_at(heads, pos, fallback):
    sec = None
    for off, s in heads:
        if off <= pos:
            sec = s
        else:
            break
    return sec or fallback

def sentence_around(text, a, b, maxlen=340):
    start = a
    while start > 0 and start > a - 220:
        if text[start - 1] in '.!?。！？\n':
            break
        start -= 1
    end = b
    while end < len(text) and end < b + 220:
        if text[end] in '.!?。！？\n':
            end += 1
            break
        end += 1
    s = re.sub('\\s+', ' ', text[start:end]).strip()
    return s[:maxlen]

def classify(sentence, sec):
    for typ, rx in CUE_RULES:
        if rx.search(sentence):
            return (typ, 'cue')
    return (SECTION_TYPE.get(sec, 'background'), 'section')

def load_refs(base):
    by_src = defaultdict(dict)
    for r in read_jsonl(C.references_jsonl(base)):
        src = r.get('src_citekey')
        idx = r.get('ref_index')
        if not src or idx is None:
            continue
        by_src[src][int(idx)] = r
    return by_src

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    out_dir = C.citation_dir(base)
    os.makedirs(out_dir, exist_ok=True)
    refs = load_refs(base)
    nrefs = {ck: len(v) for ck, v in refs.items()}
    author_year = defaultdict(dict)
    for ck, entries in refs.items():
        for idx, r in entries.items():
            sur = norm_surname(re.split('[,;，；]', (r.get('authors') or '').strip())[0])
            yr = r.get('year')
            if sur and yr:
                author_year[ck].setdefault(f'{sur}|{yr}', idx)
    pages = defaultdict(lambda: [10 ** 6, 0])
    total = 0
    with open(os.path.join(C.index_dir(base), 'chunks.jsonl'), encoding='utf-8') as fh:
        for line in fh:
            try:
                ch = json.loads(line)
            except Exception:
                continue
            ck = ch.get('citekey')
            ps, pe = (ch.get('page_start'), ch.get('page_end'))
            if ck and ps and pe:
                p = pages[ck]
                p[0] = min(p[0], ps)
                p[1] = max(p[1], pe)
            total += 1
    hits = defaultdict(lambda: defaultdict(Counter))
    meta = defaultdict(dict)
    n_num = n_ay = 0
    n_reflist = 0
    bar = Progress(total, prefix='scan ', enabled=True)
    with open(os.path.join(C.index_dir(base), 'chunks.jsonl'), encoding='utf-8') as fh:
        for line in fh:
            bar.update(1)
            try:
                ch = json.loads(line)
            except Exception:
                continue
            ck = ch.get('citekey')
            text = ch.get('text') or ''
            if not ck or not text or ck not in refs:
                continue
            if looks_like_reflist(text):
                n_reflist += 1
                continue
            heads = headings_in(text)
            fallback = ch.get('section') or 'Front'
            limit = nrefs.get(ck, 0) + 2

            def record(idx, pos, end, sec):
                typ, how = classify(sentence_around(text, pos, end), sec)
                hits[ck][idx][typ] += 1
                if idx not in meta[ck]:
                    meta[ck][idx] = {'section': sec, 'type': typ, 'how': how, 'snippet': sentence_around(text, pos, end), 'page': ch.get('page_start')}
            for m in NUM_CITE.finditer(text):
                sec = section_at(heads, m.start(), fallback)
                for n in expand_nums(m.group(1)):
                    if 0 < n <= limit:
                        record(n, m.start(), m.end(), sec)
                        n_num += 1
            ay = author_year.get(ck) or {}
            if ay:
                for m in YEAR_CITE.finditer(text):
                    sur = norm_surname(m.group(1))
                    yr = int(m.group(2) + m.group(3))
                    for dy in (0, -1, 1):
                        idx = ay.get(f'{sur}|{yr + dy}')
                        if idx:
                            sec = section_at(heads, m.start(), fallback)
                            record(idx, m.start(), m.end(), sec)
                            n_ay += 1
                            break
    bar.close()
    rows = []
    for ck, per_ref in hits.items():
        for idx, counter in per_ref.items():
            typ = max(counter.items(), key=lambda kv: (TYPE_WEIGHT[kv[0]] * kv[1], kv[1]))[0]
            r = refs[ck].get(idx, {})
            m = meta[ck].get(idx, {})
            rows.append({'src': ck, 'ref_index': idx, 'type': typ, 'weight': TYPE_WEIGHT[typ], 'n_markers': sum(counter.values()), 'types_seen': dict(counter), 'section': m.get('section'), 'how': m.get('how'), 'page': m.get('page'), 'ref_title': r.get('title', ''), 'ref_doi': r.get('doi', ''), 'ref_year': r.get('year'), 'snippet': m.get('snippet', '')})
    rows.sort(key=lambda r: (r['src'], r['ref_index']))
    write_jsonl(os.path.join(out_dir, 'contexts.jsonl'), rows)
    stats = {'papers_with_refs': len(refs), 'refs_with_context': len(rows), 'numeric_markers': n_num, 'author_year_markers': n_ay, 'skipped_reflist_chunks': n_reflist, 'by_type': dict(Counter((r['type'] for r in rows))), 'by_method': dict(Counter((r['how'] for r in rows)))}
    write_json(os.path.join(out_dir, 'context_stats.json'), stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
