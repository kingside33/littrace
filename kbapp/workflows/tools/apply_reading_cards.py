"""kbapp.workflows.tools.apply_reading_cards：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import re
import sys
BASE = str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().base)
SECTIONS = [('problem', '1 问题与动机'), ('method', '2 方法'), ('experiment', '3 实验'), ('critique', '4 批判性评估'), ('relation', '5 与我的课题'), ('quotes', '6 关键摘录')]
SUMMARY_KEY = 'summary'
SUMMARY_SECTION = '速览'
SUMMARY_LINE_RE = re.compile('^\\*\\*一句话\\*\\*：.*$', re.M)
FIG_SECTION = '8 关键图表'

def load_figure_index(index, citekey):
    p = os.path.join(index, 'figures', citekey + '.json')
    if not os.path.exists(p):
        return {}
    try:
        return {f['id']: f for f in json.load(open(p, encoding='utf-8'))}
    except Exception:
        return {}

def render_figures(citekey, picks, fig_index):
    """picks: [{"id": "fig-03", "title": "...", "why": "..."}]"""
    blocks = []
    for item in picks:
        fid = item.get('id')
        meta = fig_index.get(fid)
        if not meta:
            continue
        kind_cn = '图' if meta.get('kind') == 'figure' else '表'
        m = re.search('([0-9]+(?:[-–.][0-9]+)?)', meta.get('label') or '')
        num = m.group(1) if m else ''
        title = item.get('title') or meta.get('caption', '')[:40]
        rel = f'_知识库/_attachments/figures/{citekey}/{fid}.png'
        head = f'{kind_cn} {num} · {title}'.strip(' ·') if num else f'{kind_cn} · {title}'
        blocks.append(f'### {head}')
        blocks.append('')
        blocks.append(f'![[{rel}]]')
        blocks.append('')
        blocks.append(f"> {meta.get('caption', '')}（p.{meta.get('page')}）")
        if item.get('why'):
            blocks.append('')
            blocks.append(f"**关键点**：{item['why']}")
        blocks.append('')
    return '\n'.join(blocks).strip()

def split_card(text):
    """拆成 (frontmatter+标题区, {section: body}) —— 只切二级标题"""
    lines = text.split('\n')
    head_end = 0
    for i, ln in enumerate(lines):
        if ln.startswith('## '):
            head_end = i
            break
    else:
        return (text, {})
    head = '\n'.join(lines[:head_end])
    body = {}
    cur = None
    buf = []
    order = []
    for ln in lines[head_end:]:
        m = re.match('^##\\s+(.+?)\\s*$', ln)
        if m:
            if cur is not None:
                body[cur] = '\n'.join(buf).strip('\n')
            cur = m.group(1).strip()
            order.append(cur)
            buf = []
        else:
            buf.append(ln)
    if cur is not None:
        body[cur] = '\n'.join(buf).strip('\n')
    return (head, body, order)

def render(head, body, order):
    out = [head.rstrip('\n')]
    for name in order:
        out.append('')
        out.append(f'## {name}')
        content = body.get(name, '').strip('\n')
        out.append('')
        if content:
            out.append(content)
    return '\n'.join(out).rstrip('\n') + '\n'

def overlay_text(text, rec, fig_index=None):
    """把一条精读记录叠加到卡片文本上，返回新文本；失败返回 None。"""
    parsed = split_card(text)
    if len(parsed) == 2:
        return None
    head, body, order = parsed
    ck = rec.get('citekey', '')
    fig_index = fig_index or {}
    summary = (rec.get(SUMMARY_KEY) or '').strip()
    if summary and SUMMARY_SECTION in body:
        sec_text = body[SUMMARY_SECTION]
        if SUMMARY_LINE_RE.search(sec_text):
            body[SUMMARY_SECTION] = SUMMARY_LINE_RE.sub(lambda m: f'**一句话**：{summary}', sec_text, count=1)
        else:
            body[SUMMARY_SECTION] = f'**一句话**：{summary}\n\n' + sec_text
    picks = rec.get('figures') or []
    if picks:
        block = render_figures(ck, picks, fig_index)
        if block:
            if FIG_SECTION not in order:
                order.append(FIG_SECTION)
            body[FIG_SECTION] = block
    for key, heading in SECTIONS:
        val = (rec.get(key) or '').strip()
        if not val:
            continue
        if heading not in order:
            order.append(heading)
        body[heading] = val
    return render(head, body, order)

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=BASE)
    ap.add_argument('--index', default=str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().active_index()))
    ap.add_argument('--file', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index)
    src = args.file or os.path.join(index, 'reading_cards.jsonl')
    if not os.path.exists(src):
        sys.exit(f'找不到输入文件：{src}')
    manifest = json.load(open(os.path.join(index, 'cards_manifest.json'), encoding='utf-8'))
    records = [json.loads(line) for line in open(src, encoding='utf-8') if line.strip()]
    applied, skipped = (0, [])
    for rec in records:
        ck = rec.get('citekey')
        info = manifest.get(ck)
        if not info:
            skipped.append((ck, 'citekey 不在 manifest 中'))
            continue
        path = os.path.join(base, info['path'])
        if not os.path.exists(path):
            skipped.append((ck, '卡片文件不存在'))
            continue
        text = open(path, encoding='utf-8').read()
        fig_index = load_figure_index(index, ck)
        new = overlay_text(text, rec, fig_index)
        if new is None:
            skipped.append((ck, '卡片缺少二级标题，跳过'))
            continue
        if args.dry_run:
            print(f"[dry] {ck} -> {info['path']}")
            applied += 1
            continue
        with open(path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(new)
        applied += 1
    print(json.dumps({'input': len(records), 'applied': applied, 'skipped': len(skipped)}, ensure_ascii=False))
    for ck, why in skipped[:10]:
        print(f'  - {ck}: {why}')
