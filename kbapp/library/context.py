"""kbapp.library.context：表格与公式的上下文结构化（D05/D06）。

表格：保留标题、列头、单位、脚注与续表标记；
公式：提取变量解释与使用上下文。均为确定性提取，不生成新内容。
"""
from __future__ import annotations
import re

UNIT = re.compile(r'[\(\[]([^)\]%]{0,24}(?:%|mm|cm|km|m/s|ms|kg|N·m|N|J|W|°|s|m)[^)\]]{0,12})[\)\]]', re.I)
NOTE_LINE = re.compile(r'^\s*(?:[*†‡§]|[a-z][\)\].:]|\d{1,2}[\)\].:])\s*\S')
CONTINUED = re.compile(r'continued|续表|续上表|continued table', re.I)
SYMBOL = r'([A-Za-zα-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]{0,5})'
VERB = r'(?:is|denotes|represents|stands for|为|表示|是)'
MEANING = r'([^.;。；]{3,90})'
PRIMARY = re.compile(r'(?:where|其中|式中|这里)\s+' + SYMBOL + r'\s+' + VERB + r'\s+' + MEANING, re.I)
SECONDARY = re.compile(r'(?:[,，;；]\s*(?:and\s+)?|\band\s+|和\s*)' + SYMBOL + r'\s+' + VERB + r'\s+' + MEANING, re.I)
MEANING_CUT = re.compile(r'\s+(?:and|和)\s+[A-Za-zα-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]{0,5}\s+' + VERB + r'\s+', re.I)


def table_context(caption='', table_text='', context=''):
    """返回表格标题、列头、单位、脚注与续表标记。"""
    lines = [line.strip() for line in (table_text or '').splitlines() if line.strip()]
    header = lines[0] if lines else ''
    units = sorted({match.strip() for match in UNIT.findall(' '.join([caption or '', header, context or '']))})
    notes = [line.strip() for line in (context or '').splitlines() if NOTE_LINE.match(line)]
    return {'header': header, 'units': units, 'footnotes': notes[:5],
            'continued': bool(CONTINUED.search(caption or '') or CONTINUED.search(context or '')),
            'rows': max(0, len(lines) - 1)}


def formula_context(latex='', context=''):
    """返回公式变量解释与上下文是否足以理解。"""
    text = context or ''
    variables, seen = [], set()
    for pattern in (PRIMARY, SECONDARY):
        for match in pattern.finditer(text):
            symbol = match.group(1)
            if symbol in seen:
                continue
            seen.add(symbol)
            meaning = MEANING_CUT.split(match.group(2))[0].strip()
            variables.append({'symbol': symbol, 'meaning': meaning})
    return {'variables': variables[:8], 'has_explanation': bool(variables),
            'context_length': len(text.strip()), 'latex_present': bool((latex or '').strip())}
