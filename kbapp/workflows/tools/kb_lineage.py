"""kbapp.workflows.tools.kb_lineage：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
import kbapp.core.config as C
from kbapp.core.io import read_json, write_text

def load(base):
    cdir = C.citation_dir(base)
    graph = read_json(os.path.join(cdir, 'graph.json'), {}) or {}
    nodes = {n['id']: n for n in graph.get('nodes', [])}
    edges = graph.get('edges', [])
    backbone = read_json(os.path.join(cdir, 'backbone.json'), {}) or {}
    gaps = read_json(os.path.join(cdir, 'gaps.json'), {}) or {}
    stats = read_json(os.path.join(cdir, 'evolution_stats.json'), {}) or {}
    lineage = read_json(os.path.join(cdir, 'lineage.json'), {}) or {}
    return (nodes, edges, backbone, gaps, stats, graph.get('directions', {}), lineage)

def label(n, width=76):
    t = (n.get('title') or '')[:width]
    return f"{n.get('year') or '----'}  {t}"

def cmd_backbone(nodes, backbone, dirs, arg, as_json):
    dd = backbone.get('directions', {})
    keys = [arg] if arg else sorted(dd)
    out = {}
    for d in keys:
        v = dd.get(d)
        if not v:
            print(f'方向 {d} 没有主干链')
            continue
        out[d] = v
        if as_json:
            continue
        print(f"\n{'=' * 72}")
        print(f"{d}  {v['name']}   {len(v['chains'])} 条主干")
        print('=' * 72)
        for i, c in enumerate(v['chains'], 1):
            print(f"\n  链 {i}   score={c['score']}   {c['years'][0]} → {c['years'][-1]}   {len(c['nodes'])} 步")
            for k, nid in enumerate(c['nodes']):
                n = nodes.get(nid, {})
                kind = '库内' if n.get('kind') == 'library' else '库外'
                sup = c.get('support', [0] * len(c['nodes']))[k]
                print(f'    {k + 1:2}. [{kind}] {label(n)}' + (f'   ← 本方向 {sup} 篇引用' if sup else ''))
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))

def cmd_chain(nodes, edges, backbone, ck, lineage):
    ch = lineage.get(ck)
    if ch:
        print(f'\n{ck} 的引用链（{len(ch)} 步，从最早期前驱）\n')
        for k, x in enumerate(ch):
            arrow = '  ' if k == 0 else '→ '
            tag = '  ← 本文' if x.get('id') == ck else ''
            print(f"  {arrow}{x.get('year') or '----'}  {(x.get('title') or '')[:76]}{tag}")
        return
    for d, v in backbone.get('directions', {}).items():
        for c in v['chains']:
            if ck in c['nodes']:
                print(f"\n{ck} 所在主干链（方向 {d} {v['name']}）\n")
                for k, nid in enumerate(c['nodes']):
                    n = nodes.get(nid, {})
                    arrow = '  ' if k == 0 else '→ '
                    print(f'  {arrow}{label(n)}')
                return
    print(f'{ck} 没有可用的祖先链（可能是中文文献或无 OpenAlex 记录），直接上游：')
    for e in edges:
        if e['s'] == ck:
            print('   ', label(nodes.get(e['d'], {})))

def cmd_gaps(nodes, gaps, ck):
    g = gaps.get(ck)
    if not g:
        print(f'{ck} 没有检测到明显缺口（或该篇无 OpenAlex 记录）')
        return
    print(f'\n{ck} 可能漏引（同方向多篇引用了它，但本篇没有）：\n')
    for i, x in enumerate(g, 1):
        n = nodes.get(x['id'], {})
        kind = '库内' if n.get('kind') == 'library' else '库外'
        print(f"  {i}. [{kind}] {label(n, 84)}   ← 同方向 {x['support']} 篇引用")

def cmd_stats(stats):
    print(json.dumps(stats, ensure_ascii=False, indent=2))

def cmd_report(base, nodes, edges, backbone, gaps, stats, dirs):
    out_dir = os.path.join(base, '_知识库', '50_谱系')
    os.makedirs(out_dir, exist_ok=True)
    dd = backbone.get('directions', {})
    forks_by_node = defaultdict(list)
    for f in backbone.get('forks', []):
        forks_by_node[f['id']].append(f)
    dir_of = {}
    for nid, n in nodes.items():
        if n.get('kind') == 'library':
            dir_of[nid] = n.get('primary_direction') or ''
    written = []
    overview = ['# 研究演化谱系 · 总览', '', f"> {stats.get('nodes_shown', 0)} 节点 / {stats.get('edges_shown', 0)} 边 · 库内 {stats.get('library_resolved', 0)} 篇有 OpenAlex 记录", '', '打开交互图：`_index/citation/evolution.html`', '', '## 方向索引', '']
    for d in sorted(dd):
        v = dd[d]
        name = v['name']
        lines = [f'# {d} {name} · 演化链条', '', f"> 共 {len(v['chains'])} 条主干链，由「同方向文献实际引用 + 引用所在章节」加权提取。", '']
        for i, c in enumerate(v['chains'], 1):
            lines.append(f"## 主线 {i}：{c['years'][0]} → {c['years'][-1]}")
            lines.append('')
            for k, nid in enumerate(c['nodes']):
                n = nodes.get(nid, {})
                sup = c.get('support', [0] * len(c['nodes']))[k]
                if n.get('kind') == 'library':
                    title = f'[[{nid}]]'
                else:
                    title = f"{n.get('title', '')}"
                tail = f'（本方向 {sup} 篇引用）' if sup else ''
                lines.append(f"{k + 1}. **{n.get('year') or '—'}** {title} {tail}".rstrip())
            lines.append('')
            forks = [f for f in backbone.get('forks', []) if f['id'] in c['nodes']]
            if forks:
                lines.append('**分叉点**：' + '；'.join((f"{f['year']} {f['title'][:50]}" for f in forks[:3])))
                lines.append('')
            lines.append('')
        agg = Counter()
        meta = {}
        for ck, miss in gaps.items():
            if dir_of.get(ck) != d:
                continue
            for m in miss:
                agg[m['id']] += 1
                meta[m['id']] = m
        if agg:
            lines.append('## 常见漏引（多篇同方向文献都引了，但部分文献没引）')
            lines.append('')
            for mid, cnt in agg.most_common(10):
                m = meta[mid]
                n = nodes.get(mid, {})
                nm = f'[[{mid}]]' if n.get('kind') == 'library' else n.get('title', '')
                lines.append(f"- **{m.get('year') or '—'}** {nm} （{cnt} 篇未引）")
            lines.append('')
        path = os.path.join(out_dir, f'{d}_{name}.md')
        write_text(path, '\n'.join(lines))
        written.append(path)
        overview.append(f"- [[{d}_{name}]] — {len(v['chains'])} 条主干")
    overview += ['', '## 统计', '', f"- 库内文献：{stats.get('by_kind', {}).get('library', 0)}", f"- 库外祖先：{stats.get('by_kind', {}).get('ancestor', 0)}", f"- 有上游引用的库内文献：{stats.get('library_with_upstream', 0)}", f"- 完全孤立：{stats.get('library_isolated', 0)}", f"- 分叉点：{stats.get('forks', 0)}", f"- 有漏引提示的文献：{stats.get('papers_with_gaps', 0)}"]
    write_text(os.path.join(out_dir, '00_总览.md'), '\n'.join(overview))
    print(f'已生成 {len(written) + 1} 个文件到 {out_dir}', flush=True)
    for p in written:
        print('  ', os.path.basename(p), flush=True)

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--backbone', nargs='?', const='', default=None)
    ap.add_argument('--chain', default='')
    ap.add_argument('--gaps', default='')
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    nodes, edges, backbone, gaps, stats, dirs, lineage = load(base)
    if not nodes:
        sys.exit('图谱不存在，先运行 build_evolution_graph.py')
    if args.stats:
        cmd_stats(stats)
    if args.backbone is not None:
        cmd_backbone(nodes, backbone, dirs, args.backbone, args.json)
    if args.chain:
        cmd_chain(nodes, edges, backbone, args.chain, lineage)
    if args.gaps:
        cmd_gaps(nodes, gaps, args.gaps)
    if args.report:
        cmd_report(base, nodes, edges, backbone, gaps, stats, dirs)
    if not any([args.stats, args.backbone is not None, args.chain, args.gaps, args.report]):
        ap.print_help()
