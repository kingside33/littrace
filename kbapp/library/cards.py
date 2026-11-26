"""kbapp.library.cards：由职责边界组织的知识库能力。"""
import json
import os
import re
import unicodedata
from collections import defaultdict
from kbapp.core.config import DIRECTIONS as _DIRECTION_ROWS, DIR_NAME, DIR_ORDER, env_json
from kbapp.core.io import digest
BASE = str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().base)
PERSIST_START = '<!-- PERSIST:START -->'
PERSIST_END = '<!-- PERSIST:END -->'
VENUE_TYPE_LABEL = {'journalArticle': '期刊论文', 'conferencePaper': '会议论文', 'thesis': '学位论文', 'preprint': '预印本', 'book': '专著', 'bookSection': '书章', 'report': '报告', 'webpage': '网页', 'computerProgram': '软件'}
DIRECTIONS = [(row[0], row[1], row[2]) for row in _DIRECTION_ROWS]
DIR_TITLE = {row[0]: row[2] for row in DIRECTIONS}


def _env_rules(name, default):
    """集合/磁盘归类规则可由环境变量覆盖，默认不携带任何个人目录结构。"""
    value = env_json(name, default)
    if not isinstance(value, list) or any((not isinstance(row, (list, tuple)) or len(row) != 2 or not isinstance(row[1], (list, tuple)) for row in value)):
        raise ValueError(f'{name} 必须是 [[集合路径, [方向编号]], ...] 形式的 JSON 数组')
    return [(str(prefix), [str(d) for d in dirs]) for prefix, dirs in value]
_DEFAULT_FOLDER_RULES = [(DIR_NAME[d], [d]) for d in DIR_NAME if d != '90']
COLLECTION_RULES = _env_rules('KB_COLLECTION_RULES', _DEFAULT_FOLDER_RULES)
DISK_RULES = _env_rules('KB_DISK_RULES', _DEFAULT_FOLDER_RULES)
# 内置题名→方向词表按机器人／路径规划词汇编写，仅作为 KB_TITLE_RULES 未设置时的默认值；
# 非原领域的库用 KB_TITLE_RULES 覆盖（与 KB_DIRECTIONS 配套设置）。
_DEFAULT_TITLE_RULES = [('覆盖|coverage|CPP|inspection|巡检|检测', ['04']), ('构型|变构|articulat|reconfigur|snake|蛇形', ['01', '05']), ('ROI|入口|交接|docking|handover', ['03']), ('路径规划|path planning|RRT|A\\*|Dijkstra|轨迹|trajectory', ['02']), ('优化|optimiz|metaheuristic|群智能|蚁群|粒子群|遗传', ['06']), ('传感器|sensor|感知|electric sense|SLAM|声呐|sonar|光场', ['08']), ('浊流|turbidity|沉积|sediment|海洋学|oceanograph', ['09'])]


def validate_title_rules(value, name='KB_TITLE_RULES', check_dirs=True):
    """校验 [[正则, [方向编号]], ...] 并归一化；显式空列表合法（关闭题名推断）。

    check_dirs=False 只验结构、形状与正则：导入阶段先用它加载内置词表，编号对生效
    方向表的检查交给 ensure_title_rules() 在题名推断使用点补上。
    """
    if not isinstance(value, list):
        raise ValueError(f'{name} 必须是 [[正则, [方向编号]], ...] 形式的 JSON 数组')
    rules = []
    for index, row in enumerate(value, 1):
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError(f'{name} 第 {index} 条 {json.dumps(row, ensure_ascii=False)} 必须是 [正则, [方向编号]] 形式的数组')
        pattern, dirs = row
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f'{name} 第 {index} 条 {json.dumps(row, ensure_ascii=False)} 的正则必须是非空字符串')
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f'{name} 第 {index} 条 ({json.dumps(pattern, ensure_ascii=False)}, {json.dumps(dirs, ensure_ascii=False)}) 的正则无效: {exc}') from exc
        if not isinstance(dirs, (list, tuple)) or not dirs:
            raise ValueError(f'{name} 第 {index} 条 ({json.dumps(pattern, ensure_ascii=False)}) 的方向编号必须是非空数组')
        ids = [str(d) for d in dirs]
        if check_dirs:
            for d in ids:
                if d not in DIR_ORDER:
                    raise ValueError(f'{name} 第 {index} 条 ({json.dumps(pattern, ensure_ascii=False)}, {json.dumps(dirs, ensure_ascii=False)}) 的方向编号 {json.dumps(d, ensure_ascii=False)} 不在方向表中')
        rules.append((pattern, ids))
    return rules


