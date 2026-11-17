"""kbapp.workflows.tools.build_index：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from kbapp.core.config import DIR_DESC, DIR_NAME, DIR_ORDER, DIR_TITLE
BASE = str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().base)

def load_jsonl(p):
    with open(p, encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]

def est_tokens(s):
    """粗略 token 估算：CJK 约 0.7 token/字，其他约 0.25 token/字符。"""
    if not s:
        return 0
    cjk = sum((1 for ch in s if '一' <= ch <= '鿿'))
    return int(cjk * 0.7 + (len(s) - cjk) / 4)

def is_core(p):
    tf = p.get('tagfields') or {}
    return 'A' in (tf.get('priority') or []) or 'Core-A' in (tf.get('reading_tier') or [])

def one_line(p, width=90):
    txt = (p.get('tldr') or p.get('title') or '').replace('\n', ' ').strip()
    txt = txt.replace('|', '/')
    return txt[:width] + ('…' if len(txt) > width else '')

def build_compact(papers):
    """全库一行式清单，按方向分组。"""
    L = ['# 全库文献地图', '']
    L.append('> 每行一篇：`citekey | 年 | 优先级 | 一句话`。用于快速判断相关性，细节再读卡片。')
    L.append('')
    by_dir = defaultdict(list)
    for p in papers:
        by_dir[p['primary_direction']].append(p)
    for d in DIR_ORDER:
        ps = by_dir.get(d) or []
        if not ps:
            continue
        ps.sort(key=lambda p: p.get('year') or 0, reverse=True)
        L.append(f'## {d} · {DIR_TITLE[d]}（{len(ps)} 篇）')
        L.append('')
        for p in ps:
            tf = p.get('tagfields') or {}
            pr = ','.join(tf.get('priority') or []) or '-'
            L.append(f"- `{p['citekey']}` | {p.get('year') or '----'} | {pr} | {one_line(p)}")
        L.append('')
    return '\n'.join(L)

def build_directions_json(papers):
    out = []
    for d in DIR_ORDER:
        ps = [p for p in papers if p['primary_direction'] == d]
        if not ps:
            continue
        years = [p['year'] for p in ps if p.get('year')]
        out.append({'id': d, 'name': DIR_TITLE[d], 'folder': DIR_NAME[d], 'description': DIR_DESC[d], 'papers': len(ps), 'core_papers': sum((1 for p in ps if is_core(p))), 'year_range': [min(years), max(years)] if years else None, 'note': f'_知识库/20_方向/{DIR_NAME[d]}.md', 'card_folder': f'_知识库/10_文献/{DIR_NAME[d]}', 'representative': [p['citekey'] for p in sorted(ps, key=lambda x: (is_core(x), x.get('year') or 0), reverse=True)[:5]]})
    return out

def build_llms_txt(papers, dirs, index_dir, base):
    total = len(papers)
    core = sum((1 for p in papers if is_core(p)))
    years = [p['year'] for p in papers if p.get('year')]
    chunk_n = 0
    cpath = os.path.join(index_dir, 'chunks.jsonl')
    if os.path.exists(cpath):
        with open(cpath, encoding='utf-8') as fh:
            chunk_n = sum((1 for line in fh if line.strip()))
    chunk_desc = f'{chunk_n:,} 行' if chunk_n else '—'

    def count_lines(name):
        p = os.path.join(index_dir, name)
        if not os.path.exists(p):
            return 0
        with open(p, encoding='utf-8') as fh:
            return sum((1 for line in fh if line.strip()))
    fig_n = count_lines('figures.jsonl')
    eq_n = count_lines('formulas.jsonl')
    eq_latex = 0
    eq_path = os.path.join(index_dir, 'formulas.jsonl')
    if os.path.exists(eq_path):
        try:
            with open(eq_path, encoding='utf-8') as fh:
                for line in fh:
                    if line.strip() and json.loads(line).get('latex'):
                        eq_latex += 1
        except Exception:
            eq_latex = 0
    ref_n = count_lines('references.jsonl')
    edge_n = 0
    gp = os.path.join(index_dir, 'citation_graph.json')
    if os.path.exists(gp):
        try:
            edge_n = len(json.load(open(gp, encoding='utf-8')).get('edges', []))
        except Exception:
            edge_n = 0
    L = []
    L.append('# ' + os.environ.get('KB_LIBRARY_TITLE', '文献知识库'))
    L.append('')
    L.append(f'> {total} 篇文献（{min(years)}–{max(years)}），{len(dirs)} 个研究方向，{core} 篇核心文献。')
    L.append('> 主题：' + os.environ.get('KB_LIBRARY_TOPIC', '、'.join((DIR_TITLE[d] for d in DIR_ORDER if d not in ('00', '90')))) + '。')
    L.append('')
    L.append('## 怎么用这个库（按顺序，别一次全读）')
    L.append('')
    L.append('1. **先读本文件**（约 1k token），了解结构与入口。')
    L.append('2. **需要全库地图** → 读 `compact.md`（约 16k token，562 行，一行一篇）。')
    L.append('3. **锁定方向后** → 读 `../_知识库/20_方向/<方向>.md`，含子主题分布、代表文献、完整清单。')
    L.append('4. **需要单篇细节** → 读 `../_知识库/10_文献/<方向>/<citekey>.md`（400–800 token）。')
    L.append('5. **需要精确检索** → 跑 `python kb.py search "查询词"`，返回带出处的段落。')
    L.append('6. **需要结构化过滤** → 用 `papers.jsonl`（一行一篇，jq/grep 处理，不要整读）。')
    L.append('')
    L.append('## 索引文件')
    L.append('')
    L.append('| 文件 | 体量 | 内容 |')
    L.append('|---|---|---|')
    L.append('| `compact.md` | ~16k tokens | 全库一行式清单，按方向分组 |')
    L.append(f'| `papers.jsonl` | {total} 行 | 结构化元数据（作者/年份/DOI/方向/标签/卡片路径） |')
    L.append('| `directions.json` | ' + str(len(DIR_ORDER)) + ' 条 | 方向定义、篇数、代表文献 |')
    L.append(f'| `cards_manifest.json` | {total} 条 | citekey → 卡片路径 / PDF 路径 |')
    L.append('| `manifest.json` | — | 各产物体积与 token 估算 |')
    L.append(f'| `chunks.jsonl` | {chunk_desc} | 章节感知切块（向量检索的文本源） |')
    L.append(f'| `figures.jsonl` | {fig_n:,} 行 | 图/表记录：图注、页码、章节、PNG 路径 |')
    L.append(f'| `formulas.jsonl` | {eq_n:,} 行（LaTeX {eq_latex:,} 条） | 公式记录：编号、上下文、LaTeX、PNG 路径 |')
    L.append(f'| `references.jsonl` | {ref_n:,} 行 | 结构化参考文献（DOI/标题/作者/年份） |')
    L.append(f'| `citation_graph.json` | {edge_n:,} 边 | 库内引文图（度数 + PageRank） |')
    L.append('| `vec/` | — | 向量索引（本地重建，不随同步） |')
    L.append('')
    L.append('## 图表 / 公式 / 引文检索')
    L.append('')
    L.append('```bash')
    L.append('python kb.py search "示例查询词" --modality figures -k 5')
    L.append('python kb.py search "示例公式查询" --modality formulas -k 5')
    L.append('python kb.py search "example quoted phrase" --modality all --json')
    L.append('python kb.py data tool kb_graph --cited-by <citekey>')
    L.append('python kb.py data tool kb_graph --top-cited 20')
    L.append('```')
    L.append('')
    L.append('')
    L.append('## 研究方向')
    L.append('')
    for d in dirs:
        L.append(f"- [{d['id']} {d['name']}](../_知识库/20_方向/{d['folder']}.md) — {d['papers']} 篇（核心 {d['core_papers']}）· {d['description']}")
    L.append('')
    L.append('## 卡片字段')
    L.append('')
    L.append('```yaml')
    L.append('type: paper')
    L.append('citekey / zotero_key / title / authors / year / venue / doi')
    L.append('directions: [方向 wikilink]        # 可多值')
    L.append('task / robot / env / constraint / method   # 来自 Zotero 标签命名空间')
    L.append('priority: [A|B|C]                 # 阅读优先级')
    L.append('reading_tier: [Core-A|Core-B|Background]')
    L.append('review_status: [abstract_screened|fulltext_pending|body_screened|...]')
    L.append('pdf: 相对路径 | pdf_location: vault|zotero-storage')
    L.append('```')
    L.append('')
    L.append('## 卡片正文结构')
    L.append('')
    L.append('`TL;DR` / `摘要` / `问题` / `方法` / `贡献` / `局限` / `与我的课题关系` / `可借鉴点`')
    L.append('')
    L.append('## 注意')
    L.append('')
    L.append('- 标 `metadata_source: filename` 的卡片只有从文件名解析的作者/年份/标题，元数据不可靠。')
    L.append('- 标 `metadata_source: old-zotero` 的卡片元数据来自旧 Zotero 库，尚未导入当前库。')
    L.append('- 标 `pdf_location: zotero-storage` 的卡片，PDF 在 Zotero 库内，不在本 vault。')
    L.append('- 标 `pdf: null` 的条目没有 PDF（网页、软件条目等）。')
    L.append('- 方向 `90_待分类` 表示自动归类失败，需人工确认。')
    L.append('')
    L.append('## 维护')
    L.append('')
    L.append('```bash')
    L.append('python kb.py data tool export_zotero    # Zotero → papers.jsonl')
    L.append('python kb.py data tool map_pdfs         # PDF 映射与查重')
    L.append('python kb.py data tool build_cards      # 生成卡片')
    L.append('python kb.py data tool build_home       # 首页与方向页')
    L.append('python kb.py data tool build_index      # 本索引')
    L.append('python kb.py data tool chunk            # 切块')
    L.append('python kb.py data tool embed            # 向量化（需 Ollama 模型）')
    L.append('```')
    L.append('')
    return '\n'.join(L)

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=BASE)
    ap.add_argument('--index', default=str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().active_index()))
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index_dir = os.path.abspath(args.index)
    papers = load_jsonl(os.path.join(index_dir, 'papers_all.jsonl'))
    dirs = build_directions_json(papers)
    compact = build_compact(papers)
    with open(os.path.join(index_dir, 'compact.md'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(compact)
    llms = build_llms_txt(papers, dirs, index_dir, base)
    with open(os.path.join(index_dir, 'llms.txt'), 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(llms)
    with open(os.path.join(index_dir, 'directions.json'), 'w', encoding='utf-8') as fh:
        json.dump(dirs, fh, ensure_ascii=False, indent=2)
    artifacts = []
    for rel in ('llms.txt', 'compact.md', 'papers.jsonl', 'papers_all.jsonl', 'directions.json', 'cards_manifest.json', 'chunks.jsonl', 'figures.jsonl', 'formulas.jsonl', 'references.jsonl', 'citation_graph.json', 'citation_report.json', 'kbapp.workflows.tools.audit.json', 'duplicate_pdfs.json', 'orphan_pdfs.json', 'zotero_stats.json', 'cards_stats.json'):
        p = os.path.join(index_dir, rel)
        if not os.path.exists(p):
            continue
        size = os.path.getsize(p)
        if rel.endswith(('.md', '.txt', '.jsonl')):
            with open(p, encoding='utf-8') as fh:
                n = est_tokens(fh.read())
        else:
            n = None
        artifacts.append({'file': rel, 'bytes': size, 'est_tokens': n})
    manifest = {'generated': 'build_index.py', 'papers_total': len(papers), 'papers_core': sum((1 for p in papers if is_core(p))), 'directions': len(dirs), 'metadata_source': dict(Counter((p['metadata_source'] for p in papers))), 'artifacts': artifacts, 'reading_order': ['llms.txt', 'compact.md', '_知识库/20_方向/<方向>.md', '_知识库/10_文献/<方向>/<citekey>.md', 'kb_search.py']}
    with open(os.path.join(index_dir, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
