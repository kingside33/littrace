"""kbapp.library.equivalence：来源内容等价组（C05）。

原生文本、OCR、旧切块与文档切块可能包含同一段内容。用确定性规范化文本键
建立等价组：同组来源互相可替代，检索去重与证据引用可指向同一原文。
"""
from __future__ import annotations
import re
import unicodedata
from kbapp.core.io import digest


def normalize_text(text):
    """NFKC + 行尾连字符合并 + 空白压缩；仅用于等价判断，不改写原文。"""
    value = text or ''
    if not value.isascii():
        value = unicodedata.normalize('NFKC', value)
    value = re.sub(r'-\s*\n\s*', '', value)
    value = re.sub(r'\s+', ' ', value)
    return value.strip().lower()


def text_key(text):
    return digest(normalize_text(text))[:16]


def annotate(sources):
    """为来源补充 content_group 与 equivalent_count；返回分组统计。"""
    groups = {}
    for source in sources:
        key = text_key(source.get('text', ''))
        source['content_group'] = key
        groups.setdefault(key, []).append(source)
    for key, members in groups.items():
        origins = sorted({str(m.get('origin') or m.get('kind') or '') for m in members})
        for member in members:
            member['equivalent_count'] = len(members)
            member['equivalent_origins'] = origins
    return {'groups': len(groups), 'duplicated_groups': sum(1 for m in groups.values() if len(m) > 1)}
