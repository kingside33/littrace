"""kbapp.workflows.tools.verify：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from kbapp.core.config import DIR_NAME
BASE = str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().base)
DIRECTIONS = list(DIR_NAME.values())

def parse_frontmatter(text):
    """极简 YAML 解析：只处理我们生成的标量 / 行内列表格式。"""
    if not text.startswith('---'):
        return None
    end = text.find('\n---', 3)
    if end < 0:
        return None
    block = text[3:end].strip('\n')
    out = {}
    for line in block.split('\n'):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if ':' not in line:
            return None
        k, v = line.split(':', 1)
        k, v = (k.strip(), v.strip())
        if v.startswith('[') and v.endswith(']'):
            try:
                out[k] = json.loads(v)
            except Exception:
                out[k] = v
        elif v == 'null':
            out[k] = None
        elif v in ('true', 'false'):
            out[k] = v == 'true'
        elif v.isdigit():
            out[k] = int(v)
        elif v.startswith('"') and v.endswith('"'):
            try:
                out[k] = json.loads(v)
            except Exception:
                out[k] = v.strip('"')
        else:
            out[k] = v
    return out

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=BASE)
    ap.add_argument('--index', default=str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().active_index()))
    ap.add_argument('--fix-report', action='store_true')
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index)
    lit = os.path.join(base, '_知识库', '10_文献')
    errors = []
    warnings = []
    cards = []
    citekeys = Counter()
    dois = defaultdict(list)
    for root, _, files in os.walk(lit):
        for fn in files:
            if not fn.endswith('.md'):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, base).replace(os.sep, '/')
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
            fm = parse_frontmatter(text)
            if fm is None:
                errors.append({'file': rel, 'issue': 'frontmatter 无法解析'})
                continue
            ck = fm.get('citekey')
            if not ck:
                errors.append({'file': rel, 'issue': '缺少 citekey'})
                continue
            if os.path.splitext(fn)[0] != ck:
                warnings.append({'file': rel, 'issue': f'文件名与 citekey 不一致: {ck}'})
            citekeys[ck] += 1
            if fm.get('doi'):
                dois[str(fm['doi']).lower()].append(ck)
            pdf = fm.get('pdf')
            loc = fm.get('pdf_location')
            if pdf and loc != 'zotero-storage':
                full = pdf if os.path.isabs(pdf) else os.path.join(base, pdf)
                if not os.path.exists(full):
                    errors.append({'file': rel, 'issue': f'PDF 不存在: {pdf}'})
            cards.append({'citekey': ck, 'file': rel, 'fm': fm})
    for ck, n in citekeys.items():
        if n > 1:
            errors.append({'file': '-', 'issue': f'citekey 重复 {n} 次: {ck}'})
    dup_doi = {d: ks for d, ks in dois.items() if len(set(ks)) > 1}
    for d, ks in dup_doi.items():
        warnings.append({'file': '-', 'issue': f"DOI 重复 {d}: {', '.join(sorted(set(ks)))}"})
    missing_dirs = []
    for d in DIRECTIONS:
        p = os.path.join(base, '_知识库', '20_方向', d + '.md')
        if not os.path.exists(p):
            missing_dirs.append(d)
    index_files = ['llms.txt', 'compact.md', 'papers_all.jsonl', 'directions.json', 'chunks.jsonl', 'figures.jsonl', 'formulas.jsonl', 'references.jsonl', 'citation_graph.json']
    missing_index = [f for f in index_files if not os.path.exists(os.path.join(index, f))]
    papers = [json.loads(line) for line in open(os.path.join(index, 'papers_all.jsonl'), encoding='utf-8') if line.strip()]
    card_keys = set(citekeys)
    no_card = [p['citekey'] for p in papers if p['citekey'] not in card_keys]

    def jsonl_rows(path):
        if not os.path.exists(path):
            return []
        return [json.loads(line) for line in open(path, encoding='utf-8') if line.strip()]
    chunk_keys = set()
    cpath = os.path.join(index, 'chunks.jsonl')
    if os.path.exists(cpath):
        for line in open(cpath, encoding='utf-8'):
            if line.strip():
                chunk_keys.add(json.loads(line)['citekey'])
    with_pdf = [p['citekey'] for p in papers if p.get('pdf')]
    with_pdf_set = set(with_pdf)
    no_chunks = [k for k in with_pdf if k not in chunk_keys]
    fig_rows = jsonl_rows(os.path.join(index, 'figures.jsonl'))
    eq_rows = jsonl_rows(os.path.join(index, 'formulas.jsonl'))
    ref_rows = jsonl_rows(os.path.join(index, 'references.jsonl'))
    fig_keys = {r['citekey'] for r in fig_rows if r.get('citekey')}
    eq_keys = {r['citekey'] for r in eq_rows if r.get('citekey')}
    ref_keys = {r['src_citekey'] for r in ref_rows if r.get('src_citekey')}
    missing_fig_files = 0
    for r in fig_rows:
        f = r.get('file')
        if not f:
            continue
        full = f if os.path.isabs(f) else os.path.join(base, f)
        if not os.path.exists(full):
            missing_fig_files += 1
    missing_eq_files = 0
    for r in eq_rows:
        f = r.get('file')
        if not f:
            continue
        full = f if os.path.isabs(f) else os.path.join(base, f)
        if not os.path.exists(full):
            missing_eq_files += 1
    ocr_pages = 0
    ocr_dir = os.path.join(index, 'ocr_pages')
    if os.path.isdir(ocr_dir):
        ocr_pages = sum((1 for fn in os.listdir(ocr_dir) if fn.endswith('.json')))
    graph = {}
    gp = os.path.join(index, 'citation_graph.json')
    if os.path.exists(gp):
        try:
            graph = json.load(open(gp, encoding='utf-8'))
        except Exception:
            graph = {}
    graph_nodes = len(graph.get('nodes', []))
    graph_edges = len(graph.get('edges', []))
    self_cites = sum((1 for e in graph.get('edges', []) if e.get('src') == e.get('dst')))
    evo = {}
    ep = os.path.join(index, 'citation', 'evolution_stats.json')
    if os.path.exists(ep):
        try:
            evo = json.load(open(ep, encoding='utf-8'))
        except Exception:
            evo = {}
    fig_cov = round(100 * len(fig_keys & with_pdf_set) / max(1, len(with_pdf)), 1)
    eq_cov = round(100 * len(eq_keys & with_pdf_set) / max(1, len(with_pdf)), 1)
    ref_cov = round(100 * len(ref_keys & with_pdf_set) / max(1, len(with_pdf)), 1)
    if fig_cov < 80:
        warnings.append({'file': '-', 'issue': f'图表覆盖不足：{fig_cov}% < 80%'})
    if eq_cov < 50:
        warnings.append({'file': '-', 'issue': f'公式覆盖不足：{eq_cov}% < 50%'})
    if ref_cov < 60:
        warnings.append({'file': '-', 'issue': f'参考文献解析覆盖不足：{ref_cov}% < 60%'})
    if missing_fig_files:
        warnings.append({'file': '-', 'issue': f'图表 PNG 缺失 {missing_fig_files} 个'})
    if missing_eq_files:
        warnings.append({'file': '-', 'issue': f'公式 PNG 缺失 {missing_eq_files} 个'})
    if graph and graph_nodes != len(papers):
        warnings.append({'file': '-', 'issue': f'引文图节点数 {graph_nodes} != 文献数 {len(papers)}'})
    if self_cites:
        warnings.append({'file': '-', 'issue': f'引文图存在自引边 {self_cites} 条'})
    report = {'cards': len(cards), 'papers_in_index': len(papers), 'citekey_unique': len(citekeys) == len(cards), 'errors': len(errors), 'warnings': len(warnings), 'missing_direction_pages': missing_dirs, 'missing_index_files': missing_index, 'papers_without_card': len(no_card), 'papers_with_pdf': len(with_pdf), 'papers_with_pdf_but_no_chunks': len(no_chunks), 'chunk_coverage': round(100 * (len(with_pdf) - len(no_chunks)) / max(1, len(with_pdf)), 1), 'dup_doi_groups': len(dup_doi), 'figures': len(fig_rows), 'figure_papers': len(fig_keys), 'figure_coverage': fig_cov, 'formulas': len(eq_rows), 'formula_papers': len(eq_keys), 'formula_coverage': eq_cov, 'formulas_with_latex': sum((1 for r in eq_rows if r.get('latex'))), 'refs': len(ref_rows), 'ref_papers': len(ref_keys), 'ref_coverage': ref_cov, 'citation_nodes': graph_nodes, 'citation_edges': graph_edges, 'evo_nodes': evo.get('nodes_shown', 0), 'evo_edges': evo.get('edges_shown', 0), 'evo_library_with_upstream': evo.get('library_with_upstream', 0), 'evo_library_isolated': evo.get('library_isolated', 0), 'evo_chains': evo.get('directions_with_backbone', 0), 'ocr_pages': ocr_pages}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        print('\n错误（前 20）:')
        for e in errors[:20]:
            print(f"  - {e['file']}: {e['issue']}")
    if warnings:
        print('\n警告（前 10）:')
        for w in warnings[:10]:
            print(f"  - {w['file']}: {w['issue']}")
    if no_chunks:
        print('\n有 PDF 但未切块（前 10）:')
        for k in no_chunks[:10]:
            print(f'  - {k}')
    if args.fix_report:
        with open(os.path.join(index, 'verify_report.json'), 'w', encoding='utf-8') as fh:
            json.dump({'summary': report, 'errors': errors, 'warnings': warnings, 'no_chunks': no_chunks, 'no_card': no_card}, fh, ensure_ascii=False, indent=2)
    sys.exit(1 if errors else 0)