def _unknown_direction_error(rules, name='KB_TITLE_RULES'):
    """第一条与生效方向表不符的规则的报错文本；全部合法返回空串。"""
    for index, (pattern, dirs) in enumerate(rules, 1):
        for d in dirs:
            if d not in DIR_ORDER:
                return f'{name} 第 {index} 条 ({json.dumps(pattern, ensure_ascii=False)}, {json.dumps(list(dirs), ensure_ascii=False)}) 的方向编号 {json.dumps(d, ensure_ascii=False)} 不在方向表中'
    return ''


def load_title_rules():
    """题名→方向规则三态：未设置 KB_TITLE_RULES 用内置词表，显式 [] 关闭题名推断，其余校验后生效。

    导入阶段只验结构、形状与正则：编号对生效方向表的检查推迟到 ensure_title_rules()，
    这样方向表已改、题名规则未配（或配了表外编号）的库仍能用不涉及题名推断的入口
    （issue #100 / PR #106 评审）。
    """
    value = env_json('KB_TITLE_RULES', None)
    if value is None:
        return validate_title_rules(_DEFAULT_TITLE_RULES, check_dirs=False)
    return validate_title_rules(value, check_dirs=False)


def ensure_title_rules():
    """题名推断使用点确认规则与生效方向表配套；不配套时报可操作错误。"""
    error = _unknown_direction_error(KEYWORD_RULES)
    if not error:
        return
    if os.environ.get('KB_TITLE_RULES') is None:
        raise ValueError(
            f'内置题名词表(未设置 KB_TITLE_RULES 时的默认值)不在生效方向表内: {error}; '
            '处理方式: 设置 KB_TITLE_RULES 显式给出题名规则, 或让 KB_DIRECTIONS 保留内置词表引用的方向编号'
        )
    raise ValueError(f'{error}; 处理方式: 显式设置 KB_TITLE_RULES=[] 关闭题名推断, 或把该条规则改用生效方向表里的编号')


KEYWORD_RULES = load_title_rules()


def title_rules_digest():
    """生效规则摘要：进分类指纹与图谱缓存键，规则变化后内存与磁盘缓存自动失效。"""
    return digest(json.dumps(KEYWORD_RULES, ensure_ascii=False))
TAG_FIELDS = {'task': 'task', 'robot': 'robot', 'env': 'env', 'constraint': 'constraint', 'method': 'method', 'priority': 'priority', 'reading_tier': 'reading_tier', 'review_status': 'review_status', 'relation': 'relation', 'era': 'era', 'pdf_status': 'pdf_status', 'domain_transfer': 'domain_transfer', 'library_tier': 'library_tier'}

