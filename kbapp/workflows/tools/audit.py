"""kbapp.workflows.tools.audit：由职责边界组织的知识库能力。"""
import argparse
import difflib
import json
import os
import re
import statistics
import sys
import unicodedata
from collections import Counter, defaultdict
import kbapp.core.config as C
from kbapp.core.io import read_json, read_jsonl, write_json, write_text

def pct(a, b):
    return round(100 * a / b, 1) if b else 0.0

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--index', default=None)
    ap.add_argument('--skip-ocr-probe', action='store_true')
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    papers = read_jsonl(os.path.join(index, 'papers_all.jsonl'))
    chunks = read_jsonl(os.path.join(index, 'chunks.jsonl'))
    figs = read_jsonl(os.path.join(index, 'figures.jsonl'))
    eqs = read_jsonl(os.path.join(index, 'formulas.jsonl'))
    refs = read_jsonl(os.path.join(index, 'references.jsonl'))
    graph = read_json(os.path.join(index, 'citation_graph.json'), {}) or {}
    creport = read_json(os.path.join(index, 'citation_report.json'), {}) or {}
    cards_stats = read_json(os.path.join(index, 'cards_stats.json'), {}) or {}
    orphans = read_json(os.path.join(index, 'orphan_pdfs.json'), []) or []
    dup_pdfs = read_json(os.path.join(index, 'duplicate_pdfs.json'), []) or []
    dup_cards = read_json(os.path.join(index, 'duplicate_cards.json'), []) or []
    chunk_failures = read_json(os.path.join(index, 'chunk_failures.json'), []) or []
    meta = {p['citekey']: p for p in papers}
    with_pdf = [p['citekey'] for p in papers if p.get('pdf')]
    with_pdf_set = set(with_pdf)
    chunk_keys = {c['citekey'] for c in chunks if c.get('citekey')}
    no_chunks = sorted(with_pdf_set - chunk_keys)
    no_text = [f for f in chunk_failures if f.get('error') == 'no_text']
    bad_pdf = [f for f in chunk_failures if f.get('error') == 'bad_pdf' or str(f.get('error', '')).startswith('open_error')]
    text_layer = {'papers': len(papers), 'with_pdf': len(with_pdf), 'chunked': len(with_pdf_set & chunk_keys), 'chunks': len(chunks), 'coverage': pct(len(with_pdf_set & chunk_keys), len(with_pdf)), 'no_chunks': no_chunks[:30], 'no_chunks_total': len(no_chunks), 'no_text': [f['citekey'] for f in no_text], 'no_text_total': len(no_text), 'bad_pdf': [f['citekey'] for f in bad_pdf], 'bad_pdf_total': len(bad_pdf)}
    fig_by = defaultdict(int)
    fig_kind = Counter()
    for r in figs:
        fig_by[r.get('citekey')] += 1
        fig_kind[r.get('kind')] += 1
    fig_counts = list(fig_by.values())
    zero_figs = [{'citekey': ck, 'title': (meta.get(ck) or {}).get('title', '')[:80]} for ck in sorted(with_pdf_set - set(fig_by))]
    figure_layer = {'records': len(figs), 'papers_with': len(fig_by), 'coverage': pct(len(set(fig_by) & with_pdf_set), len(with_pdf)), 'per_paper_median': round(statistics.median(fig_counts), 1) if fig_counts else 0, 'per_paper_max': max(fig_counts) if fig_counts else 0, 'kinds': dict(fig_kind), 'zero_figure_papers': len(zero_figs), 'zero_figure_sample': zero_figs[:20]}
    eq_by = defaultdict(int)
    for r in eqs:
        eq_by[r.get('citekey')] += 1
    zero_eq = [{'citekey': ck, 'title': (meta.get(ck) or {}).get('title', '')[:80]} for ck in sorted(with_pdf_set - set(eq_by))]
    formula_layer = {'records': len(eqs), 'papers_with': len(eq_by), 'coverage': pct(len(set(eq_by) & with_pdf_set), len(with_pdf)), 'with_latex': sum((1 for r in eqs if r.get('latex'))), 'zero_formula_papers': len(zero_eq), 'zero_formula_sample': zero_eq[:20]}
    ref_by = defaultdict(int)
    for r in refs:
        ref_by[r.get('src_citekey')] += 1
    citation_layer = {'refs': len(refs), 'papers_with_refs': len(ref_by), 'coverage': pct(len(set(ref_by) & with_pdf_set), len(with_pdf)), 'matched_main': creport.get('matched_main', 0), 'matched_lowconf': creport.get('matched_lowconf', 0), 'unmatched': creport.get('unmatched', 0), 'match_rate': creport.get('match_rate', 0), 'edges': len(graph.get('edges', [])), 'nodes': len(graph.get('nodes', [])), 'isolated': creport.get('isolated', 0), 'self_cites': creport.get('self_cites', 0), 'top_cited': creport.get('top_cited', [])[:10]}
    sources = Counter((p.get('metadata_source') for p in papers))
    doi_counter = Counter(((p.get('doi') or '').strip().lower() for p in papers if (p.get('doi') or '').strip()))
    dup_doi = sum((1 for _d, n in doi_counter.items() if n > 1))
    unique_orphans = max(0, len(orphans) - len(dup_pdfs))
    covered_orphans = sources.get('old-zotero', 0) + sources.get('filename', 0)

    def _norm(s):
        s = unicodedata.normalize('NFKC', s or '').lower()
        return re.sub('[^0-9a-z\\u4e00-\\u9fff]+', '', s)
    titles = [(p['citekey'], _norm(p.get('title'))) for p in papers]
    have_pdf = {os.path.normcase(os.path.normpath(p['pdf'])) for p in papers if p.get('pdf')}
    dup_rels = {d['rel'] for d in dup_pdfs}
    exact_uncovered = [o['rel'] for o in orphans if o['rel'] not in dup_rels and os.path.normcase(os.path.normpath(o['rel'])) not in have_pdf]
    named_dups = 0
    true_uncovered = []
    for rel in exact_uncovered:
        stem = _norm(os.path.splitext(os.path.basename(rel))[0])
        best = 0.0
        for _ck, t in titles:
            if not t:
                continue
            if len(t) >= 10 and (t in stem or stem in t):
                best = 1.0
                break
            ratio = difflib.SequenceMatcher(None, stem, t).ratio()
            if ratio > best:
                best = ratio
        if best >= 0.85:
            named_dups += 1
        else:
            true_uncovered.append(rel)
    metadata_layer = {'sources': dict(sources), 'duplicate_doi_groups': dup_doi, 'duplicate_pdf_files': len(dup_pdfs), 'duplicate_records_merged': len(dup_cards), 'orphan_pdfs_raw': len(orphans), 'orphan_unique_after_dedup': unique_orphans, 'orphan_covered_by_cards': covered_orphans, 'orphan_named_duplicates': named_dups, 'orphan_uncovered': len(true_uncovered), 'orphan_uncovered_list': true_uncovered[:20], 'filename_unparsed': cards_stats.get('filename_unparsed', 0)}
    ocr_dir = os.path.join(index, 'ocr_pages')
    ocr_papers = 0
    ocr_pages = 0
    if os.path.isdir(ocr_dir):
        for fn in os.listdir(ocr_dir):
            if fn.endswith('.json'):
                ocr_papers += 1
                try:
                    ocr_pages += len(json.load(open(os.path.join(ocr_dir, fn), encoding='utf-8')))
                except Exception:
                    pass
    ocr_probe = {}
    if not args.skip_ocr_probe:
        try:
            from kbapp.integrations.ocr import GLMOCR
            ocr_probe = GLMOCR().probe()
        except Exception as ex:
            ocr_probe = {'ok': False, 'detail': f'{type(ex).__name__}: {ex}'}
    ocr_layer = {'available': bool(ocr_probe.get('ok')), 'endpoint': ocr_probe.get('url', C.GLM_OCR_URL), 'model': ocr_probe.get('model', C.GLM_OCR_MODEL), 'detail': ocr_probe.get('detail', '未探测'), 'ocr_papers': ocr_papers, 'ocr_pages': ocr_pages, 'formulas_with_latex': formula_layer['with_latex']}
    gaps = []
    if text_layer['bad_pdf_total']:
        gaps.append({'priority': '高', 'gap': f"{text_layer['bad_pdf_total']} 个 PDF 文件损坏（0 页/无法打开）", 'fix': '重新下载或替换对应 PDF，然后重跑 map_pdfs.py 与 chunk.py'})
    if text_layer['no_text_total'] and (not ocr_pages):
        gaps.append({'priority': '高', 'gap': f"{text_layer['no_text_total']} 篇扫描件无文字层", 'fix': '启动 GLM-OCR 后跑 ocr_pages.py，再跑 chunk.py'})
    if figure_layer['coverage'] < 80:
        gaps.append({'priority': '高', 'gap': f"图表覆盖仅 {figure_layer['coverage']}%", 'fix': 'python kb.py data tool extract_figures（全库增量）'})
    if formula_layer['coverage'] < 50:
        gaps.append({'priority': '高', 'gap': f"公式覆盖仅 {formula_layer['coverage']}%", 'fix': 'python kb.py data tool extract_formulas'})
    if formula_layer['records'] and (not formula_layer['with_latex']):
        gaps.append({'priority': '中', 'gap': '公式只有截图，没有 LaTeX 源码', 'fix': '启动 GLM-OCR 后跑 ocr_formulas.py，再跑 embed_extra.py --kind formulas'})
    if formula_layer['records'] and formula_layer['with_latex'] < formula_layer['records']:
        gaps.append({'priority': '低', 'gap': f"{formula_layer['records'] - formula_layer['with_latex']} 条公式还没有 LaTeX", 'fix': '继续跑 ocr_formulas.py（可加 --max-per-paper 提高覆盖），再跑 embed_extra.py --kind formulas'})
    if citation_layer['coverage'] < 60:
        gaps.append({'priority': '中', 'gap': f"参考文献解析覆盖仅 {citation_layer['coverage']}%", 'fix': '检查扫描件/中文论文的 References 标题识别；可对失败论文单独跑 extract_refs.py --citekey'})
    if dup_doi:
        gaps.append({'priority': '中', 'gap': f'{dup_doi} 组重复 DOI 仍会产生重复卡片/检索结果', 'fix': '在 Zotero 合并重复条目后重建'})
    if metadata_layer['filename_unparsed']:
        gaps.append({'priority': '低', 'gap': f"{metadata_layer['filename_unparsed']} 条文件名元数据无法解析", 'fix': '导入 _index/import/orphans.ris 到 Zotero 后重建'})
    gaps += [{'priority': '低', 'gap': '没有外部引文补全（只匹配库内）', 'fix': '需要时接 Crossref/OpenAlex 补 DOI 与外部引文'}, {'priority': '低', 'gap': '没有图内容视觉描述（只索引图注+上下文）', 'fix': '需要时接本地视觉模型生成图内容描述'}, {'priority': '低', 'gap': '检索只有 RRF，没有 cross-encoder 重排', 'fix': '需要时加本地 reranker'}, {'priority': '低', 'gap': '没有 MCP / HTTP 检索封装', 'fix': '需要时把 kb_search.py --json 包成 MCP tool'}]
    audit = {'generated': 'audit.py', 'base': base, 'text': text_layer, 'figures': figure_layer, 'formulas': formula_layer, 'citations': citation_layer, 'metadata': metadata_layer, 'ocr': ocr_layer, 'gaps': gaps}
    write_json(os.path.join(index, 'kbapp.workflows.tools.audit.json'), audit)
    L = []
    L.append('# 文献索引完整性审计')
    L.append('')
    L.append(f"- 文献总数：**{text_layer['papers']}**，其中有 PDF：**{text_layer['with_pdf']}**")
    L.append(f"- 文本块：**{text_layer['chunks']:,}**，覆盖 **{text_layer['coverage']}%**")
    L.append(f"- 图表：**{figure_layer['records']:,}** 条，覆盖 **{figure_layer['coverage']}%** 的有 PDF 文献")
    L.append(f"- 公式：**{formula_layer['records']:,}** 条，覆盖 **{formula_layer['coverage']}%**，其中 LaTeX **{formula_layer['with_latex']}** 条")
    L.append(f"- 参考文献：**{citation_layer['refs']:,}** 条，覆盖 **{citation_layer['coverage']}%**，库内匹配率 **{citation_layer['match_rate']}%**")
    L.append(f"- 引文图：**{citation_layer['nodes']}** 节点 / **{citation_layer['edges']:,}** 边")
    L.append('')
    L.append('## 逐层覆盖')
    L.append('')
    L.append('| 层 | 有 PDF 文献覆盖 | 记录数 | 说明 |')
    L.append('|---|---|---|---|')
    L.append(f"| 文本 | {text_layer['coverage']}% | {text_layer['chunks']:,} 块 | 无文字层 {text_layer['no_text_total']} 篇，坏 PDF {text_layer['bad_pdf_total']} 个 |")
    L.append(f"| 图表 | {figure_layer['coverage']}% | {figure_layer['records']:,} 条 | 每篇中位数 {figure_layer['per_paper_median']}，零图 {figure_layer['zero_figure_papers']} 篇 |")
    L.append(f"| 公式 | {formula_layer['coverage']}% | {formula_layer['records']:,} 条 | LaTeX {formula_layer['with_latex']} 条，零公式 {formula_layer['zero_formula_papers']} 篇 |")
    L.append(f"| 引文 | {citation_layer['coverage']}% | {citation_layer['refs']:,} 条 | 匹配率 {citation_layer['match_rate']}%，孤立节点 {citation_layer['isolated']} |")
    L.append('')
    if citation_layer['top_cited']:
        L.append('## 库内高被引 Top 10')
        L.append('')
        for i, r in enumerate(citation_layer['top_cited'], 1):
            ck = r.get('citekey')
            title = (meta.get(ck) or {}).get('title', '')[:70]
            L.append(f"{i}. `{ck}`（被引 {r.get('in_degree')}）— {title}")
        L.append('')
    L.append('## 元数据与孤儿 PDF')
    L.append('')
    L.append(f"- 元数据来源：{metadata_layer['sources']}")
    L.append(f"- 重复 DOI 组：{metadata_layer['duplicate_doi_groups']}")
    L.append(f"- 重复 PDF 文件：{metadata_layer['duplicate_pdf_files']}")
    L.append(f"- 孤儿 PDF：原始 {metadata_layer['orphan_pdfs_raw']}，去重后 {metadata_layer['orphan_unique_after_dedup']}，已入库 {metadata_layer['orphan_covered_by_cards']}，同名副本 {metadata_layer['orphan_named_duplicates']}，未覆盖 {metadata_layer['orphan_uncovered']}")
    L.append('')
    L.append('## OCR')
    L.append('')
    L.append(f"- GLM-OCR：{('可用' if ocr_layer['available'] else '不可用')}（{ocr_layer['endpoint']}，{ocr_layer['detail']}）")
    L.append(f"- 已 OCR 论文 {ocr_layer['ocr_papers']} 篇 / {ocr_layer['ocr_pages']} 页")
    L.append(f"- 公式 LaTeX：{formula_layer['with_latex']} 条（{pct(formula_layer['with_latex'], formula_layer['records'])}%）")
    L.append('')
    L.append('## 仍缺能力（按优先级）')
    L.append('')
    for g in gaps:
        L.append(f"- **[{g['priority']}]** {g['gap']} — {g['fix']}")
    L.append('')
    write_text(os.path.join(index, 'kbapp.workflows.tools.audit.md'), '\n'.join(L))
    print(json.dumps({'text_coverage': text_layer['coverage'], 'figure_coverage': figure_layer['coverage'], 'formula_coverage': formula_layer['coverage'], 'citation_coverage': citation_layer['coverage'], 'citation_match_rate': citation_layer['match_rate'], 'ocr_available': ocr_layer['available'], 'gaps': len(gaps)}, ensure_ascii=False, indent=2))
    print(f"\n审计报告：{os.path.join(index, 'kbapp.workflows.tools.audit.md')}")
