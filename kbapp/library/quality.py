"""kbapp.library.quality：版面与解析质量检查（D01）。

检查双栏、页眉页脚、跨页段落与旋转页面，输出每页问题与整篇摘要，
供 D09 按质量与研究价值安排 OCR/复核。
"""
from __future__ import annotations
from collections import Counter


def _blocks(page):
    lines = []
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(span['text'] for span in line.get('spans', [])).strip()
            if text:
                lines.append(tuple(line['bbox']) + (text,))
    return lines


def _is_two_column(blocks, width):
    if len(blocks) < 4:
        return False
    left = [b for b in blocks if (b[0] + b[2]) / 2 < width * 0.45]
    right = [b for b in blocks if (b[0] + b[2]) / 2 > width * 0.55]
    middle = len(blocks) - len(left) - len(right)
    if len(left) < 2 or len(right) < 2 or middle > 1:
        return False
    left_edge = min(b[2] for b in left)
    right_edge = max(b[0] for b in right)
    return right_edge - left_edge > width * 0.05


def _edge_lines(page):
    lines = [line.strip() for line in (page.get_text() or '').splitlines() if line.strip()]
    return (lines[0] if lines else '', lines[-1] if lines else '')


def analyze(path, pages=None):
    """返回 {page: [问题], 'summary': {...}}；问题为 rotated/two_column/header_footer/cross_page。"""
    import pymupdf
    issues = {}
    edge = Counter()
    texts = {}
    with pymupdf.open(path) as doc:
        numbers = pages or list(range(1, len(doc) + 1))
        for number in numbers:
            if not 1 <= number <= len(doc):
                continue
            page = doc[number - 1]
            page_issues = []
            if page.rotation % 360:
                page_issues.append('rotated')
            if _is_two_column(_blocks(page), page.rect.width):
                page_issues.append('two_column')
            first, last = _edge_lines(page)
            if first:
                edge['first:' + first] += 1
            if last:
                edge['last:' + last] += 1
            texts[number] = page.get_text() or ''
            if page_issues:
                issues[number] = page_issues
        for number, text in texts.items():
            stripped = text.rstrip()
            following = texts.get(number + 1, '')
            if stripped and following:
                tail = stripped[-1]
                head = following.lstrip()[:1]
                if tail not in '.!?。！？:;' and head.islower():
                    issues.setdefault(number, []).append('cross_page')
    repeated = [key.split(':', 1)[1] for key, count in edge.items() if count >= max(2, len(texts) // 2)]
    for number in texts:
        first, last = _edge_lines_from(texts[number])
        if first in repeated or last in repeated:
            issues.setdefault(number, []).append('header_footer')
    summary = {kind: sum(kind in values for values in issues.values()) for kind in ('rotated', 'two_column', 'header_footer', 'cross_page')}
    summary['pages'] = len(texts)
    summary['clean_pages'] = len(texts) - len(issues)
    return {'pages': issues, 'summary': summary}


def _edge_lines_from(text):
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    return (lines[0] if lines else '', lines[-1] if lines else '')