def norm_text(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFKC', s)
    s = os.path.splitext(s)[0]
    s = re.sub('[^\\w\\u4e00-\\u9fff]+', '', s, flags=re.UNICODE)
    return s.lower()

def base_stem(name):
    """去掉 _1 / _2 / ' 3' / (1) 这类副本后缀，用于找同名有效副本。"""
    s = os.path.splitext(name or '')[0]
    s = re.sub('[\\s_\\-]*[\\(（]\\d{1,2}[\\)）]\\s*$', '', s)
    s = re.sub('[\\s_\\-]+\\d{1,2}\\s*$', '', s)
    return norm_text(s)

def slug(s, maxlen=24):
    s = unicodedata.normalize('NFKC', s or '')
    s = re.sub('[^\\w\\u4e00-\\u9fff]+', '', s)
    return s[:maxlen]

def parse_filename(name):
    """解析 Zotero 风格文件名：作者 - 年份 - 标题.pdf"""
    stem = os.path.splitext(os.path.basename(name))[0]
    out = {'authors': [], 'year': None, 'title': stem, 'parsed': False}
    m = re.match('^([0-9a-f]{16,24})_(.+)$', stem)
    if m:
        out['title'] = m.group(2).replace('_', ' ')
        return out
    parts = [p.strip() for p in stem.split(' - ')]
    if len(parts) >= 3:
        head, year_s, title = (parts[0], parts[1], ' - '.join(parts[2:]))
        if re.fullmatch('(1[89]\\d{2}|20\\d{2})', year_s):
            out['year'] = int(year_s)
            out['title'] = title
            head = re.sub('\\s*等$', '', head).strip()
            if head and head not in ('等',):
                out['authors'] = [head]
            out['parsed'] = True
            return out
    if len(parts) == 2:
        head, year_s = (parts[0], parts[1])
        if re.fullmatch('(1[89]\\d{2}|20\\d{2})', year_s):
            out['year'] = int(year_s)
            out['title'] = head
            out['parsed'] = True
            return out
    m = re.match('^(1[89]\\d{2}|20\\d{2})\\s*-\\s*(.+)$', stem)
    if m:
        out['year'] = int(m.group(1))
        out['title'] = m.group(2)
        out['parsed'] = True
    return out

def directions_from_collections(colls):
    dirs = []
    for c in colls:
        p = c['path']
        for prefix, ds in COLLECTION_RULES:
            if p == prefix or p.startswith(prefix + '/'):
                for d in ds:
                    if d not in dirs:
                        dirs.append(d)
                break
    return dirs

def directions_from_disk(rel):
    for prefix, ds in DISK_RULES:
        if rel.startswith(prefix):
            return list(ds)
    return []

def directions_from_keywords(text):
    if not KEYWORD_RULES:
        return []
    ensure_title_rules()
    for pat, ds in KEYWORD_RULES:
        if re.search(pat, text, re.IGNORECASE):
            return list(ds)
    return []

def pick_directions(paper, rel):
    """合集 → 题名关键词 → 磁盘 → 兜底 90；优先次序与语义保持不变。"""
    return pick_directions_with_basis(paper, rel)[0]


def pick_directions_with_basis(paper, rel):
    """pick_directions 的带来源版本：入库时把 direction_basis 一并持久化，供规则
    变化后按摘要重算题名推断出的方向（issue #100 要求 3 的真实入库路径）。"""
    dirs = directions_from_collections(paper.get('collections') or [])
    basis = 'collections' if dirs else ''
    if not dirs:
        dirs = directions_from_keywords(paper.get('title') or '')
        basis = 'title_keywords' if dirs else ''
    if not dirs:
        dirs = directions_from_disk(rel or '')
        basis = 'disk' if dirs else ''
    if not dirs:
        dirs, basis = ['90'], 'fallback'
    return dirs, basis

def tag_fields(tags):
    out = defaultdict(list)
    other = []
    for t in tags:
        if ':' in t:
            k, v = t.split(':', 1)
            if k in TAG_FIELDS:
                out[TAG_FIELDS[k]].append(v)
            else:
                other.append(t)
        else:
            other.append(t)
    return (out, other)

def yaml_val(v):
    return json.dumps(v, ensure_ascii=False)

def make_citekey(authors, year, title, used):
    first = ''
    if authors:
        a = authors[0]
        first = re.split('[,，\\s]', a.strip())[0]
    first = slug(first, 12) or 'anon'
    y = str(year) if year else 'nd'
    tw = ''
    for w in re.split('[\\s\\-_]+', title or ''):
        w2 = slug(w, 12)
        if not w2 or not w2.isascii():
            continue
        if w2.lower() not in ('a', 'an', 'the', 'of', 'on', 'for', 'and', 'in'):
            tw = w2
            break
    base = f'{first}{y}{tw}' if tw else f'{first}{y}'
    base = base[:40]
    key = base
    n = 1
    while key.lower() in used:
        n += 1
        key = f'{base}-{n}'
    used.add(key.lower())
    return key

def merge_duplicate_records(records):
    """按 DOI（优先）或归一化标题合并同一篇论文的多张卡片。

    保留优先级：zotero+有PDF > old-zotero+有PDF > zotero无PDF > filename
    """

    def score(r):
        s = 0
        if r['metadata_source'] == 'zotero':
            s += 100
        elif r['metadata_source'] == 'old-zotero':
            s += 50
        if r.get('pdf'):
            s += 20
        if r.get('abstract'):
            s += 5
        if r.get('doi'):
            s += 3
        if r.get('tagfields'):
            s += 2
        return s
    groups = {}
    order = []
    for i, r in enumerate(records):
        if r.get('doi'):
            key = 'doi:' + str(r['doi']).lower().strip()
        elif r.get('title'):
            key = 't:' + norm_text(r['title'])[:80]
        else:
            key = f'i:{i}'
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)
    out, dups = ([], [])
    for key in order:
        g = groups[key]
        if len(g) == 1:
            out.append(g[0])
            continue
        g.sort(key=score, reverse=True)
        win = g[0]
        for lose in g[1:]:
            for f in ('abstract', 'venue', 'doi', 'year'):
                if not win.get(f) and lose.get(f):
                    win[f] = lose[f]
            if not win.get('pdf') and lose.get('pdf'):
                win['pdf'] = lose['pdf']
                win['pdf_location'] = lose.get('pdf_location')
                win['pdf_exists'] = lose.get('pdf_exists')
            for d in lose.get('directions') or []:
                if d not in win['directions']:
                    win['directions'].append(d)
            for k, vs in (lose.get('tagfields') or {}).items():
                win['tagfields'].setdefault(k, [])
                for v in vs:
                    if v not in win['tagfields'][k]:
                        win['tagfields'][k].append(v)
            dups.append({'merged_into': win['title'], 'dropped_citekey_hint': lose.get('citekey'), 'dropped_source': lose['metadata_source'], 'dropped_title': lose.get('title'), 'dropped_pdf': lose.get('pdf'), 'doi': win.get('doi')})
        out.append(win)
    return (out, dups)

