"""kbapp.workflows.tools.build_home：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from kbapp.core.config import DIR_DESC, DIR_NAME, DIR_ORDER, DIR_TITLE, env_json
BASE = str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().base)
KB = '_知识库'


def _collection_prefixes():
    """方向 -> 集合路径前缀。默认只认方向文件夹名；个人集合树用 KB_DIR_COLLECTION_PREFIX 注入。"""
    value = env_json('KB_DIR_COLLECTION_PREFIX', {d: [DIR_NAME[d]] for d in DIR_ORDER})
    if not isinstance(value, dict):
        raise ValueError('KB_DIR_COLLECTION_PREFIX 必须是 {"方向编号": ["集合路径", ...]} 形式的 JSON 对象')
    out = {}
    for key, prefixes in value.items():
        if not isinstance(prefixes, list) or any((not isinstance(prefix, str) for prefix in prefixes)):
            raise ValueError('KB_DIR_COLLECTION_PREFIX 的每个值必须是字符串数组')
        out[str(key)] = prefixes
    return out
DIR_COLLECTION_PREFIX = _collection_prefixes()

def load_jsonl(p):
    with open(p, encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]

def safe_name(s, maxlen=60):
    s = re.sub('[\\\\/:*?"<>|#^\\[\\]]', '_', s or '')
    s = re.sub('\\s+', ' ', s).strip()
    return s[:maxlen] or '未命名'

def fmt_authors(a, n=3):
    if not a:
        return '—'
    s = '、'.join(a[:n])
    return s + (' 等' if len(a) > n else '')

def is_core(p):
    tf = p.get('tagfields') or {}
    return 'A' in (tf.get('priority') or []) or 'Core-A' in (tf.get('reading_tier') or [])

def status_of(p):
    tf = p.get('tagfields') or {}
    rs = tf.get('review_status') or []
    if not rs:
        return '未标注'
    return rs[0]

def rank_for_rep(p):
    tf = p.get('tagfields') or {}
    pr = tf.get('priority') or []
    score = 0
    if 'A' in pr:
        score += 100
    if 'B' in pr:
        score += 50
    if 'Core-A' in (tf.get('reading_tier') or []):
        score += 30
    if 'Core-B' in (tf.get('reading_tier') or []):
        score += 10
    score += (p.get('year') or 0) / 100.0
    return score

def coauthor_clusters(papers, min_papers=2, min_members=3, iters=25):
    """标签传播找合作者群组。只保留发表 >= min_papers 的作者。"""
    author_papers = defaultdict(set)
    for p in papers:
        for a in p.get('authors') or []:
            author_papers[a].add(p['citekey'])
    nodes = {a for a, ps in author_papers.items() if len(ps) >= min_papers}
    if not nodes:
        return []
    edges = defaultdict(Counter)
    for p in papers:
        auths = [a for a in p.get('authors') or [] if a in nodes]
        for i in range(len(auths)):
            for j in range(i + 1, len(auths)):
                edges[auths[i]][auths[j]] += 1
                edges[auths[j]][auths[i]] += 1
    label = {n: n for n in nodes}
    for _ in range(iters):
        changed = False
        for n in sorted(nodes):
            if not edges[n]:
                continue
            cnt = Counter()
            for m, w in edges[n].items():
                cnt[label[m]] += w
            if not cnt:
                continue
            best = max(cnt.items(), key=lambda kv: (kv[1], kv[0] == label[n]))[0]
            if best != label[n]:
                label[n] = best
                changed = True
        if not changed:
            break
    groups = defaultdict(list)
    for n, value in label.items():
        groups[value].append(n)
    out = []
    for members in groups.values():
        if len(members) < min_members:
            continue
        ps = set()
        for m in members:
            ps |= author_papers[m]
        if len(ps) < min_papers:
            continue
        members.sort(key=lambda a: (-len(author_papers[a]), a))
        out.append({'members': members, 'papers': sorted(ps)})
    out.sort(key=lambda g: (-len(g['papers']), -len(g['members'])))
    return out

def page_direction(d, papers, outdir):
    ps = [p for p in papers if p['primary_direction'] == d]
    ps.sort(key=lambda p: p.get('year') or 0, reverse=True)
    core = [p for p in ps if is_core(p)]
    yrs = [p['year'] for p in ps if p.get('year')]
    sub = Counter()
    prefixes = DIR_COLLECTION_PREFIX.get(d, [])
    for p in ps:
        for c in p.get('collections') or []:
            prefix = next((pre for pre in prefixes if pre and (c == pre or c.startswith(pre + '/'))), None)
            if prefix is None:
                continue
            rest = c[len(prefix):].strip('/')
            if not rest:
                sub[c] += 1
                continue
            head = rest.split('/')[0]
            if head.startswith('90_'):
                continue
            sub[head] += 1
    L = []
    L.append('---')
    L.append('type: "direction"')
    L.append(f'id: "{d}"')
    L.append(f'aliases: {json.dumps([DIR_TITLE[d], DIR_NAME[d]], ensure_ascii=False)}')
    L.append(f'papers: {len(ps)}')
    L.append(f'core_papers: {len(core)}')
    L.append('---')
    L.append('')
    L.append(f'# {d} · {DIR_TITLE[d]}')
    L.append('')
    L.append(f'> {DIR_DESC[d]}')
    L.append('')
    L.append(f"**{len(ps)} 篇**（核心 {len(core)}）· 年份跨度 {(min(yrs) if yrs else '—')}–{(max(yrs) if yrs else '—')}")
    L.append('')
    if sub:
        L.append('## 子主题分布')
        L.append('')
        L.append('| 子主题 | 篇数 |')
        L.append('|---|---:|')
        for k, v in sub.most_common(12):
            L.append(f'| {k} | {v} |')
        L.append('')
    rep = sorted(ps, key=rank_for_rep, reverse=True)[:6]
    if rep:
        L.append('## 代表文献')
        L.append('')
        L.append('| 文献 | 年 | 作者 | 说明 |')
        L.append('|---|---:|---|---|')
        for p in rep:
            note = p.get('tldr') or ''
            note = note.replace('|', '/')[:70]
            L.append(f"| [[{p['citekey']}]] | {p.get('year') or '—'} | {fmt_authors(p['authors'], 2)} | {note} |")
        L.append('')
    L.append('## 全部文献')
    L.append('')
    L.append('```dataview')
    L.append('TABLE WITHOUT ID')
    L.append('  file.link AS "文献",')
    L.append('  year AS "年",')
    L.append('  join(authors, "、") AS "作者",')
    L.append('  choice(priority, join(priority, ","), "") AS "优先级",')
    L.append('  choice(reading_tier, join(reading_tier, ","), "") AS "层级",')
    L.append('  choice(review_status, join(review_status, ","), "") AS "状态"')
    L.append(f'FROM "10_文献/{DIR_NAME[d]}"')
    L.append('WHERE type = "paper"')
    L.append('SORT year DESC')
    L.append('```')
    L.append('')
    L.append('## 跨方向关联')
    L.append('')
    L.append('```dataview')
    L.append('TABLE WITHOUT ID file.link AS "文献", join(directions, " ") AS "方向", year AS "年"')
    L.append('FROM "10_文献"')
    L.append('WHERE type = "paper" AND contains(directions, this.file.link)')
    L.append('SORT year DESC')
    L.append('LIMIT 40')
    L.append('```')
    L.append('')
    path = os.path.join(outdir, DIR_NAME[d] + '.md')
    from kbapp.library.generated import write_generated
    write_generated(path, '\n'.join(L))

def page_author(name, papers, outdir):
    ps = sorted(papers, key=lambda p: p.get('year') or 0, reverse=True)
    L = []
    L.append('---')
    L.append('type: "scholar"')
    L.append(f'aliases: {json.dumps([name], ensure_ascii=False)}')
    L.append(f'papers: {len(ps)}')
    L.append('---')
    L.append('')
    L.append(f'# {name}')
    L.append('')
    L.append(f'> 库内 {len(ps)} 篇')
    L.append('')
    L.append('## 论文')
    L.append('')
    L.append('| 文献 | 年 | 期刊 | 方向 |')
    L.append('|---|---:|---|---|')
    for p in ps:
        d = DIR_TITLE.get(p['primary_direction'], '')
        L.append(f"| [[{p['citekey']}]] | {p.get('year') or '—'} | {(p.get('venue') or '—')[:28]} | {d} |")
    L.append('')
    L.append('## 合作者')
    L.append('')
    L.append('```dataview')
    L.append('TABLE WITHOUT ID a AS "合作者", length(rows) AS "共同论文"')
    L.append('FROM "10_文献"')
    L.append('WHERE type = "paper" AND contains(authors, this.aliases[0])')
    L.append('FLATTEN authors AS a')
    L.append('WHERE a != this.aliases[0]')
    L.append('GROUP BY a')
    L.append('SORT length(rows) DESC')
    L.append('LIMIT 20')
    L.append('```')
    L.append('')
    path = os.path.join(outdir, safe_name('作者-' + name) + '.md')
    from kbapp.library.generated import write_generated
    write_generated(path, '\n'.join(L))

def page_team(idx, team, papers_by_key, outdir):
    members = team['members']
    ps = [papers_by_key[k] for k in team['papers'] if k in papers_by_key]
    ps.sort(key=lambda p: p.get('year') or 0, reverse=True)
    title = f'{members[0]} 等 {len(members)} 人'
    L = []
    L.append('---')
    L.append('type: "team"')
    L.append(f'members: {json.dumps(members, ensure_ascii=False)}')
    L.append(f'papers: {len(ps)}')
    L.append('institution: ""')
    L.append('---')
    L.append('')
    L.append(f'# 团队 · {title}')
    L.append('')
    L.append('> 由共同署名自动聚类得到，**机构信息尚未补全**（需按通讯作者机构人工确认或接入 OpenAlex）')
    L.append('')
    L.append('## 成员')
    L.append('')
    L.append('、'.join((f'[[{m}]]' for m in members)))
    L.append('')
    L.append('## 共同论文')
    L.append('')
    L.append('| 文献 | 年 | 期刊 | 作者 |')
    L.append('|---|---:|---|---|')
    for p in ps:
        L.append(f"| [[{p['citekey']}]] | {p.get('year') or '—'} | {(p.get('venue') or '—')[:26]} | {fmt_authors(p['authors'], 4)} |")
    L.append('')
    path = os.path.join(outdir, safe_name(f'团队-{title}') + '.md')
    from kbapp.library.generated import write_generated
    write_generated(path, '\n'.join(L))

def page_home(papers, teams, outdir, dup_count, orphan_count):
    total = len(papers)
    with_abs = sum((1 for p in papers if p.get('abstract')))
    yrs = [p['year'] for p in papers if p.get('year')]
    L = []
    L.append('---')
    L.append('type: "home"')
    L.append(f'papers: {total}')
    L.append('---')
    L.append('')
    L.append('# 文献知识库总览')
    L.append('')
    L.append(f'> **{total}** 篇 · **{len(teams)}** 个合作团队 · 年份跨度 **{min(yrs)}–{max(yrs)}** · 有摘要 {with_abs}')
    L.append('')
    L.append('## 研究方向大类')
    L.append('')
    L.append('| 方向 | 篇数 | 核心 | 未读 | 最新年 |')
    L.append('|---|---:|---:|---:|---:|')
    for d in DIR_ORDER:
        ps = [p for p in papers if p['primary_direction'] == d]
        if not ps:
            continue
        unread = sum((1 for p in ps if 'fulltext_pending' in (p.get('tagfields') or {}).get('review_status', [])))
        yy = [p['year'] for p in ps if p.get('year')]
        L.append(f"| [[{DIR_NAME[d]}\\|{DIR_TITLE[d]}]] | {len(ps)} | {sum((1 for p in ps if is_core(p)))} | {unread} | {(max(yy) if yy else '—')} |")
    L.append('')
    L.append('## 团队 / 合作群组')
    L.append('')
    L.append('| 团队 | 成员 | 论文 | 核心成员 |')
    L.append('|---|---:|---:|---|')
    for t in teams[:20]:
        L.append(f"| [[团队-{safe_name(t['members'][0] + ' 等 ' + str(len(t['members'])) + ' 人')}\\|{t['members'][0]} 等 {len(t['members'])} 人]] | {len(t['members'])} | {len(t['papers'])} | {'、'.join(t['members'][:3])} |")
    L.append('')
    L.append('> 团队由共同署名聚类得到，机构信息待补全。完整列表见 [[02_团队与学者]]。')
    L.append('')
    L.append('## 最近两年')
    L.append('')
    recent = sorted([p for p in papers if (p.get('year') or 0) >= 2025], key=lambda p: p.get('year') or 0, reverse=True)[:15]
    L.append('| 文献 | 年 | 方向 |')
    L.append('|---|---:|---|')
    for p in recent:
        L.append(f"| [[{p['citekey']}]] | {p.get('year')} | {DIR_TITLE.get(p['primary_direction'], '')} |")
    L.append('')
    L.append('## 快速入口')
    L.append('')
    L.append(f'- [[01_研究方向总览]] — {len(DIR_ORDER)} 个方向的论文清单与代表文献')
    L.append('- [[02_团队与学者]] — 合作群组与高频作者')
    L.append('- [[03_阅读进度]] — 按状态、优先级、层级的待读队列')
    L.append('- `_index/llms.txt` — 给 AI 的检索入口说明')
    L.append('')
    L.append('## 数据质量')
    L.append('')
    L.append(f'- 内容重复被合并的 PDF：**{dup_count}** 个（见 `_index/duplicate_pdfs.json`）')
    L.append(f'- 未进入 Zotero 的孤儿 PDF：**{orphan_count}** 个（见 `_index/orphan_pdfs.json`）')
    L.append('')
    from kbapp.library.generated import write_generated
    write_generated(os.path.join(outdir, '00_总览.md'), '\n'.join(L))

def page_direction_index(papers, outdir):
    L = []
    L.append('# 研究方向总览')
    L.append('')
    L.append(f'> 按 {len(DIR_ORDER)} 个研究方向组织。每个方向页含子主题分布、代表文献、全部清单。')
    L.append('')
    for d in DIR_ORDER:
        ps = [p for p in papers if p['primary_direction'] == d]
        if not ps:
            continue
        L.append(f'## [[{DIR_NAME[d]}|{d} · {DIR_TITLE[d]}]]')
        L.append('')
        L.append(f'{DIR_DESC[d]} — 共 **{len(ps)}** 篇')
        L.append('')
        rep = sorted(ps, key=rank_for_rep, reverse=True)[:3]
        for p in rep:
            L.append(f"- [[{p['citekey']}]] — {p.get('tldr') or p.get('title', '')}")
        L.append('')
    from kbapp.library.generated import write_generated
    write_generated(os.path.join(outdir, '01_研究方向总览.md'), '\n'.join(L))

def page_teams_scholars(papers, teams, author_papers, outdir):
    L = []
    L.append('# 团队与学者')
    L.append('')
    L.append('## 合作团队（自动聚类）')
    L.append('')
    L.append('> 依据共同署名关系做标签传播聚类，仅保留 ≥3 人且共同发表 ≥2 篇的群组。机构需人工补全。')
    L.append('')
    for t in teams:
        L.append(f"- [[团队-{safe_name(t['members'][0] + ' 等 ' + str(len(t['members'])) + ' 人')}|{t['members'][0]} 等 {len(t['members'])} 人]] — {len(t['papers'])} 篇")
    L.append('')
    L.append('## 高频作者（Top 60）')
    L.append('')
    L.append('| 作者 | 篇数 |')
    L.append('|---|---:|')
    for name, ps in author_papers[:60]:
        L.append(f'| [[作者-{safe_name(name)}|{name}]] | {len(ps)} |')
    L.append('')
    from kbapp.library.generated import write_generated
    write_generated(os.path.join(outdir, '02_团队与学者.md'), '\n'.join(L))

def page_progress(papers, outdir):
    L = []
    L.append('# 阅读进度')
    L.append('')
    st = Counter((status_of(p) for p in papers))
    L.append('## 状态分布')
    L.append('')
    L.append('| 状态 | 篇数 |')
    L.append('|---|---:|')
    for k, v in st.most_common():
        L.append(f'| {k} | {v} |')
    L.append('')
    L.append('## 优先级分布')
    L.append('')
    pr = Counter((x for p in papers for x in (p.get('tagfields') or {}).get('priority', [])))
    L.append('| 优先级 | 篇数 |')
    L.append('|---|---:|')
    for k in sorted(pr):
        L.append(f'| {k} | {pr[k]} |')
    L.append('')
    L.append('## 待读：Priority A 且尚未精读')
    L.append('')
    L.append('| 文献 | 年 | 方向 | 状态 |')
    L.append('|---|---:|---|---|')
    todo = [p for p in papers if 'A' in (p.get('tagfields') or {}).get('priority', []) and 'fulltext_pending' in (p.get('tagfields') or {}).get('review_status', [])]
    todo.sort(key=lambda p: p.get('year') or 0, reverse=True)
    for p in todo[:40]:
        L.append(f"| [[{p['citekey']}]] | {p.get('year') or '—'} | {DIR_TITLE.get(p['primary_direction'], '')} | {status_of(p)} |")
    L.append('')
    from kbapp.library.generated import write_generated
    write_generated(os.path.join(outdir, '03_阅读进度.md'), '\n'.join(L))

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
    index = os.path.abspath(args.index)
    papers = load_jsonl(os.path.join(index, 'papers_all.jsonl'))
    papers_by_key = {p['citekey']: p for p in papers}
    dir_dir = os.path.join(base, KB, '20_方向')
    home_dir = os.path.join(base, KB, '00_首页')
    team_dir = os.path.join(base, KB, '30_团队')
    auth_dir = os.path.join(base, KB, '40_学者')
    for d in (dir_dir, home_dir, team_dir, auth_dir):
        os.makedirs(d, exist_ok=True)
    for d in DIR_ORDER:
        page_direction(d, papers, dir_dir)
    ap_map = defaultdict(list)
    for p in papers:
        for a in p.get('authors') or []:
            ap_map[a].append(p)
    author_papers = sorted(ap_map.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for name, ps in author_papers[:60]:
        page_author(name, ps, auth_dir)
    teams = coauthor_clusters(papers)
    for i, t in enumerate(teams, 1):
        page_team(i, t, papers_by_key, team_dir)
    dup = json.load(open(os.path.join(index, 'duplicate_pdfs.json'), encoding='utf-8'))
    orphan = [p for p in papers if p.get('metadata_source') != 'zotero']
    page_home(papers, teams, home_dir, len(dup), len(orphan))
    page_direction_index(papers, home_dir)
    page_teams_scholars(papers, teams, author_papers, home_dir)
    page_progress(papers, home_dir)
    print(json.dumps({'directions': len(DIR_ORDER), 'scholar_pages': min(60, len(author_papers)), 'team_pages': len(teams), 'home_pages': 4, 'top_authors': [{'name': n, 'papers': len(ps)} for n, ps in author_papers[:10]]}, ensure_ascii=False, indent=2))