def yaml_scalar(v):
    """None -> null，字符串 -> 带引号，列表 -> JSON 数组。"""
    return 'null' if v is None or v == '' else json.dumps(v, ensure_ascii=False)

def load_template(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()

def render(template, ctx):
    """把 {{key}} 替换成 ctx[key]；notes 最后替换，避免用户内容里的 {{ }} 触发误报。"""
    out = template
    for k, v in ctx.items():
        if k == 'notes':
            continue
        out = out.replace('{{' + k + '}}', v)
    left = [m for m in re.findall('\\{\\{[^}]+\\}\\}', out) if m != '{{notes}}']
    if left:
        raise ValueError('模板存在未替换的占位符: ' + ', '.join(sorted(set(left))))
    return out.replace('{{notes}}', ctx['notes'])

def load_figures(index_dir, citekey):
    p = os.path.join(index_dir, 'figures', citekey + '.json')
    if not os.path.exists(p):
        return []
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return []

def load_citation_maps(index_dir):

    def _load(name):
        p = os.path.join(index_dir, name)
        if not os.path.exists(p):
            return {}
        try:
            return json.load(open(p, encoding='utf-8'))
        except Exception:
            return {}
    return (_load('cites.json'), _load('cited_by.json'))

def _evo_context(index_dir):
    """懒加载演化图，返回 (节点表, 出边表, 主干链映射)。缺失时返回空。"""
    if not index_dir:
        return ({}, {}, {})
    key = ('evo', index_dir)
    if key in _EVO_CACHE:
        return _EVO_CACHE[key]
    cdir = os.path.join(index_dir, 'citation')
    nodes, oute, chains = ({}, {}, {})
    try:
        g = json.load(open(os.path.join(cdir, 'graph.json'), encoding='utf-8'))
        nodes = {n['id']: n for n in g.get('nodes', [])}
        for e in g.get('edges', []):
            oute.setdefault(e['s'], []).append(e)
    except Exception:
        pass
    try:
        b = json.load(open(os.path.join(cdir, 'backbone.json'), encoding='utf-8'))
        for d, v in (b.get('directions') or {}).items():
            for i, c in enumerate(v.get('chains', []), 1):
                for nid in c.get('nodes', []):
                    chains.setdefault(nid, (d, v.get('name', d), i, c))
    except Exception:
        pass
    _EVO_CACHE[key] = (nodes, oute, chains)
    return _EVO_CACHE[key]
_EVO_CACHE = {}

def render_citations(citekey, cites, cited_by, index_dir=None):
    out_c = cites.get(citekey) or []
    in_c = cited_by.get(citekey) or []
    nodes, oute, chains = _evo_context(index_dir)
    if not out_c and (not in_c) and (citekey not in nodes):
        return '<!-- 未解析到库内引用关系 -->'
    lines = []
    if out_c:
        lines.append(f'**本文引用（库内 {len(out_c)} 篇）**：' + '、'.join((f'[[{k}]]' for k in out_c)))
    else:
        lines.append('**本文引用（库内）**：—')
    lines.append('')
    if in_c:
        lines.append(f'**被库内引用（{len(in_c)} 次）**：' + '、'.join((f'[[{k}]]' for k in in_c)))
    else:
        lines.append('**被库内引用**：—')
    if citekey in nodes:
        ups = oute.get(citekey, [])
        ext = [e for e in ups if nodes.get(e['d'], {}).get('kind') != 'library']
        typed = {}
        for e in ups:
            t = e.get('t') or 'cites'
            if t != 'cites':
                typed[t] = typed.get(t, 0) + 1
        downs = nodes[citekey].get('in_deg', 0)
        if ext or typed or downs:
            bits = [f'上游 {len(ups)} 篇（库外 {len(ext)}）']
            if typed.get('method'):
                bits.append(f"方法依赖 {typed['method']}")
            if typed.get('baseline'):
                bits.append(f"基线对比 {typed['baseline']}")
            if typed.get('lineage'):
                bits.append(f"同族 {typed['lineage']}")
            if downs:
                bits.append(f'下游 {downs} 篇')
            lines.append('')
            lines.append('**演化位置**：' + ' ｜ '.join(bits))
        ch = chains.get(citekey)
        if ch:
            d, name, i, c = ch
            lines.append(f"**所在主干链**：{d} {name} · 第 {i} 条（{c['years'][0]} → {c['years'][-1]}，共 {len(c['nodes'])} 步）")
    if index_dir:
        p = os.path.join(index_dir, 'references', citekey + '.json')
        if os.path.exists(p):
            try:
                n = len(json.load(open(p, encoding='utf-8')))
                lines.append('')
                lines.append(f'> 参考文献解析 {n} 条，匹配库内 {len(out_c)} 条。')
            except Exception:
                pass
    return '\n'.join(lines)

def default_figures_block(figs, citekey=''):
    """精读前：列图注清单并给 PNG 链接，不嵌图，避免卡片过重。"""
    if not figs:
        return '<!-- 未提取到图表（扫描件或图注格式特殊） -->'
    lines = ['<!-- 待挑选：精读后保留 2-4 张关键图 -->', '']
    for f in figs:
        kind = '图' if f['kind'] == 'figure' else '表'
        num = ''
        m = re.search('([0-9]+(?:[-–.][0-9]+)?)', f.get('label') or '')
        if m:
            num = m.group(1)
        link = ''
        if citekey and f.get('file'):
            link = f" · [[{f['file']}|{kind}{(' ' + num if num else '')}]]"
        lines.append(f"- `{f['id']}` p.{f['page']} {kind}{(' ' + num if num else '')} — {f['caption'][:110]}{link}")
    return '\n'.join(lines)

def build_ctx(rec, manual=None, notes='', figures=None, citations=''):
    """把一条文献记录摊平成模板需要的键值。"""
    manual = manual or {}
    tf = rec['tagfields']

    def tag_yaml(name):
        vals = sorted(set(tf.get(name) or []))
        return json.dumps(vals, ensure_ascii=False) if vals else '[]'

    def tag_inline(name):
        vals = sorted(set(tf.get(name) or []))
        return '、'.join(vals) if vals else '—'
    meta = []
    if rec['authors']:
        meta.append('、'.join(rec['authors'][:5]) + (' 等' if len(rec['authors']) > 5 else ''))
    if rec.get('year'):
        meta.append(str(rec['year']))
    if rec.get('venue'):
        meta.append(rec['venue'])
    if rec.get('doi'):
        meta.append(f"DOI: {rec['doi']}")
    if rec.get('pdf'):
        pdf_line = f"`{rec['pdf']}`（Zotero storage）" if rec.get('pdf_location') == 'zotero-storage' else f"[[{rec['pdf']}]]"
    else:
        pdf_line = '未找到 PDF'
    manual_defaults = {k: 'null' for k in MANUAL_FM_KEYS}
    manual_defaults['baselines'] = '[]'
    manual_defaults['metrics'] = '[]'
    ctx = {'citekey_yaml': yaml_scalar(rec['citekey']), 'zotero_key_yaml': yaml_scalar(rec.get('zotero_key')), 'title_yaml': yaml_scalar(rec['title']), 'authors_yaml': json.dumps(rec['authors'], ensure_ascii=False), 'author_links_yaml': json.dumps([f'[[{a}]]' for a in rec['authors']], ensure_ascii=False), 'year_yaml': str(rec['year']) if rec.get('year') else 'null', 'venue_yaml': yaml_scalar(rec.get('venue')), 'venue_type_yaml': yaml_scalar(VENUE_TYPE_LABEL.get(rec.get('item_type') or '', rec.get('item_type'))), 'doi_yaml': yaml_scalar(rec.get('doi')), 'directions_yaml': json.dumps([f'[[{DIR_NAME[d]}]]' for d in rec['directions']], ensure_ascii=False), 'metadata_source_yaml': yaml_scalar(rec['metadata_source']), 'item_type_yaml': yaml_scalar(rec.get('item_type')), 'pdf_yaml': yaml_scalar(rec.get('pdf')), 'pdf_location_yaml': yaml_scalar(rec.get('pdf_location')), 'abstract_available_yaml': 'true' if rec.get('abstract') else 'false', 'title': rec['title'] or '(无标题)', 'meta_line': ' · '.join(meta) if meta else '—', 'directions_line': '、'.join((f'[[{DIR_NAME[d]}]]' for d in rec['directions'])), 'pdf_line': pdf_line, 'tldr': rec.get('tldr') or '<!-- 待补 -->', 'abstract': rec['abstract'].strip() if rec.get('abstract') else '<!-- Zotero 无摘要 -->', 'authors_links': '- 作者：' + '、'.join((f'[[{a}]]' for a in rec['authors'][:8])) if rec['authors'] else '- 作者：—', 'direction_links': '\n'.join((f'- 方向：[[{DIR_NAME[d]}]]' for d in rec['directions'])), 'notes': notes if notes.strip() else '<!-- 手写区：重跑 build_cards.py 不会覆盖这里 -->', 'figures': figures if figures else default_figures_block([], rec['citekey']), 'citations': citations or '<!-- 未解析到库内引用关系 -->'}
    for f in ('task', 'robot', 'env', 'constraint', 'method', 'priority', 'reading_tier', 'review_status', 'relation', 'era'):
        ctx[f'{f}_yaml'] = tag_yaml(f)
    for f in ('task', 'robot', 'env', 'constraint', 'method'):
        ctx[f'{f}_inline'] = tag_inline(f)
    for k in MANUAL_FM_KEYS:
        ctx[f'{k}_yaml'] = manual.get(k, manual_defaults[k])
    return ctx
MANUAL_FM_KEYS = ('status', 'rating', 'read_date', 'venue_rank', 'code_url', 'dataset', 'platform', 'baselines', 'metrics')

def extract_persist(text):
    """取出手写区内容；兼容没有标记的旧卡片（取 ## 我的笔记 整节）。"""
    if PERSIST_START in text and PERSIST_END in text:
        s = text.index(PERSIST_START) + len(PERSIST_START)
        e = text.index(PERSIST_END)
        return text[s:e].strip('\n')
    m = re.search('^## 我的笔记\\s*\\n(.*?)(?=^## |\\Z)', text, re.S | re.M)
    if m:
        return m.group(1).strip('\n')
    return ''

def extract_figure_block(text):
    """若卡片已有嵌图的关键图表区，取回内容以便重建时保留。"""
    m = re.search('^## 8 关键图表\\s*\\n(.*?)(?=^## |\\Z)', text, re.S | re.M)
    if not m:
        return ''
    body = m.group(1).strip('\n')
    return body if '![[' in body else ''

def extract_manual_fm(text):
    """取出人工填写的 frontmatter 字段原始值（保持用户填的内容不被覆盖）。"""
    out = {}
    if not text.startswith('---'):
        return out
    end = text.find('\n---', 3)
    if end < 0:
        return out
    for line in text[3:end].split('\n'):
        if ':' not in line:
            continue
        k, v = line.split(':', 1)
        k = k.strip()
        if k not in MANUAL_FM_KEYS:
            continue
        v = v.split('#', 1)[0].strip() if not v.strip().startswith('[') else v.strip()
        if v in ('null', '', '[]'):
            continue
        out[k] = v
    return out

def first_sentence(text, limit=180):
    if not text:
        return ''
    t = re.sub('\\s+', ' ', text).strip()
    m = re.search('[。！？.!?]\\s', t + ' ')
    s = t[:m.end()].strip() if m else t
    if len(s) > limit:
        s = s[:limit].rstrip() + '…'
    return s

def card_rel_path(rec):
    if rec.get('card'):
        return rec['card']
    return os.path.join('_知识库/10_文献', DIR_NAME[rec['primary_direction']], rec['citekey'] + '.md').replace(os.sep, '/')

def write_papers_all(records, index_dir):
    rows = []
    for r in records:
        rows.append({'citekey': r['citekey'], 'zotero_key': r['zotero_key'], 'title': r['title'], 'authors': r['authors'], 'year': r['year'], 'venue': r['venue'], 'doi': r['doi'], 'abstract': r['abstract'], 'tldr': r['tldr'], 'directions': r['directions'], 'primary_direction': r['primary_direction'], 'tags': sorted([f'{k}:{v}' for k, vs in r['tagfields'].items() for v in vs] + r['other_tags']), 'metadata_source': r['metadata_source'], 'pdf': r['pdf'], 'pdf_exists': r['pdf_exists'], 'collections': r['collections'], 'tagfields': {k: sorted(set(v)) for k, v in r['tagfields'].items()}, 'card': card_rel_path(r)})
    with open(os.path.join(index_dir, 'papers_all.jsonl'), 'w', encoding='utf-8', newline='\n') as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')
    return len(rows)
